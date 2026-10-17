"""Compatibility entry point; persistence now uses the SQL database."""

import logging

from app.config import Settings
from app.logging_setup import setup_logging
from app.sam.sync_service import run_sync

__all__ = ["run_sync"]

if __name__ == "__main__":
    settings = Settings()
    setup_logging(settings)
    try:
        run_sync(settings)
    except Exception:
        logging.getLogger(__name__).error("sync_command_failed")
        raise SystemExit(1) from None
