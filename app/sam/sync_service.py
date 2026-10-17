import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import make_engine, session_factory
from app.db.models import CompanyCase, Opportunity, SyncRun
from app.errors import DomainError, SyncBusy
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


def acquire(factory: sessionmaker[Session], settings: Settings, now: datetime) -> int:
    try:
        with factory.begin() as session:
            stale = session.scalars(
                select(SyncRun).where(SyncRun.status == "running", SyncRun.lease_expires_at <= now)
            ).all()
            for run in stale:
                run.status, run.error, run.finished_at = "failed", "lease_expired", now
                audit(session, "sync.lease_expired", "sync_run", str(run.id))
            session.flush()
            previous = last_checkpoint(session) or now - timedelta(days=1)
            run = SyncRun(
                started_at=now,
                lease_expires_at=now + timedelta(minutes=settings.sync_lock_minutes),
                window_from=(previous - timedelta(days=1)).date(),
                window_to=now.date(),
                status="running",
            )
            session.add(run)
            session.flush()
            audit(session, "sync.started", "sync_run", str(run.id))
            return run.id
    except IntegrityError:
        raise SyncBusy("Une synchronisation est déjà en cours.") from None


def run_sync(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    client: SamClient | None = None,
    now: datetime | None = None,
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
    try:
        run_id = acquire(factory, settings, now)
        with factory() as session:
            run = session.get_one(SyncRun, run_id)
            start, end = run.window_from, run.window_to
        # No database writes are committed before every remote page has succeeded.
        raw_records = client.fetch(start, end)
        counts: dict[str, int] = {
            name: 0
            for name in (
                "inserted",
                "updated",
                "skipped_inactive",
                "skipped_type",
                "skipped_duplicate",
                "purged",
            )
        }
        with factory.begin() as session:
            # Fence out expired workers even after a successor acquires the lock.
            result = session.execute(
                update(SyncRun)
                .where(
                    SyncRun.id == run_id,
                    SyncRun.status == "running",
                    SyncRun.lease_expires_at > datetime.now(UTC),
                )
                .values(
                    lease_expires_at=datetime.now(UTC)
                    + timedelta(minutes=settings.sync_lock_minutes)
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise SyncBusy("Le verrou de synchronisation a expiré.")
            for raw in raw_records:
                if str(raw.get("active", "")).strip().lower() not in {"yes", "true", "1"}:
                    counts["skipped_inactive"] += 1
                    continue
                if str(raw.get("type", "")).strip().lower() not in VALID_TYPES:
                    counts["skipped_type"] += 1
                    continue
                record = format_opp(raw, now)
                outcome = upsert(session, record)
                counts[outcome] += 1
                if outcome != "skipped_duplicate":
                    audit(session, f"opportunity.{outcome}", "opportunity", record.notice_id)
            session.flush()
            expired = session.scalars(
                select(Opportunity.notice_id).where(Opportunity.response_deadline_utc < now)
            ).all()
            states = OpportunityStateService(session)
            counts["purged"] = sum(states.expire(notice_id, now) for notice_id in expired)
            for case in session.scalars(
                select(CompanyCase).where(
                    CompanyCase.notice_id.in_(expired),
                    CompanyCase.status.in_(["new", "screening", "bid", "no-bid"]),
                )
            ).all():
                CompanyStateService(session).transition(
                    case, "expired", actor_id="system", role="system", now=now
                )
            # Retain expired records conservatively; linked dossiers must not be deleted.
            run = session.get_one(SyncRun, run_id)
            for name, count in counts.items():
                setattr(run, name, count)
            run.fetched = len(raw_records)
            run.status, run.finished_at, run.checkpoint_at = "success", datetime.now(UTC), now
            audit(session, "sync.success", "sync_run", str(run_id), after=counts)
        log.info("sync_success", extra={"fields": {"run_id": run_id, **counts}})
        return run_id
    except Exception as error:
        if run_id is not None:
            # Never store network exception strings, validation input, URLs, or secrets.
            safe_error = error.code if isinstance(error, DomainError) else type(error).__name__
            with factory.begin() as session:
                run = session.get_one(SyncRun, run_id)
                if run.status == "running":
                    run.status, run.error, run.finished_at = "failed", safe_error, datetime.now(UTC)
                    audit(
                        session, "sync.failed", "sync_run", str(run_id), meta={"error": safe_error}
                    )
            log.error("sync_failed", extra={"fields": {"run_id": run_id, "error_type": safe_error}})
        raise
    finally:
        if owned_client:
            client.close()
        if owned_engine:
            engine.dispose()


def sync_status(engine: Engine) -> dict[str, Any]:
    with session_factory(engine)() as session:
        checkpoint = last_success(session)
        latest = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
        return {
            "last_success": checkpoint.isoformat() if checkpoint else None,
            "age_seconds": (datetime.now(UTC) - checkpoint).total_seconds() if checkpoint else None,
            "last_run_status": latest.status if latest else None,
        }
