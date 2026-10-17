import re

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.errors import SamAPIError


def fetch_description(url: str | None, settings: Settings) -> str:
    if not url or not settings.sam_api_key:
        raise SamAPIError("Description SAM indisponible.")
    parsed = httpx.URL(url)
    # Never attach the SAM credential to an arbitrary URL supplied in an opportunity.
    if parsed.scheme != "https" or parsed.host != "api.sam.gov" or parsed.userinfo:
        raise SamAPIError("Destination de description non autorisée.")
    try:
        with httpx.Client(timeout=settings.sam_timeout_seconds, follow_redirects=False) as client:
            response = client.get(
                str(parsed.copy_set_param("api_key", settings.sam_api_key.get_secret_value())),
                headers={"User-Agent": settings.sam_user_agent},
            )
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise SamAPIError("Description reportée : accès, quota ou taille non conforme.")
        content = response.text
        if response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
            content = str(payload.get("description", "")) if isinstance(payload, dict) else ""
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        if not text:
            raise SamAPIError("Description vide ; analyse reportée.")
        # Contact details are not relevant to screening and must not enter model prompts.
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email retiré]", text)
        text = re.sub(
            r"(?<!\w)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)",
            "[téléphone retiré]",
            text,
        )
        return text[: settings.max_desc_chars]
    except (httpx.HTTPError, ValueError):
        raise SamAPIError("Description SAM inaccessible ; analyse reportée.") from None
