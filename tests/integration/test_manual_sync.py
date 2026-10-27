from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.db.base import session_factory
from app.db.models import Opportunity, OpportunityState, SyncRun
from app.errors import SamAPIError
from app.sam.client import SEARCH_URL, SamClient
from app.sam.sync_service import last_success, run_sync, sync_status


def response(records):
    return httpx.Response(200, json={"totalRecords": len(records), "opportunitiesData": records})


@respx.mock
def test_first_manual_sync_only_requests_today_and_never_bootstraps(engine, settings, sample):
    now = datetime.now(UTC)
    windows = []

    def handler(request):
        windows.append((request.url.params["postedFrom"], request.url.params["postedTo"]))
        return response([sample, dict(sample, noticeId="expired", responseDeadLine="2001-01-01")])

    respx.get(SEARCH_URL).mock(side_effect=handler)
    run_id = run_sync(settings, engine=engine, now=now)
    assert windows == [(now.strftime("%m/%d/%Y"), now.strftime("%m/%d/%Y"))]
    with session_factory(engine)() as session:
        run = session.get_one(SyncRun, run_id)
        assert run.kind == "incremental" and run.status == "success"
        assert run.window_from == run.window_to == now.date()
        assert run.skipped_expired == 1
        assert session.get(Opportunity, "expired") is None
    assert "initial_sync" not in sync_status(engine)


@respx.mock
def test_daily_refresh_updates_old_notice_with_unchanged_posting_date(engine, settings, sample):
    from app.repositories.opportunities import upsert
    from app.sam.normalize import format_opp

    now = datetime.now(UTC)
    old = dict(sample, postedDate=(now - timedelta(days=100)).date().isoformat())
    with session_factory(engine).begin() as session:
        upsert(session, format_opp(old, now - timedelta(days=2)))
        session.get_one(OpportunityState, sample["noticeId"]).status = "bid"
    requests = []

    def handler(request):
        requests.append(dict(request.url.params))
        if request.url.params.get("noticeid") == sample["noticeId"]:
            return response([dict(old, title="Corrected scope", responseDeadLine="2099-06-01")])
        return response([])

    respx.get(SEARCH_URL).mock(side_effect=handler)
    run_sync(settings, engine=engine, now=now)
    with session_factory(engine)() as session:
        opp = session.get_one(Opportunity, sample["noticeId"])
        assert opp.title == "Corrected scope" and opp.posted_date == now.date() - timedelta(
            days=100
        )
        assert session.get_one(OpportunityState, sample["noticeId"]).status == "bid"
    assert any(r.get("noticeid") == sample["noticeId"] for r in requests)
    # Already checked today: the second manual run does not recheck every old notice again.
    requests.clear()
    run_sync(settings, engine=engine, now=now)
    assert not any("noticeid" in request for request in requests)


@respx.mock
def test_quota_during_old_notice_refresh_does_not_lose_new_records(engine, settings, sample):
    from app.repositories.opportunities import upsert
    from app.sam.normalize import format_opp

    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        upsert(
            session,
            format_opp(
                dict(sample, noticeId="old", postedDate="2026-01-01"), now - timedelta(days=2)
            ),
        )

    def handler(request):
        return httpx.Response(403) if "noticeid" in request.url.params else response([sample])

    respx.get(SEARCH_URL).mock(side_effect=handler)
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine, now=now)
    with session_factory(engine)() as session:
        assert session.get(Opportunity, sample["noticeId"]) is not None
        assert last_success(session) is None
        assert session.get_one(Opportunity, "old").last_checked_at < now - timedelta(days=1)


@respx.mock
def test_search_window_splits_instead_of_truncating(settings, sample):
    settings.sam_max_pages = 1
    settings.sam_page_size = 1
    day = datetime.now(UTC).date()

    def handler(request):
        start, end = request.url.params["postedFrom"], request.url.params["postedTo"]
        return httpx.Response(
            200, json={"totalRecords": 2 if start != end else 1, "opportunitiesData": [sample]}
        )

    respx.get(SEARCH_URL).mock(side_effect=handler)
    client = SamClient(settings)
    try:
        assert len(client.fetch(day - timedelta(days=1), day, split_large=True)) == 2
    finally:
        client.close()


@respx.mock
def test_server_restart_never_calls_sam(settings, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from app.db.base import make_engine
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    command.upgrade(Config("alembic.ini"), "head")
    route = respx.get(SEARCH_URL).mock(return_value=response([]))
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get("/readyz").status_code == 200
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(SyncRun)) == 0
    engine.dispose()
    assert route.call_count == 0


def test_default_does_not_schedule_daily_calls(engine, settings):
    from app.scheduler.jobs import build_scheduler

    assert not settings.scheduled_sync_enabled
    assert build_scheduler(settings, engine).get_jobs() == []


@respx.mock
def test_cancelled_manual_sync_does_not_advance_checkpoint(engine, settings):
    stop = Event()
    stop.set()
    with pytest.raises(SamAPIError) as error:
        run_sync(settings, engine=engine, stop_event=stop)
    assert error.value.reason == "cancelled"
    with session_factory(engine)() as session:
        assert last_success(session) is None


@respx.mock
def test_old_notice_changed_to_award_is_not_left_active(engine, settings, sample):
    from app.repositories.opportunities import upsert
    from app.sam.normalize import format_opp

    now = datetime.now(UTC)
    old = dict(sample, postedDate=(now - timedelta(days=100)).date().isoformat())
    with session_factory(engine).begin() as session:
        upsert(session, format_opp(old, now - timedelta(days=2)))

    def handler(request):
        if "noticeid" in request.url.params:
            assert "ptype" not in request.url.params
            return response([dict(old, type="Award Notice")])
        return response([])

    respx.get(SEARCH_URL).mock(side_effect=handler)
    run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert not session.get_one(Opportunity, sample["noticeId"]).active
