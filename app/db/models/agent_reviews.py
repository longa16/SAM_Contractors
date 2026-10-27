from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime
from app.db.models.core import utcnow


class AgentReview(Base):
    __tablename__ = "case_agent_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    active_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_opportunity_state.id"), unique=True
    )
    requested_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, deferred=True)
    model: Mapped[str] = mapped_column(String(200))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(40))
    review_decision: Mapped[str | None] = mapped_column(String(16))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
