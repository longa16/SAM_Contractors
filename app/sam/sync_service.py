import logging
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any

from sqlalchemy import Engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import make_engine, session_factory
from app.db.models import AuditLog, CompanyCase, Opportunity, SamBootstrap, SyncRun
from app.errors import DomainError, InitialSyncComplete, SamAPIError, SyncBusy
from app.repositories.opportunities import upsert
from app.sam.client import SamClient
from app.sam.normalize import VALID_TYPES, format_opp
from app.services.audit import audit
from app.services.cases import CompanyStateService
from app.services.state import OpportunityStateService

log = logging.getLogger(__name__)


def last_success(session: Session) -> datetime | None:
    return session.scalar(
        select(SyncRun.checkpoint_at)
        .where(SyncRun.status == "success")
        .order_by(SyncRun.checkpoint_at.desc())
        .limit(1)
    )


def last_checkpoint(session: Session) -> datetime | None:
    return session.scalar(
        select(SyncRun.checkpoint_at)
        .where(SyncRun.status.in_(["success", "imported"]), SyncRun.checkpoint_at.is_not(None))
        .order_by(SyncRun.checkpoint_at.desc())
        .limit(1)
    )


def acquire(
    factory: sessionmaker[Session], settings: Settings, now: datetime, *, initial_only: bool = False
) -> int:
    try:
        with factory.begin() as session:
            stale = session.scalars(
                select(SyncRun).where(SyncRun.status == "running", SyncRun.lease_expires_at <= now)
            ).all()
            for run in stale:
                run.status, run.error, run.finished_at = "failed", "lease_expired", now
                audit(session, "sync.lease_expired", "sync_run", str(run.id))
            session.flush()
            bootstrap = session.get(SamBootstrap, 1)
            if bootstrap is None:
                bootstrap = SamBootstrap(
                    id=1,
                    window_from=now.date() - timedelta(days=364),
                    window_to=now.date(),
                    next_date=now.date(),
                    snapshot_at=now,
                )
                session.add(bootstrap)
            initial = bootstrap.completed_at is None
            if initial_only and not initial:
                raise InitialSyncComplete()
            previous = last_checkpoint(session) or now - timedelta(days=1)
            run = SyncRun(
                started_at=now,
                lease_expires_at=now + timedelta(minutes=settings.sync_lock_minutes),
                kind="initial" if initial else "incremental",
                window_from=bootstrap.window_from
                if initial
                else (previous - timedelta(days=1)).date(),
                window_to=bootstrap.next_date if initial else now.date(),
                status="running",
            )
            session.add(run)
            session.flush()
            audit(session, "sync.started", "sync_run", str(run.id))
            return run.id
    except IntegrityError:
        raise SyncBusy("Une synchronisation est déjà en cours.") from None


def initial_sync_needed(engine: Engine) -> bool:
    with session_factory(engine)() as session:
        bootstrap = session.get(SamBootstrap, 1)
        return bootstrap is None or bootstrap.completed_at is None


def _fence(session: Session, run_id: int, settings: Settings) -> None:
    now = datetime.now(UTC)
    result = session.execute(
        update(SyncRun)
        .where(SyncRun.id == run_id, SyncRun.status == "running", SyncRun.lease_expires_at > now)
        .values(lease_expires_at=now + timedelta(minutes=settings.sync_lock_minutes))
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise SyncBusy("Le verrou de synchronisation a expiré.")


def _store(
    session: Session, records: list[dict[str, Any]], now: datetime, counts: dict[str, int]
) -> None:
    for raw in records:
        notice_id = str(raw.get("noticeId", ""))
        existing = session.get(Opportunity, notice_id)
        if (
            str(raw.get("active", "")).strip().lower() not in {"yes", "true", "1"}
            and existing is None
        ):
            counts["skipped_inactive"] += 1
            continue
        if str(raw.get("type", "")).strip().lower() not in VALID_TYPES and existing is None:
            counts["skipped_type"] += 1
            continue
        record = format_opp(raw, now)
        expired = record.response_deadline_utc is not None and record.response_deadline_utc < now
        if expired and existing is None:
            counts["skipped_expired"] += 1
            continue
        if expired or record.type.strip().lower() not in VALID_TYPES:
            record.active = False
        outcome = upsert(session, record)
        counts[outcome] += 1
        if outcome != "skipped_duplicate":
            audit(session, f"opportunity.{outcome}", "opportunity", record.notice_id)


def _expire(session: Session, now: datetime) -> int:
    expired = session.scalars(
        select(Opportunity.notice_id).where(Opportunity.response_deadline_utc < now)
    ).all()
    states = OpportunityStateService(session)
    count = sum(states.expire(notice_id, now) for notice_id in expired)
    for case in session.scalars(
        select(CompanyCase).where(
            CompanyCase.notice_id.in_(expired),
            CompanyCase.status.in_(["new", "screening", "bid", "no-bid"]),
        )
    ).all():
        CompanyStateService(session).transition(
            case, "expired", actor_id="system", role="system", now=now
        )
    return count


def run_sync(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    client: SamClient | None = None,
    now: datetime | None = None,
    initial_only: bool = False,
    stop_event: Event | None = None,
) -> int:
    settings = settings or Settings()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    owned_engine = engine is None
    engine = engine or make_engine(settings.database_url)
    factory = session_factory(engine)
    owned_client = client is None
    client = client or SamClient(settings)
    run_id: int | None = None
    counts = {
        name: 0
        for name in (
            "inserted",
            "updated",
            "skipped_inactive",
            "skipped_type",
            "skipped_duplicate",
            "skipped_expired",
            "purged",
        )
    }
    fetched = 0
    try:
        run_id = acquire(factory, settings, now, initial_only=initial_only)

        def heartbeat() -> None:
            with factory.begin() as session:
                assert run_id is not None
                _fence(session, run_id, settings)

        client.progress, client.stop_event = heartbeat, stop_event
        with factory() as session:
            run = session.get_one(SyncRun, run_id)
            start, end, kind = run.window_from, run.window_to, run.kind
        if kind == "initial":
            cursor = end
            while cursor >= start:
                chunk_start = max(start, cursor - timedelta(days=settings.bootstrap_chunk_days - 1))
                records = client.fetch(
                    chunk_start, cursor, min_deadline=now.date(), split_large=True
                )
                with factory.begin() as session:
                    _fence(session, run_id, settings)
                    _store(session, records, now, counts)
                    fetched += len(records)
                    session.get_one(SamBootstrap, 1).next_date = chunk_start - timedelta(days=1)
                    run = session.get_one(SyncRun, run_id)
                    run.fetched = fetched
                    for name, count in counts.items():
                        setattr(run, name, count)
                    audit(
                        session,
                        "sync.initial_chunk",
                        "sync_run",
                        str(run_id),
                        meta={"from": chunk_start.isoformat(), "to": cursor.isoformat()},
                    )
                cursor = chunk_start - timedelta(days=1)
        else:
            # A failed search page writes nothing. Successfully fetched records and individual
            # rechecks are retained if a later known-notice request reaches the provider quota.
            records = client.fetch(start, end)
            with factory.begin() as session:
                _fence(session, run_id, settings)
                _store(session, records, now, counts)
                fetched += len(records)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            with factory() as session:
                known = session.execute(
                    select(Opportunity.notice_id, Opportunity.posted_date)
                    .where(
                        Opportunity.active.is_(True),
                        or_(
                            Opportunity.response_deadline_utc.is_(None),
                            Opportunity.response_deadline_utc >= now,
                        ),
                        or_(
                            Opportunity.last_checked_at.is_(None),
                            Opportunity.last_checked_at < midnight,
                        ),
                    )
                    .order_by(Opportunity.last_checked_at.asc().nullsfirst(), Opportunity.notice_id)
                ).all()
            for notice_id, posted in known:
                refreshed = client.fetch_notice(notice_id, posted, now.date())
                with factory.begin() as session:
                    _fence(session, run_id, settings)
                    if refreshed is not None:
                        _store(session, [refreshed], now, counts)
                        fetched += 1
                    session.get_one(Opportunity, notice_id).last_checked_at = now
                    audit(
                        session,
                        "opportunity.rechecked",
                        "opportunity",
                        notice_id,
                        meta={"found": refreshed is not None},
                    )
        with factory.begin() as session:
            _fence(session, run_id, settings)
            counts["purged"] = _expire(session, now)
            run = session.get_one(SyncRun, run_id)
            for name, count in counts.items():
                setattr(run, name, count)
            run.fetched = fetched
            checkpoint = now
            if kind == "initial":
                bootstrap = session.get_one(SamBootstrap, 1)
                bootstrap.completed_at = datetime.now(UTC)
                checkpoint = bootstrap.snapshot_at
            run.status, run.finished_at, run.checkpoint_at = (
                "success",
                datetime.now(UTC),
                checkpoint,
            )
            audit(session, "sync.success", "sync_run", str(run_id), after=counts)
        log.info("sync_success", extra={"fields": {"run_id": run_id, "kind": kind, **counts}})
        return run_id
    except Exception as error:
        if run_id is not None:
            # Never store network exception strings, validation input, URLs, or secrets.
            safe_error = error.code if isinstance(error, DomainError) else type(error).__name__
            details = error.diagnostic() if isinstance(error, SamAPIError) else {}
            with factory.begin() as session:
                run = session.get_one(SyncRun, run_id)
                if run.status == "running":
                    run.status, run.error, run.finished_at = "failed", safe_error, datetime.now(UTC)
                    audit(
                        session,
                        "sync.failed",
                        "sync_run",
                        str(run_id),
                        meta={"error": safe_error, "details": details},
                    )
            log.error(
                "sync_failed",
                extra={"fields": {"run_id": run_id, "error_type": safe_error, **details}},
            )
        raise
    finally:
        client.progress = None
        client.stop_event = None
        if owned_client:
            client.close()
        if owned_engine:
            engine.dispose()


def sync_status(engine: Engine) -> dict[str, Any]:
    with session_factory(engine)() as session:
        checkpoint = last_success(session)
        bootstrap = session.get(SamBootstrap, 1)
        latest = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
        failure = None
        if latest and latest.status == "failed":
            failure = session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.action == "sync.failed",
                    AuditLog.entity_type == "sync_run",
                    AuditLog.entity_id == str(latest.id),
                )
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
        diagnostic = (failure.meta or {}).get("details") if failure else None
        return {
            "initial_sync": {
                "completed": bool(bootstrap and bootstrap.completed_at),
                "window_from": bootstrap.window_from.isoformat() if bootstrap else None,
                "window_to": bootstrap.window_to.isoformat() if bootstrap else None,
                "next_date": bootstrap.next_date.isoformat() if bootstrap else None,
                "completed_days": min(365, max(0, (bootstrap.window_to - bootstrap.next_date).days))
                if bootstrap and not bootstrap.completed_at
                else (365 if bootstrap else 0),
                "total_days": 365,
            },
            "last_run_kind": latest.kind if latest else None,
            "last_run_id": latest.id if latest else None,
            "last_run_error": diagnostic
            if diagnostic
            else (
                {"message": "Diagnostic détaillé indisponible pour cette tentative."}
                if latest and latest.status == "failed"
                else None
            ),
            "last_success": checkpoint.isoformat() if checkpoint else None,
            "age_seconds": (datetime.now(UTC) - checkpoint).total_seconds() if checkpoint else None,
            "last_run_status": latest.status if latest else None,
        }
