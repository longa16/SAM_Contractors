import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select

from app.db.base import session_factory
from app.db.models import AgentRun, Company, CompanyCase, LLMUsage, Opportunity, ScreeningResult
from app.errors import Conflict
from app.llm.client import Completion
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.services.screening import ScreeningService


def test_retry_repair_success_and_idempotency_then_force(
    engine, settings, company, seeded_opp, fake_llm
):
    fake_llm.responses = ["invalid", fake_llm.complete("", "").text]
    fake_llm.calls.clear()
    service = ScreeningService(engine, settings, fake_llm)
    result = service.run(company.id, seeded_opp.notice_id)
    assert result.stage == 2 and len(fake_llm.calls) == 2
    assert service.run(company.id, seeded_opp.notice_id) == result
    assert len(fake_llm.calls) == 2
    settings.llm_daily_token_budget = 100000000
    service.run(company.id, seeded_opp.notice_id, force=True)
    assert len(fake_llm.calls) == 3
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(ScreeningResult)) == 4
        assert (
            session.scalar(
                select(AgentRun).where(AgentRun.agent_name == "opportunity_analyst")
            ).status
            == "success"
        )


def test_two_invalid_outputs_leave_stage1_visible_and_failed_run(
    engine, settings, company, seeded_opp, fake_llm, caplog
):
    fake_llm.responses = ["bad", "still bad"]
    result = ScreeningService(engine, settings, fake_llm).run(company.id, seeded_opp.notice_id)
    assert result.stage == 1
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase))
        assert case.screening_stage == "stage1" and case.analysis_status == "failed"
        run = session.scalar(select(AgentRun).where(AgentRun.agent_name == "opportunity_analyst"))
        assert run.status == "failed" and run.error == "llm_output_error"
    assert seeded_opp.description_text not in caplog.text


@pytest.mark.parametrize("kind", ["hard", "threshold", "cap", "budget"])
def test_llm_not_called_when_ineligible_or_over_limits(
    engine, settings, company, seeded_opp, fake_llm, kind
):
    with session_factory(engine).begin() as session:
        if kind == "hard":
            opp = session.get_one(Opportunity, seeded_opp.notice_id)
            opp.set_aside_category, opp.set_aside_code = "eight_a", "8A"
        if kind == "threshold":
            profile = dict(company.profile)
            profile["screening"] = {**profile["screening"], "stage1_threshold": 100}
            session.get_one(Company, company.id).profile = profile
        if kind == "cap":
            settings.screening_stage2_daily_cap = 0
        if kind == "budget":
            settings.llm_daily_token_budget = 0
    assert (
        ScreeningService(engine, settings, fake_llm).run(company.id, seeded_opp.notice_id).stage
        == 1
    )
    assert not fake_llm.calls


def test_quota_is_shared_across_companies_and_ranked_batch(
    engine, settings, company, seeded_opp, fake_llm, sample, profile
):
    settings.screening_stage2_daily_cap = 1
    settings.llm_daily_token_budget = 100000000
    with session_factory(engine).begin() as session:
        session.add(Company(name="Second", profile=profile.model_dump()))
        upsert(session, format_opp(dict(sample, noticeId="other", naicsCode="111111")))
    service = ScreeningService(engine, settings, fake_llm)
    assert service.batch() == 4
    assert len(fake_llm.calls) == 1
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(LLMUsage)) == 1


def test_company_profile_is_required(engine, settings, company, seeded_opp, fake_llm):
    with session_factory(engine).begin() as session:
        session.get_one(Company, company.id).profile = None
    with pytest.raises(Conflict):
        ScreeningService(engine, settings, fake_llm).run(company.id, seeded_opp.notice_id)


def test_description_failure_defers_without_calling_model(
    engine, settings, company, seeded_opp, fake_llm
):
    with session_factory(engine).begin() as session:
        opp = session.get_one(Opportunity, seeded_opp.notice_id)
        opp.description_text, opp.description_url = None, "http://127.0.0.1/private"
    result = ScreeningService(engine, settings, fake_llm).run(company.id, seeded_opp.notice_id)
    assert result.stage == 1 and not fake_llm.calls
    with session_factory(engine)() as session:
        assert session.scalar(select(CompanyCase)).analysis_status == "needs_description"


def test_hallucinated_quote_is_rejected(engine, settings, company, seeded_opp, fake_llm):
    payload = json.loads(fake_llm.complete("", "").text)
    fake_llm.calls.clear()
    payload["positive_factors"] = [
        {"text_fr": "Exigence vérifiée", "evidence": "invented quotation"}
    ]
    fake_llm.responses = [json.dumps(payload)]
    result = ScreeningService(engine, settings, fake_llm).run(company.id, seeded_opp.notice_id)
    assert result.stage == 1
    with session_factory(engine)() as session:
        assert session.scalar(select(CompanyCase)).analysis_status == "failed"


def test_force_does_not_duplicate_concurrent_model_call(engine, settings, company, seeded_opp):
    entered, release = Event(), Event()

    class SlowLLM:
        def complete(self, *args, **kwargs):
            entered.set()
            assert release.wait(10)
            return Completion("invalid")

    service = ScreeningService(engine, settings, SlowLLM())
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(service.run, company.id, seeded_opp.notice_id)
        assert entered.wait(10)
        try:
            with pytest.raises(Conflict):
                service.run(company.id, seeded_opp.notice_id, force=True)
        finally:
            release.set()
        assert running.result().stage == 1
