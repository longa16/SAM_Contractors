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
def no_real_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live"):
        return

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


@pytest.fixture
def profile():
    from app.schemas.company import CompanyProfile

    return CompanyProfile.model_validate(
        {
            "company": {
                "legal_name": "Test enterprise",
                "small_business": True,
                "certifications": [],
            },
            "targeting": {
                "naics_primary": ["238220"],
                "service_states": ["VA"],
                "contract_value_max_usd": 5000000,
                "max_bonding_usd": 100000,
            },
        }
    )


@pytest.fixture
def company(engine, profile):
    from app.db.base import session_factory
    from app.db.models import Company

    with session_factory(engine).begin() as session:
        obj = Company(name="Test enterprise", profile=profile.model_dump())
        session.add(obj)
        session.flush()
        return obj


@pytest.fixture
def fake_llm():
    from app.llm.client import Completion
    from app.schemas.screening import ScreeningLLMOutput

    class FakeLLM:
        def __init__(self):
            self.calls = []
            self.responses = []

        def complete(self, system, user, **kwargs):
            self.calls.append((system, user))
            if self.responses:
                item = self.responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return Completion(item, 10, 5)
            result = ScreeningLLMOutput(
                required_licenses=None,
                required_insurance=None,
                bonding_required_usd=None,
                subcontracting_constraints=None,
                estimated_value_usd=None,
                scope_complexity="low",
                risks=[],
                positive_factors=[],
                blocking_factors=[],
                capability_keywords_matched=[],
            )
            return Completion(result.model_dump_json(), 10, 5)

    return FakeLLM()


@pytest.fixture
def seeded_opp(engine, sample):
    from app.db.base import session_factory
    from app.db.models import Opportunity
    from app.repositories.opportunities import upsert
    from app.sam.normalize import format_opp

    with session_factory(engine).begin() as session:
        upsert(session, format_opp(sample))
        session.flush()
        opp = session.get_one(Opportunity, sample["noticeId"])
        opp.description_text = "Licensed work is required. Performance bond of $50,000."
        return opp
