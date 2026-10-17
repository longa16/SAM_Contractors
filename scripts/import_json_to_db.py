import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import Engine

from app.config import ROOT, Settings
from app.db.base import make_engine, session_factory
from app.logging_setup import setup_logging
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.services.audit import audit

log = logging.getLogger(__name__)


def import_json(path: Path, engine: Engine) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = list(payload.values())
    records = TypeAdapter(list[dict[str, Any]]).validate_python(payload)
    now = datetime.now(UTC)
    # Normalize the entire source first; malformed data fails atomically, never silently drops rows.
    normalized = [format_opp(record, now) for record in records]
    with session_factory(engine).begin() as session:
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
