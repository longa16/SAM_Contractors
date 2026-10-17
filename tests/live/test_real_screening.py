"""Opt-in acceptance test. Real credentials and selected business inputs are mandatory."""

import json
import os
from pathlib import Path
from time import monotonic

import pytest
import yaml

from app.config import Settings
from app.db.base import Base, make_engine, session_factory
from app.db.models import Company
from app.llm.client import GroqClient
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.schemas.company import CompanyProfile
from app.services.screening import ScreeningService


@pytest.mark.live
def test_full_live_screening_below_120_seconds(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'live.db'}")
    paths = [os.getenv("LIVE_COMPANY_PROFILE"), os.getenv("LIVE_OPPORTUNITY_JSON")]
    if not settings.sam_api_key or not settings.groq_api_key or not all(paths):
        pytest.skip(
            "Requires SAM/Groq credentials, a real enterprise profile, "
            "and an eligible selected notice"
        )
    profile = CompanyProfile.model_validate(yaml.safe_load(Path(paths[0]).read_text()))
    opportunity = format_opp(json.loads(Path(paths[1]).read_text()))
    engine = make_engine(settings.database_url)
    try:
        Base.metadata.create_all(engine)
        with session_factory(engine).begin() as session:
            company = Company(name=profile.company.legal_name, profile=profile.model_dump())
            session.add(company)
            session.flush()
            company_id = company.id
            upsert(session, opportunity)
        started = monotonic()
        result = ScreeningService(engine, settings, GroqClient(settings)).run(
            company_id, opportunity.notice_id
        )
        assert result.stage == 2, (
            "Live screening did not complete; inspect the sanitized agent_run failure"
        )
        assert monotonic() - started < 120
    finally:
        engine.dispose()
