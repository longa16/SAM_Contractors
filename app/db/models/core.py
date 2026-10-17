from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class Opportunity(Base):
    __tablename__ = "opportunity"

    notice_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    solicitation_number: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(128))
    description_url: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    description_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    point_of_contact: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    additional_info_link: Mapped[str | None] = mapped_column(Text)
    agency_path: Mapped[str] = mapped_column(Text)
    agency_code: Mapped[str] = mapped_column(Text)
    organization_type: Mapped[str] = mapped_column(String(128))
    office_address: Mapped[str] = mapped_column(Text)
    naics_code: Mapped[str] = mapped_column(String(32), index=True)
    classification_code: Mapped[str] = mapped_column(String(32))
    set_aside_code: Mapped[str | None] = mapped_column(String(64))
    set_aside_label: Mapped[str | None] = mapped_column(Text)
    set_aside_category: Mapped[str] = mapped_column(String(64))
    posted_date: Mapped[date] = mapped_column(Date)
    response_deadline_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    response_deadline_raw: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
    place_of_performance_raw: Mapped[str | None] = mapped_column(Text)
    pop_state: Mapped[str | None] = mapped_column(String(2))
    resource_links: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class OpportunityState(Base):
    __tablename__ = "opportunity_state"

    notice_id: Mapped[str] = mapped_column(ForeignKey("opportunity.notice_id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    status_changed_by: Mapped[str] = mapped_column(String(128), default="system")
    screening_stage: Mapped[str] = mapped_column(String(16), default="none")
    bid_decision: Mapped[str | None] = mapped_column(String(16))
    bid_decision_reason: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    pipeline_stage: Mapped[str] = mapped_column(String(32), default="rfp")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_run"
    __table_args__ = (
        Index(
            "uq_sync_running",
            "status",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    window_from: Mapped[date] = mapped_column(Date)
    window_to: Mapped[date] = mapped_column(Date)
    checkpoint_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped_inactive: Mapped[int] = mapped_column(Integer, default=0)
    skipped_type: Mapped[int] = mapped_column(Integer, default=0)
    skipped_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    purged: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), index=True)
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    contractor_id: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
