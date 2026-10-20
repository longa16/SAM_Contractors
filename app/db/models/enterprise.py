from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime
from app.db.models.core import utcnow


class Company(Base):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    statement: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    statement_mode: Mapped[str] = mapped_column(String(16), default="form", server_default="form")
    statement_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CompanyCase(Base):
    __tablename__ = "company_opportunity_state"
    __table_args__ = (UniqueConstraint("company_id", "notice_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), index=True)
    notice_id: Mapped[str] = mapped_column(ForeignKey("opportunity.notice_id"), index=True)
    application_stage: Mapped[str | None] = mapped_column(String(32), index=True)
    application_notes: Mapped[str | None] = mapped_column(Text)
    application_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    application_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    screening_stage: Mapped[str] = mapped_column(String(16), default="none")
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending")
    bid_decision: Mapped[str | None] = mapped_column(String(16))
    bid_decision_reason: Mapped[str | None] = mapped_column(Text)
    pipeline_stage: Mapped[str] = mapped_column(String(32), default="rfp")
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    status_changed_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ScreeningResult(Base):
    __tablename__ = "screening_result"
    __table_args__ = (Index("ix_screening_case_created", "case_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    stage: Mapped[int]
    score: Mapped[float]
    recommendation: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class OpportunityComment(Base):
    __tablename__ = "opportunity_comment"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"))
    reserved_tokens: Mapped[int]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ScreeningLock(Base):
    __tablename__ = "screening_lock"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("company_opportunity_state.id"), primary_key=True
    )
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
