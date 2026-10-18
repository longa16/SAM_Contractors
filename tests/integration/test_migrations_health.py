from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.db.base import make_engine
from app.main import create_app


def test_migration_upgrade_downgrade_and_health(settings, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    config = Config("alembic.ini")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/healthz").headers["X-Request-ID"]
        assert client.get("/readyz").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/readyz").status_code == 200
    engine = make_engine(settings.database_url)
    assert "opportunity" in inspect(engine).get_table_names()
    engine.dispose()
    command.check(config)
    command.downgrade(config, "base")
    engine = make_engine(settings.database_url)
    assert "opportunity" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(config, "head")


def test_enterprise_migration_preserves_legacy_user_assignments(settings, monkeypatch, sample):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.db.base import session_factory
    from app.db.models import OpportunityState, User
    from app.sam.normalize import format_opp

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "a4d2cab60e85")
    engine = make_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                'INSERT INTO "user" (id,email,password_hash,role,is_active,created_at) '
                "VALUES (1,:email,:hash,:role,1,:created)"
            ),
            {
                "email": "legacy@example.test",
                "hash": "unused-test-hash",
                "role": "admin",
                "created": datetime.now(UTC).isoformat(),
            },
        )
    # Insert through the historical reflected schema, not the current ORM's newer columns.
    from sqlalchemy import MetaData, Table

    legacy_opportunity = Table("opportunity", MetaData(), autoload_with=engine)
    legacy_state = Table("opportunity_state", MetaData(), autoload_with=engine)
    moment = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            legacy_opportunity.insert().values(
                **format_opp(sample, moment).model_dump(), first_seen_at=moment
            )
        )
        connection.execute(
            legacy_state.insert().values(
                notice_id=sample["noticeId"],
                assigned_to=1,
                status="new",
                screening_stage="none",
                pipeline_stage="rfp",
                status_changed_at=moment,
                updated_at=moment,
                status_changed_by="system",
            )
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as session:
        assert session.get_one(User, 1).email == "legacy@example.test"
        assert session.get_one(OpportunityState, sample["noticeId"]).assigned_to == 1
    engine.dispose()
