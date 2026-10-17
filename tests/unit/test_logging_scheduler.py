import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.db.base import session_factory
from app.db.models import OpportunityState, SyncRun
from app.errors import InvalidTransition
from app.logging_setup import JSONFormatter
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.scheduler.jobs import build_scheduler, needs_catchup
from app.services.state import OpportunityStateService


def test_json_logging_redacts_keys_and_url():
    secret = "sentinel-key-must-not-appear"
    record = logging.LogRecord(
        "test", logging.ERROR, "", 1, f"Failure api_key={secret}&offset=0", (), None
    )
    record.fields = {"url": f"https://api.sam.gov?api_key={secret}&x=1"}
    serialized = JSONFormatter([secret]).format(record)
    payload = json.loads(serialized)
    assert secret not in serialized
    assert set(["ts", "level", "module", "event", "request_id"]) <= payload.keys()


def test_cron_utc_and_startup_catchup(engine, settings):
    scheduler = build_scheduler(settings, engine)
    trigger = scheduler.get_job("sam_sync").trigger
    now = datetime(2026, 10, 17, 7, 0, tzinfo=UTC)
    assert trigger.get_next_fire_time(None, now) == now.replace(hour=8)
    assert scheduler.get_job("sam_sync_catchup") is not None
    assert needs_catchup(engine, settings, datetime.now(UTC))
    with patch("app.scheduler.jobs.run_sync", side_effect=RuntimeError("private-url")):
        scheduler.get_job("sam_sync").func()
    real_now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        session.add(
            SyncRun(
                started_at=real_now,
                lease_expires_at=real_now,
                window_from=real_now.date(),
                window_to=real_now.date(),
                checkpoint_at=real_now,
                finished_at=real_now,
                status="success",
            )
        )
    assert not needs_catchup(engine, settings, real_now + timedelta(hours=7))
    assert build_scheduler(settings, engine).get_job("sam_sync_catchup") is None


def test_state_service_audits_valid_transitions_and_rejects_invalid(engine, sample):
    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        upsert(session, format_opp(sample, now))
        session.flush()
        service = OpportunityStateService(session)
        with pytest.raises(InvalidTransition):
            service.transition(sample["noticeId"], "bid", "user", "admin", now)
        service.transition(sample["noticeId"], "screening", "system", "system", now)
        with pytest.raises(InvalidTransition):
            service.transition(sample["noticeId"], "bid", "user", "subcontractor", now)
        with pytest.raises(InvalidTransition):
            service.transition(sample["noticeId"], "no-bid", "user", "admin", now)
        service.transition(sample["noticeId"], "bid", "user", "admin", now)
        assert session.get_one(OpportunityState, sample["noticeId"]).status == "bid"
        assert service.expire(sample["noticeId"], now)
        assert not service.expire(sample["noticeId"], now)
        with pytest.raises(InvalidTransition):
            service.transition("missing", "screening", "user", "admin", now)
