from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.db.base import session_factory
from app.db.models import AuditLog, Opportunity, OpportunityState, SyncRun
from app.errors import ConfigurationError, SamAPIError, SyncBusy
from app.sam.client import SEARCH_URL, SamClient
from app.sam.sync_service import acquire, last_success, run_sync, sync_status


def reply(records, total=None):
    return httpx.Response(
        200,
        json={
            "totalRecords": len(records) if total is None else total,
            "opportunitiesData": records,
        },
    )


@respx.mock
def test_two_syncs_preserve_state(engine, settings, sample):
    route = respx.get(SEARCH_URL).mock(return_value=reply([sample]))
    first = run_sync(settings, engine=engine)
    with session_factory(engine).begin() as session:
        state = session.get_one(OpportunityState, sample["noticeId"])
        state.status, state.bid_decision_reason = "bid", "Human decision"
        session.get_one(Opportunity, sample["noticeId"]).description_text = "Cached description"
    route.mock(return_value=reply([sample]))
    second = run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert session.get_one(Opportunity, sample["noticeId"]).title == sample["title"]
        assert (
            session.get_one(Opportunity, sample["noticeId"]).description_text
            == "Cached description"
        )
        state = session.get_one(OpportunityState, sample["noticeId"])
        assert state.status == "bid" and state.bid_decision_reason == "Human decision"
        assert session.get_one(SyncRun, first).status == "success"
        assert session.get_one(SyncRun, second).updated == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 6
    assert route.call_count == 2
    sample["title"] = "Same-day amendment"
    route.mock(return_value=reply([sample]))
    run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        updated = session.get_one(Opportunity, sample["noticeId"])
        assert updated.title == "Same-day amendment"
        assert updated.description_text is None
        assert session.get_one(OpportunityState, sample["noticeId"]).status == "bid"


@respx.mock
def test_date_only_deadline_expires_without_deletion(engine, settings, sample):
    # Existing dossiers are retained and expired; already-expired new notices are not inserted.
    from app.repositories.opportunities import upsert
    from app.sam.normalize import format_opp

    with session_factory(engine).begin() as session:
        upsert(session, format_opp(sample))
    sample["responseDeadLine"] = "2001-01-01"
    respx.get(SEARCH_URL).mock(return_value=reply([sample]))
    run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert session.get_one(OpportunityState, sample["noticeId"]).status == "expired"
        assert session.get(Opportunity, sample["noticeId"]) is not None
        assert session.scalar(select(AuditLog).where(AuditLog.action == "opportunity.expired"))


@respx.mock
def test_page_failure_does_not_advance_checkpoint_or_write_partial_data(engine, settings, sample):
    respx.get(SEARCH_URL).mock(return_value=reply([sample]))
    run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        previous = last_success(session)
    settings.sam_page_size = 1
    respx.get(SEARCH_URL).mock(
        side_effect=[
            reply([dict(sample, noticeId="new")], total=2),
            httpx.ConnectError("secret URL"),
            httpx.ConnectError("secret URL"),
            httpx.ConnectError("secret URL"),
        ]
    )
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert last_success(session) == previous
        assert session.get(Opportunity, "new") is None
        run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
        assert run.status == "failed" and run.error == "sam_api_error"


@pytest.mark.parametrize("total", [4200, 5200])
@respx.mock
def test_pagination_not_truncated_at_five_pages(settings, sample, total):
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        first_record = offset * settings.sam_page_size
        records = [
            dict(sample, noticeId=f"{i}")
            for i in range(first_record, min(total, first_record + 1000))
        ]
        return reply(records, total)

    respx.get(SEARCH_URL).mock(side_effect=handler)
    client = SamClient(settings)
    try:
        assert len(client.fetch(datetime.now(UTC).date(), datetime.now(UTC).date())) == total
    finally:
        client.close()
    assert offsets == list(range((total + 999) // 1000))


@respx.mock
def test_cap_raises_instead_of_marking_success(engine, settings, sample, caplog):
    settings.sam_page_size, settings.sam_max_pages = 1, 1
    respx.get(SEARCH_URL).mock(return_value=reply([sample], 2))
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    assert "sam_page_limit_reached" in caplog.text
    with session_factory(engine)() as session:
        assert last_success(session) is None
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 0


@pytest.mark.parametrize("status", [401, 403, 429, 500])
@respx.mock
def test_api_errors_sanitized(engine, settings, status):
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(SamAPIError) as caught:
        run_sync(settings, engine=engine)
    assert settings.sam_api_key.get_secret_value() not in str(caught.value)
    assert route.call_count == (3 if status in {429, 500} else 1)


@respx.mock
def test_invalid_or_incomplete_response_fails(engine, settings):
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"error": "bad"}))
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    settings.sam_page_size = 1
    respx.get(SEARCH_URL).mock(return_value=reply([], total=2))
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)


def test_missing_key_is_explicit_and_failed(engine, settings):
    settings.sam_api_key = None
    with pytest.raises(ConfigurationError):
        run_sync(settings, engine=engine)
    assert sync_status(engine)["last_run_status"] == "failed"


def test_lock_rejects_overlap_and_recovers_stale_lease(engine, settings):
    factory = session_factory(engine)
    now = datetime.now(UTC)
    run_id = acquire(factory, settings, now)
    with pytest.raises(SyncBusy):
        acquire(factory, settings, now)
    replacement = acquire(factory, settings, now + timedelta(minutes=31))
    assert replacement != run_id
    with factory() as session:
        assert session.get_one(SyncRun, run_id).error == "lease_expired"


def test_atomic_lock_under_concurrency(engine, settings):
    def attempt(_):
        try:
            return acquire(session_factory(engine), settings, datetime.now(UTC))
        except SyncBusy:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(value is not None for value in results) == 1


@respx.mock
def test_filters_older_and_inactive_and_wrong_type(engine, settings, sample):
    records = [
        sample,
        dict(sample, active="No", noticeId="inactive"),
        dict(sample, type="Award Notice", noticeId="award"),
    ]
    respx.get(SEARCH_URL).mock(return_value=reply(records))
    run_sync(settings, engine=engine)
    old = dict(sample, postedDate="2000-01-01", title="old")
    respx.get(SEARCH_URL).mock(return_value=reply([old]))
    run_id = run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert session.get_one(Opportunity, sample["noticeId"]).title == sample["title"]
        assert session.get_one(SyncRun, run_id).skipped_duplicate == 1


@respx.mock
def test_validation_failure_rolls_back(engine, settings, sample):
    respx.get(SEARCH_URL).mock(return_value=reply([sample, dict(sample, noticeId="")]))
    with pytest.raises(ValueError):
        run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 0


@respx.mock
def test_date_windows_split_with_overlap_in_next_run(engine, settings, sample):
    settings.sam_window_days = 1
    windows = []

    def handler(request):
        windows.append((request.url.params["postedFrom"], request.url.params["postedTo"]))
        return reply([sample])

    respx.get(SEARCH_URL).mock(side_effect=handler)
    run_sync(settings, engine=engine)
    assert len(windows) == 3
    assert all(start == end for start, end in windows)
    with session_factory(engine)() as session:
        previous = last_success(session)
    windows.clear()
    run_id = run_sync(settings, engine=engine)
    with session_factory(engine)() as session:
        assert session.get_one(SyncRun, run_id).window_from == (previous - timedelta(days=1)).date()


def test_fencing_prevents_expired_worker_writes(engine, settings, sample):
    fake = Mock(spec=SamClient)

    def fetch(start, end):
        with session_factory(engine).begin() as session:
            run = session.scalar(select(SyncRun).where(SyncRun.status == "running"))
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return [sample]

    fake.fetch.side_effect = fetch
    with pytest.raises(SyncBusy):
        run_sync(settings, engine=engine, client=fake)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 0


@pytest.mark.parametrize(
    ("status", "reason", "attempts"),
    [
        (401, "http_rejected", None),
        (403, "http_rejected", None),
        (429, "rate_limited", 3),
        (503, "upstream_unavailable", 3),
    ],
)
@respx.mock
def test_failed_sync_retains_safe_actionable_diagnostics(
    engine, settings, caplog, status, reason, attempts
):
    import json
    import logging

    caplog.set_level(logging.ERROR, logger="app.sam.sync_service")
    secret = settings.sam_api_key.get_secret_value()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(status, text="private provider body " + secret)
    )
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    result = sync_status(engine)
    assert result["last_run_status"] == "failed"
    assert result["last_run_error"]["reason"] == reason
    assert result["last_run_error"]["http_status"] == status
    assert result["last_run_error"]["attempts"] == attempts
    fields = next(record.fields for record in caplog.records if record.message == "sync_failed")
    assert fields["http_status"] == status and fields["reason"] == reason
    assert secret not in json.dumps(fields)
    assert "private provider body" not in json.dumps(result)
    with session_factory(engine)() as session:
        assert last_success(session) is None


@respx.mock
def test_network_failure_has_diagnostics_without_request_url(engine, settings):
    respx.get(SEARCH_URL).mock(
        side_effect=httpx.ConnectError("https://private?api_key=DO_NOT_SHOW")
    )
    with pytest.raises(SamAPIError):
        run_sync(settings, engine=engine)
    result = sync_status(engine)
    assert result["last_run_error"]["reason"] == "network_error"
    assert result["last_run_error"]["http_status"] is None
    assert "DO_NOT_SHOW" not in str(result)
