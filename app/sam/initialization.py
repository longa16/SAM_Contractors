import logging
from threading import Event

from sqlalchemy import Engine

from app.config import Settings
from app.errors import InitialSyncComplete, SyncBusy
from app.sam.sync_service import run_sync

log = logging.getLogger(__name__)


def initialize_catalog(settings: Settings, engine: Engine, stop_event: Event) -> None:
    try:
        run_sync(settings, engine=engine, initial_only=True, stop_event=stop_event)
    except (InitialSyncComplete, SyncBusy):
        log.info("initial_sync_already_completed_or_running")
    except Exception as error:
        # run_sync persists the sanitized details and the resumable initial cursor.
        log.error("initial_sync_paused", extra={"fields": {"exception_type": type(error).__name__}})
