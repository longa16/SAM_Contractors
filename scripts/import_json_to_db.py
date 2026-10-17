import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, TypeAdapter
from sqlalchemy import Engine

from app.config import ROOT, Settings
from app.db.base import make_engine, session_factory
from app.db.models import SyncRun
from app.logging_setup import setup_logging
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.sam.sync_service import last_checkpoint
from app.services.audit import audit

log = logging.getLogger(__name__)


class LegacyCheckpoint(BaseModel):
    last_sync: AwareDatetime


def import_json(path: Path, engine: Engine) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = list(payload.values())
    records = TypeAdapter(list[dict[str, Any]]).validate_python(payload)
    now = datetime.now(UTC)
    # Normalize the entire source first; malformed data fails atomically, never silently drops rows.
    normalized = [format_opp(record, now) for record in records]
    checkpoint_file = path.with_name("last_sync.json")
    checkpoint = None
    if checkpoint_file.exists():
        checkpoint = LegacyCheckpoint.model_validate_json(checkpoint_file.read_text()).last_sync
        if checkpoint > now:
            raise ValueError("Legacy checkpoint cannot be in the future")
    with session_factory(engine).begin() as session:
        if checkpoint is not None and last_checkpoint(session) is None:
            session.add(
                SyncRun(
                    started_at=now,
                    finished_at=now,
                    lease_expires_at=now,
                    window_from=checkpoint.date(),
                    window_to=checkpoint.date(),
                    checkpoint_at=checkpoint,
                    status="imported",
                )
            )
            audit(session, "import.checkpoint_preserved", "import", "legacy_json")
        for record in normalized:
            outcome = upsert(session, record)
            if outcome != "skipped_duplicate":
                audit(session, "opportunity.imported", "opportunity", record.notice_id)
        audit(session, "import.completed", "import", "legacy_json", meta={"count": len(records)})
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import SAM legacy JSON without deleting the source"
    )
    parser.add_argument("path", type=Path, nargs="?", default=ROOT / "data/opportunities_db.json")
    args = parser.parse_args()
    settings = Settings()
    setup_logging(settings)
    engine = make_engine(settings.database_url)
    try:
        count = import_json(args.path, engine)
        log.info("import_completed", extra={"fields": {"count": count}})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
