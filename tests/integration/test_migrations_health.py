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
