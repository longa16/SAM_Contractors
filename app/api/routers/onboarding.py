from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import Admin, Contractor, InternalUser, SessionDep
from app.db.models import CompanyDocument
from app.errors import DomainError, NotFound
from app.services.audit import audit
from app.services.cases import authorize_company
from app.services.documents import MAX_PDF_BYTES, validate_pdf

router = APIRouter(prefix="/api/companies/{company_id}/documents", tags=["onboarding"])
Kind = Literal["w9", "insurance", "workers_comp", "license", "certification"]


class Review(BaseModel):
    status: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)


def document_data(doc: CompanyDocument) -> dict[str, Any]:
    expired = bool(doc.expires_on and doc.expires_on < date.today())
    return {
        "id": doc.id,
        "kind": doc.kind,
        "title": doc.title,
        "expires_on": doc.expires_on.isoformat() if doc.expires_on else None,
        "status": "expired" if expired else doc.review_status,
        "review_status": doc.review_status,
        "review_note": doc.review_note,
        "created_at": doc.created_at.isoformat(),
        "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
    }


@router.get("")
def documents(company_id: int, session: SessionDep, actor: InternalUser) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    rows = session.scalars(
        select(CompanyDocument)
        .where(CompanyDocument.company_id == company.id)
        .order_by(CompanyDocument.id.desc())
    ).all()
    items = [document_data(doc) for doc in rows]
    operations = (company.profile or {}).get("operations", {})
    requirements = []
    for kind in ["w9", "insurance", "workers_comp", "license", "certification"]:
        applicable = True
        if kind == "workers_comp":
            applicable = operations.get("workers_comp_applicable")
        elif kind == "license":
            applicable = operations.get("licenses_applicable")
        elif kind == "certification":
            applicable = bool(
                operations.get("certifications")
                or (company.profile or {}).get("company", {}).get("certifications")
            )
        required_count = 1
        if kind == "license":
            required_count = max(1, len(operations.get("licenses", [])))
        elif kind == "certification":
            names = {
                str(value).strip().lower()
                for value in (company.profile or {}).get("company", {}).get("certifications", [])
            }
            names.update(
                value["name"].strip().lower() for value in operations.get("certifications", [])
            )
            required_count = max(1, len(names))
        docs = [item for item in items if item["kind"] == kind]
        if applicable is False:
            status = "not_applicable"
        elif applicable is None:
            status = "to_confirm"
        elif len(docs) < required_count:
            status = "missing"
        elif any(doc["status"] == "expired" for doc in docs):
            status = "expired"
        elif any(doc["status"] == "rejected" for doc in docs):
            status = "rejected"
        elif any(doc["status"] == "pending" for doc in docs):
            status = "pending"
        else:
            status = "accepted"
        requirements.append({"kind": kind, "status": status})
    return {"items": items, "requirements": requirements}


@router.post("", status_code=201)
async def upload(
    company_id: int,
    request: Request,
    session: SessionDep,
    actor: Contractor,
    kind: Kind,
    title: str = Query(min_length=1, max_length=200),
    expires_on: date | None = None,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/pdf":
        raise DomainError("Déposez un justificatif PDF.")
    if not title.strip():
        raise DomainError("Renseignez le titre du document.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_PDF_BYTES:
            raise DomainError("Le PDF doit faire au maximum 5 Mo.")
        content.extend(chunk)
    raw = bytes(content)
    await run_in_threadpool(validate_pdf, raw)
    doc = CompanyDocument(
        company_id=company.id, kind=kind, title=title.strip(), expires_on=expires_on, content=raw
    )
    session.add(doc)
    session.flush()
    audit(
        session,
        "onboarding.document_uploaded",
        "company_document",
        str(doc.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "kind": kind},
    )
    session.commit()
    return document_data(doc)


def find_document(session: Session, company_id: int, document_id: int) -> CompanyDocument:
    doc = session.get(CompanyDocument, document_id)
    if not doc or doc.company_id != company_id:
        raise NotFound("Justificatif introuvable.")
    return doc


@router.get("/{document_id}/pdf")
def download(
    company_id: int, document_id: int, session: SessionDep, actor: InternalUser
) -> Response:
    company = authorize_company(session, actor, company_id)
    doc = find_document(session, company.id, document_id)
    return Response(
        doc.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="justificatif-{doc.id}.pdf"'},
    )


@router.delete("/{document_id}")
def delete(
    company_id: int, document_id: int, session: SessionDep, actor: Contractor
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    doc = find_document(session, company.id, document_id)
    audit(
        session,
        "onboarding.document_deleted",
        "company_document",
        str(doc.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id},
    )
    session.delete(doc)
    session.commit()
    return {"status": "deleted"}


@router.post("/{document_id}/review")
def review(
    company_id: int, document_id: int, payload: Review, session: SessionDep, actor: Admin
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    doc = find_document(session, company.id, document_id)
    if payload.status == "accepted" and doc.expires_on and doc.expires_on < date.today():
        raise DomainError("Un justificatif expiré ne peut pas être accepté.")
    if payload.status == "rejected" and not payload.note.strip():
        raise DomainError("Expliquez la correction attendue.")
    doc.review_status, doc.review_note = payload.status, payload.note.strip()
    doc.reviewed_by, doc.reviewed_at = actor.id, datetime.now(UTC)
    audit(
        session,
        "onboarding.document_reviewed",
        "company_document",
        str(doc.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "status": payload.status},
    )
    session.commit()
    return document_data(doc)
