import logging
import re
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.schemas.sam import NormalizedOpportunity

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
VALID_TYPES = {"solicitation", "combined synopsis/solicitation", "combined synopsis-solicitation"}
SET_ASIDES = {
    "SBA": ("small_business", "Petite entreprise"),
    "SBP": ("small_business", "Petite entreprise"),
    "8A": ("eight_a", "8(a)"),
    "8AN": ("eight_a", "8(a)"),
    "HZC": ("hubzone", "HUBZone"),
    "HZS": ("hubzone", "HUBZone"),
    "SDVOSBC": ("sdvosb", "SDVOSB"),
    "SDVOSBS": ("sdvosb", "SDVOSB"),
    "WOSB": ("wosb", "WOSB"),
    "WOSBSS": ("wosb", "WOSB"),
    "EDWOSB": ("edwosb", "EDWOSB"),
    "EDWOSBSS": ("edwosb", "EDWOSB"),
}
STATE_CODES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY AS GU MP PR VI".split()
)


def nullable(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in {"", "none", "null"} else text


def parse_deadline(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = raw.strip()
        if len(value) == 10:
            return datetime.combine(
                date.fromisoformat(value), time(23, 59, 59), EASTERN
            ).astimezone(UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # For ambiguous fall-back hours use the later occurrence (fold=1).
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=EASTERN, fold=1)
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def normalize_set_aside(raw: Any) -> tuple[str | None, str, str]:
    code = (nullable(raw) or "").upper()
    if code in {"", "NONE"}:
        return None, "none", "Aucune restriction"
    if code not in SET_ASIDES:
        log.warning("unknown_set_aside")
        return code, "other", "Restriction inconnue — vérifier l’éligibilité"
    category, label = SET_ASIDES[code]
    return code, category, label


def extract_pop_state(raw: Any) -> str | None:
    if isinstance(raw, dict):
        state = raw.get("state")
        if isinstance(state, dict):
            candidate = str(state.get("code", "")).upper()
            return candidate if candidate in STATE_CODES else None
        return extract_pop_state(state)
    if isinstance(raw, str):
        # Avoid guessing a state from city names such as Washington.
        for candidate in reversed(re.findall(r"\b[A-Z]{2}\b", raw)):
            if candidate in STATE_CODES:
                return str(candidate)
    return None


def format_opp(raw: dict[str, Any], now: datetime | None = None) -> NormalizedOpportunity:
    now = now or datetime.now(UTC)
    code, category, label = normalize_set_aside(raw.get("typeOfSetAside"))
    office = raw.get("officeAddress") or ""
    if isinstance(office, dict):
        office = ", ".join(str(office.get(k) or "") for k in ("city", "state", "zipcode")).strip(
            ", "
        )
    pop = raw.get("placeOfPerformance")
    pop_state = extract_pop_state(pop) or extract_pop_state(office)
    links = raw.get("resourceLinks") or []
    if isinstance(links, str):
        links = [s.strip() for s in links.split(" | ") if s.strip()]
    links = [str(s) for s in links if str(s).startswith(("https://", "http://"))]
    posted = nullable(raw.get("postedDate"))
    if not posted:
        raise ValueError("Missing postedDate")
    posted_date = date.fromisoformat(posted[:10])
    poc = raw.get("pointOfContact") or []
    return NormalizedOpportunity(
        notice_id=nullable(raw.get("noticeId")) or "",
        solicitation_number=nullable(raw.get("solicitationNumber")) or "",
        title=str(raw.get("title") or ""),
        type=str(raw.get("type") or ""),
        description_url=nullable(raw.get("description")),
        point_of_contact=poc,
        additional_info_link=nullable(raw.get("additionalInfoLink")),
        agency_path=str(raw.get("fullParentPathName") or ""),
        agency_code=str(raw.get("fullParentPathCode") or ""),
        organization_type=str(raw.get("organizationType") or ""),
        office_address=str(office),
        naics_code=str(raw.get("naicsCode") or ""),
        classification_code=str(raw.get("classificationCode") or ""),
        set_aside_code=code,
        set_aside_category=category,
        set_aside_label=nullable(raw.get("typeOfSetAsideDescription")) or label,
        posted_date=posted_date,
        response_deadline_utc=parse_deadline(raw.get("responseDeadLine")),
        response_deadline_raw=nullable(raw.get("responseDeadLine")),
        active=str(raw.get("active", "")).strip().lower() in {"yes", "true", "1"},
        place_of_performance_raw=nullable(pop) if not isinstance(pop, dict) else str(pop),
        pop_state=pop_state,
        resource_links=links,
        raw_payload={k: v for k, v in raw.items() if k.lower() not in {"api_key", "apikey"}},
        synced_at=now,
    )
