from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.base import session_factory
from app.db.models import CompanyCase, Opportunity, OpportunityState
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.services.catalog_cleanup import cleanup_expired


def test_purge_removes_expired_catalogue_entries_without_erasing_linked_cases(
    engine, company, sample
):
    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        for notice_id, deadline in [
            ("expired", "2001-01-01"),
            ("historical", "2001-01-01"),
            ("valid", "2099-01-01"),
            ("unknown", None),
        ]:
            upsert(
                session,
                format_opp(dict(sample, noticeId=notice_id, responseDeadLine=deadline), now),
            )
        case = CompanyCase(
            company_id=company.id,
            notice_id="historical",
            application_stage="submitted",
            bid_decision="bid",
        )
        session.add(case)
    with session_factory(engine).begin() as session:
        assert cleanup_expired(session, now) == 1
    with session_factory(engine)() as session:
        assert session.get(Opportunity, "expired") is None
        assert session.get(OpportunityState, "expired") is None
        assert session.get(Opportunity, "valid") is not None
        assert session.get(Opportunity, "unknown") is not None
        assert session.get(Opportunity, "historical") is not None
        case = session.scalar(select(CompanyCase).where(CompanyCase.notice_id == "historical"))
        assert case.application_stage == "submitted" and case.status == "expired"
    with session_factory(engine).begin() as session:
        assert cleanup_expired(session, now) == 0


def test_migration_removes_bootstrap_and_cleans_existing_catalogue(settings, monkeypatch, sample):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table, inspect, text

    from app.db.base import make_engine
    from app.db.models import Company, SyncRun

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "b0baeedf8e85")
    engine = make_engine(settings.database_url)
    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        company = Company(name="Keep candidature history")
        session.add(company)
        session.flush()
        for notice_id, deadline in [
            ("expired", "2001-01-01"),
            ("linked", "2001-01-01"),
            ("valid", "2099-01-01"),
        ]:
            upsert(
                session,
                format_opp(dict(sample, noticeId=notice_id, responseDeadLine=deadline), now),
            )
        session.add(
            CompanyCase(company_id=company.id, notice_id="linked", application_stage="examining")
        )
        session.add(
            SyncRun(
                started_at=now,
                lease_expires_at=now + timedelta(minutes=30),
                window_from=now.date() - timedelta(days=364),
                window_to=now.date(),
                kind="initial",
                status="running",
            )
        )
    bootstrap = Table("sam_bootstrap", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            bootstrap.insert().values(
                id=1,
                window_from=now.date() - timedelta(days=364),
                window_to=now.date(),
                next_date=now.date(),
                snapshot_at=now,
            )
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = make_engine(settings.database_url)
    assert "sam_bootstrap" not in inspect(engine).get_table_names()
    with session_factory(engine)() as session:
        assert session.get(Opportunity, "expired") is None
        assert session.get(Opportunity, "linked") is not None
        assert session.get(Opportunity, "valid") is not None
        run = session.scalar(select(SyncRun))
        assert run.status == "failed" and run.error == "initial_load_removed"
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
