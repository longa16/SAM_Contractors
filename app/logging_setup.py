import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.config import Settings

request_id: ContextVar[str] = ContextVar("request_id", default="system")


class JSONFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [value for value in secrets if value]

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event": record.getMessage(),
            "request_id": request_id.get(),
        }
        fields.update(getattr(record, "fields", {}))
        # Exception strings may contain a request URL; never include traceback text.
        if record.exc_info and record.exc_info[0]:
            fields["exception_type"] = record.exc_info[0].__name__
        text = json.dumps(fields, ensure_ascii=False, default=str)
        text = re.sub(r"(?i)(api_key|authorization)([=\" :]+)[^&\s\"]+", r"\1\2[REDACTED]", text)
        for value in self.secrets:
            text = text.replace(value, "[REDACTED]")
        return text


def setup_logging(settings: Settings) -> None:
    secrets = [
        value.get_secret_value()
        for value in (settings.sam_api_key, settings.groq_api_key)
        if value is not None
    ]
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter(secrets))
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)
    # HTTP libraries log query strings at INFO, including api_key.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
