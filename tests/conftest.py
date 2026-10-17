import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from app.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base, make_engine


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Default tests must never access the network")

    monkeypatch.setattr(socket.socket, "connect", deny)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        sam_api_key=SecretStr("test-sam-key-NEVER-LOG"),
        sam_retry_wait_seconds=0,
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sample() -> dict[str, Any]:
    return {
        "noticeId": "test-1",
        "solicitationNumber": "TEST-2026",
        "title": "Office services",
        "type": "Solicitation",
        "active": "Yes",
        "postedDate": datetime.now(UTC).date().isoformat(),
        "responseDeadLine": "2099-01-01",
        "naicsCode": "238220",
        "typeOfSetAside": "SBA",
        "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=test-1",
        "additionalInfoLink": "None",
        "resourceLinks": [],
        "pointOfContact": [],
    }
