import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy import Engine

from app.config import Settings
from app.db.base import make_engine, session_factory
from app.logging_setup import setup_logging
from app.sam.sync_service import last_success, run_sync

log = logging.getLogger(__name__)


def needs_catchup(engine: Engine, settings: Settings, now: datetime) -> bool:
    with session_factory(engine)() as session:
        previous = last_success(session)
    return previous is None or now - previous > timedelta(hours=settings.sync_catchup_hours)


def build_scheduler(settings: Settings, engine: Engine) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")

    def job() -> None:
        try:
            run_sync(settings, engine=engine)
        except Exception:
            # The sync service persisted a sanitized failure. Never log raw exception URLs.
            log.error("scheduled_sync_failed")

    scheduler.add_job(
        job,
        CronTrigger(hour=settings.sync_cron_hours, minute=0, timezone="UTC"),
        id="sam_sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    if needs_catchup(engine, settings, datetime.now(UTC)):
        scheduler.add_job(job, "date", run_date=datetime.now(UTC), id="sam_sync_catchup")
    return scheduler


def main() -> None:
    settings = Settings()
    setup_logging(settings)
    engine = make_engine(settings.database_url)
    try:
        build_scheduler(settings, engine).start()
    finally:
        engine.dispose()
