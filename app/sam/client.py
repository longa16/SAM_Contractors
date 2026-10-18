import logging
from collections.abc import Callable
from datetime import date, timedelta
from threading import Event
from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.errors import ConfigurationError, SamAPIError
from app.schemas.sam import SamPage

log = logging.getLogger(__name__)
SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"


class RetryablePageError(SamAPIError):
    pass


class SamClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self.settings = settings
        self.progress: Callable[[], None] | None = None
        self.stop_event: Event | None = None
        self.http = http or httpx.Client(
            timeout=settings.sam_timeout_seconds, follow_redirects=False
        )
        self.owns_http = http is None

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def _page(
        self,
        start: date,
        end: date,
        offset: int,
        *,
        min_deadline: date | None = None,
        notice_id: str | None = None,
    ) -> SamPage:
        if self.stop_event is not None and self.stop_event.is_set():
            raise SamAPIError(
                "Synchronisation interrompue ; progression conservée.", reason="cancelled"
            )
        if not self.settings.sam_api_key:
            raise ConfigurationError("SAM_API_KEY doit être configurée dans l’environnement.")
        params = {
            "api_key": self.settings.sam_api_key.get_secret_value(),
            "postedFrom": start.strftime("%m/%d/%Y"),
            "postedTo": end.strftime("%m/%d/%Y"),
            "limit": str(self.settings.sam_page_size),
            "offset": str(offset),
            "ptype": "o,k",
        }
        if min_deadline is not None:
            params["rdlfrom"] = min_deadline.strftime("%m/%d/%Y")
        if notice_id is not None:
            params.pop("ptype", None)
            params["noticeid"] = notice_id
        try:
            response = self.http.get(
                SEARCH_URL, params=params, headers={"User-Agent": self.settings.sam_user_agent}
            )
        except httpx.TransportError:
            raise RetryablePageError("SAM transport failure", reason="network_error") from None
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryablePageError(
                "SAM temporarily unavailable",
                reason="rate_limited" if response.status_code == 429 else "upstream_unavailable",
                http_status=response.status_code,
            )
        if response.status_code != 200:
            raise SamAPIError(
                f"SAM.gov a refusé la requête (HTTP {response.status_code}).",
                reason="http_rejected",
                http_status=response.status_code,
            )
        try:
            page = SamPage.model_validate(response.json())
            if self.progress:
                self.progress()
            return page
        except (ValueError, ValidationError):
            raise SamAPIError("Réponse SAM.gov invalide.", reason="invalid_response") from None

    def fetch(
        self,
        start: date,
        end: date,
        *,
        min_deadline: date | None = None,
        split_large: bool = False,
        notice_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        current = start
        while current <= end:
            window_end = min(end, current + timedelta(days=self.settings.sam_window_days - 1))
            try:
                records.extend(
                    self._window(
                        current, window_end, min_deadline=min_deadline, notice_id=notice_id
                    )
                )
            except SamAPIError as error:
                if not split_large or error.reason != "page_limit" or current == window_end:
                    raise
                midpoint = current + (window_end - current) // 2
                records.extend(
                    self.fetch(
                        current,
                        midpoint,
                        min_deadline=min_deadline,
                        split_large=True,
                        notice_id=notice_id,
                    )
                )
                records.extend(
                    self.fetch(
                        midpoint + timedelta(days=1),
                        window_end,
                        min_deadline=min_deadline,
                        split_large=True,
                        notice_id=notice_id,
                    )
                )
            current = window_end + timedelta(days=1)
        return records

    def fetch_notice(self, notice_id: str, posted: date, end: date) -> dict[str, Any] | None:
        records = self.fetch(posted, end, notice_id=notice_id)
        matching = [record for record in records if str(record.get("noticeId")) == notice_id]
        return max(matching, key=lambda record: str(record.get("postedDate", "")), default=None)

    def _window(
        self,
        start: date,
        end: date,
        *,
        min_deadline: date | None = None,
        notice_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset = 0
        for _ in range(self.settings.sam_max_pages):
            try:
                page = Retrying(
                    stop=stop_after_attempt(self.settings.sam_retry_attempts),
                    wait=wait_exponential(multiplier=self.settings.sam_retry_wait_seconds, max=30),
                    retry=retry_if_exception_type(RetryablePageError),
                    reraise=True,
                )(self._page, start, end, offset, min_deadline=min_deadline, notice_id=notice_id)
            except RetryablePageError as error:
                raise SamAPIError(
                    "Échec SAM.gov après les tentatives de récupération.",
                    reason=error.reason,
                    http_status=error.http_status,
                    attempts=self.settings.sam_retry_attempts,
                ) from None
            if (
                offset == 0
                and page.totalRecords > self.settings.sam_max_pages * self.settings.sam_page_size
            ):
                log.warning("sam_page_limit_reached")
                raise SamAPIError("Fenêtre trop volumineuse.", reason="page_limit")
            records.extend(page.opportunitiesData)
            offset += 1
            if offset * self.settings.sam_page_size >= page.totalRecords:
                return records
            if not page.opportunitiesData:
                raise SamAPIError(
                    "Pagination SAM.gov incomplète : page vide avant la fin.",
                    reason="incomplete_page",
                )
        log.warning("sam_page_limit_reached")
        raise SamAPIError(
            "Plafond de pagination atteint ; fenêtre non validée.", reason="page_limit"
        )
