from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import delete, func, select

from app.db.base import session_factory
from app.db.models import Opportunity, OpportunityState, SamBootstrap, SyncRun
from app.errors import InitialSyncComplete, SamAPIError
from app.sam.client import SEARCH_URL, SamClient
from app.sam.initialization import initialize_catalog
from app.sam.sync_service import initial_sync_needed, last_success, run_sync, sync_status


def fresh(engine):
    with session_factory(engine).begin() as session:
        session.execute(delete(SamBootstrap))


def response(records):
    return httpx.Response(200, json={"totalRecords": len(records), "opportunitiesData": records})


@respx.mock
def test_initial_year_once_only_keeps_nonexpired_records(engine, settings, sample):
    fresh(engine)
    now = datetime.now(UTC)
    windows = []

    def handler(request):
        assert request.url.params["rdlfrom"] == now.strftime("%m/%d/%Y")
        windows.append((request.url.params["postedFrom"], request.url.params["postedTo"]))
        return response([sample, dict(sample, noticeId="expired", responseDeadLine="2001-01-01")])

    route = respx.get(SEARCH_URL).mock(side_effect=handler)
    initial = run_sync(settings, engine=engine, now=now)
    with session_factory(engine)() as session:
        run = session.get_one(SyncRun, initial)
        assert run.kind == "initial" and run.status == "success"
        assert run.window_from == now.date() - timedelta(days=364)
        assert run.window_to == now.date()
        assert session.get(Opportunity, "expired") is None
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert run.skipped_expired > 0
    assert len(windows) == 53
    assert not initial_sync_needed(engine)
    assert sync_status(engine)["initial_sync"]["completed_days"] == 365
    calls = route.call_count
    with pytest.raises(InitialSyncComplete):
        run_sync(settings, engine=engine, initial_only=True)
    assert route.call_count == calls
    respx.get(SEARCH_URL).mock(return_value=response([sample]))
    daily = run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        run = session.get_one(SyncRun, daily)
        assert run.kind == "incremental"
        assert run.window_from == now.date() - timedelta(days=1)


@respx.mock
def test_initial_failure_resumes_after_completed_chunks(engine, settings, sample):
    fresh(engine)
    route = respx.get(SEARCH_URL).mock(side_effect=[response([sample]), httpx.Response(403)])
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        bootstrap = session.get_one(SamBootstrap, 1)
        resume = bootstrap.next_date
        assert resume == bootstrap.window_to - timedelta(days=7)
        assert bootstrap.completed_at is None
        assert last_success(session) is None
        assert session.get(Opportunity, sample["noticeId"]) is not None
    windows = []

    def handler(request):
        windows.append(request.url.params["postedTo"])
        return response([sample])

    route.mock(side_effect=handler)
    run_sync(settings, engine=engine)
    assert windows[0] == resume.strftime("%m/%d/%Y")
    assert not initial_sync_needed(engine)


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
def test_initial_window_splits_instead_of_truncating(settings, sample):
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


def test_initial_only_worker_does_not_repeat_complete_load(engine, settings):
    initialize_catalog(settings, engine, Event())
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(SyncRun)) == 0


def test_server_launches_initial_load_in_background_only_once(settings, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.auto_initial_sync = True
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    command.upgrade(Config("alembic.ini"), "head")
    started, release = Event(), Event()
    calls = []

    def initialize(config, engine, stop_event):
        calls.append(True)
        started.set()
        assert release.wait(5)
        now = datetime.now(UTC)
        with session_factory(engine).begin() as session:
            session.add(
                SamBootstrap(
                    id=1,
                    window_from=now.date() - timedelta(days=364),
                    window_to=now.date(),
                    next_date=now.date() + timedelta(days=1),
                    snapshot_at=now,
                    completed_at=now,
                )
            )

    with patch("app.main.initialize_catalog", initialize):
        with TestClient(create_app(settings)) as client:
            assert started.wait(5)
            try:
                assert client.get("/healthz").status_code == 200
            finally:
                release.set()
        with TestClient(create_app(settings)) as client:
            assert client.get("/healthz").status_code == 200
    assert len(calls) == 1


def test_default_does_not_schedule_daily_calls(engine, settings):
    from app.scheduler.jobs import build_scheduler

    assert not settings.scheduled_sync_enabled
    assert build_scheduler(settings, engine).get_jobs() == []


@respx.mock
def test_cancelled_initial_load_keeps_resume_marker(engine, settings):
    fresh(engine)
    stop = Event()
    stop.set()
    with pytest.raises(SamAPIError) as error:
        run_sync(settings, engine=engine, stop_event=stop)
    assert error.value.reason == "cancelled"
    assert initial_sync_needed(engine)
    with session_factory(engine)() as session:
        state = session.get_one(SamBootstrap, 1)
        assert state.next_date == state.window_to


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
