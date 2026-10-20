from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, ForeignKey, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime
from app.db.models.core import utcnow


class CaseDossier(Base):
    __tablename__ = "case_dossier"
    case_id: Mapped[int] = mapped_column(
        ForeignKey("company_opportunity_state.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(default=0)


class CaseDocument(Base):
    __tablename__ = "case_document"
    __table_args__ = (UniqueConstraint("case_id", "document_key", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    document_key: Mapped[str] = mapped_column(String(36))
    version: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64))
    source_document_id: Mapped[int | None]
    expires_on: Mapped[date | None] = mapped_column(Date)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CaseChecklistItem(Base):
    __tablename__ = "case_checklist_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(300))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")
    checked_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DossierApproval(Base):
    __tablename__ = "dossier_approval"
    __table_args__ = (UniqueConstraint("case_id", "revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_opportunity_state.id"), index=True)
    revision: Mapped[int]
    fingerprint: Mapped[str] = mapped_column(String(64))
    prepared_document_id: Mapped[int] = mapped_column(ForeignKey("case_document.id"))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
