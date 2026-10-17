import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.base import session_factory
from app.db.models import Opportunity, OpportunityState
from scripts.import_json_to_db import import_json


def test_all_901_legacy_records_import_twice_without_loss(engine, tmp_path):
    path = tmp_path / "legacy.json"
    path.write_bytes(
        gzip.decompress((Path(__file__).parents[1] / "fixtures/legacy_901.json.gz").read_bytes())
    )
    assert import_json(path, engine) == 901
    assert import_json(path, engine) == 901
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 901
        assert session.scalar(select(func.count()).select_from(OpportunityState)) == 901
        assert (
            session.get_one(OpportunityState, session.scalar(select(Opportunity.notice_id))).status
            == "new"
        )


def test_import_mapping_and_invalid_input(engine, tmp_path, sample):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({sample["noticeId"]: sample}))
    assert import_json(path, engine) == 1
    sample["postedDate"] = None
    path.write_text(json.dumps([sample]))
    with pytest.raises(ValueError):
        import_json(path, engine)
