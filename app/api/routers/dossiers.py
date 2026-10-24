from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from app.api.deps import Admin, SessionDep
from app.db.models import CaseChecklistItem, CaseDocument, CompanyDocument, Opportunity
from app.errors import Conflict, DomainError, NotFound
from app.schemas.dossier import (
    ChecklistCreate,
    ChecklistUpdate,
    DocumentKind,
    FinalApproval,
    ImportDocument,
)
from app.services import dossiers
from app.services.audit import audit
from app.services.cases import authorize_company, get_case
from app.services.documents import MAX_PDF_BYTES, validate_pdf

router = APIRouter(
    prefix="/api/companies/{company_id}/applications/{notice_id}/dossier", tags=["dossiers"]
)


@router.get("")
def detail(company_id: int, notice_id: str, session: SessionDep, actor: Admin) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    data = dossiers.dossier_state(session, case)
    data["resource_links"] = session.get_one(Opportunity, notice_id).resource_links
    data["company_documents"] = [
        {
            "id": doc.id,
            "title": doc.title,
            "kind": doc.kind,
            "accepted": doc.review_status == "accepted"
            and not (doc.expires_on and doc.expires_on < date.today()),
        }
        for doc in session.scalars(
            select(CompanyDocument).where(CompanyDocument.company_id == company.id)
        ).all()
    ]
    data["statement_pdf_available"] = company.statement_pdf is not None
    data["editable"] = case.application_stage not in {
        "submitted",
        "won",
        "lost",
        "withdrawn",
        "rejected",
    }
    data["stage"] = case.application_stage or (
        "preparing" if case.bid_decision == "bid" else "interested"
    )
    session.commit()
    return data


@router.post("/documents", status_code=201)
async def upload(
    company_id: int,
    notice_id: str,
    request: Request,
    session: SessionDep,
    actor: Admin,
    kind: DocumentKind,
    title: str = Query(min_length=1, max_length=200),
    expected_revision: int = Query(ge=0),
    previous_document_id: int | None = Query(default=None, gt=0),
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    if (
        not title.strip()
        or request.headers.get("content-type", "").split(";")[0].lower() != "application/pdf"
    ):
        raise DomainError("Renseignez un titre et sélectionnez un PDF.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_PDF_BYTES:
            raise DomainError("Le PDF doit faire au maximum 5 Mo.")
        content.extend(chunk)
    raw = bytes(content)
    await run_in_threadpool(validate_pdf, raw)
    doc = dossiers.add_document(
        session,
        case,
        expected_revision=expected_revision,
        kind=kind,
        title=title.strip(),
        content=raw,
        actor_id=actor.id,
        previous_document_id=previous_document_id,
    )
    session.commit()
    return dossiers.document_data(doc)


@router.post("/import", status_code=201)
def import_document(
    company_id: int, notice_id: str, payload: ImportDocument, session: SessionDep, actor: Admin
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    source = session.get(CompanyDocument, payload.source_document_id)
    if source is None or source.company_id != company.id:
        raise NotFound("Justificatif de l’entreprise introuvable.")
    if source.review_status != "accepted" or (
        source.expires_on and source.expires_on < date.today()
    ):
        raise Conflict("Faites accepter un justificatif en cours de validité avant de l’importer.")
    doc = dossiers.add_document(
        session,
        case,
        expected_revision=payload.expected_revision,
        kind="contractor",
        title=source.title,
        content=source.content,
        actor_id=actor.id,
        previous_document_id=payload.previous_document_id,
        source=source,
    )
    session.commit()
    return dossiers.document_data(doc)


@router.post("/import-statement", status_code=201)
def import_statement(
    company_id: int,
    notice_id: str,
    session: SessionDep,
    actor: Admin,
    expected_revision: int = Query(ge=0),
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    if company.statement_pdf is None:
        raise NotFound("Aucun Company Statement PDF à importer.")
    doc = dossiers.add_document(
        session,
        case,
        expected_revision=expected_revision,
        kind="contractor",
        title="Company Statement — " + company.name[:170],
        content=company.statement_pdf,
        actor_id=actor.id,
    )
    session.commit()
    return dossiers.document_data(doc)


@router.get("/documents/{document_id}/pdf")
def download(
    company_id: int, notice_id: str, document_id: int, session: SessionDep, actor: Admin
) -> Response:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    doc = dossiers.get_document(session, case, document_id)
    return Response(
        doc.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="dossier-{doc.id}-v{doc.version}.pdf"'
        },
    )


@router.post("/documents/{document_id}/archive")
def archive_document(
    company_id: int,
    notice_id: str,
    document_id: int,
    session: SessionDep,
    actor: Admin,
    expected_revision: int = Query(ge=0),
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    doc = dossiers.get_document(session, case, document_id)
    dossiers.lock_edit(session, case, expected_revision)
    session.execute(
        update(CaseDocument)
        .where(CaseDocument.case_id == case.id, CaseDocument.document_key == doc.document_key)
        .values(archived=True)
    )
    audit(
        session,
        "dossier.document_archived",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "document_id": doc.id},
    )
    session.commit()
    return {"status": "archived"}


@router.post("/checklist", status_code=201)
def add_check(
    company_id: int, notice_id: str, payload: ChecklistCreate, session: SessionDep, actor: Admin
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    dossiers.lock_edit(session, case, payload.expected_revision)
    from uuid import uuid4

    item = CaseChecklistItem(
        case_id=case.id, code=str(uuid4()), label=payload.label, required=payload.required
    )
    session.add(item)
    session.flush()
    audit(
        session,
        "dossier.check_added",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "item_id": item.id},
    )
    session.commit()
    return dossiers.checklist_data(item)


@router.patch("/checklist/{item_id}")
def update_check(
    company_id: int,
    notice_id: str,
    item_id: int,
    payload: ChecklistUpdate,
    session: SessionDep,
    actor: Admin,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    item = session.get(CaseChecklistItem, item_id)
    if item is None or item.case_id != case.id:
        raise NotFound("Point de contrôle introuvable.")
    if payload.status != "pending" and not payload.note.strip():
        raise Conflict("Décrivez le contrôle effectué ou la raison de non-applicabilité.")
    dossiers.lock_edit(session, case, payload.expected_revision)
    item.status, item.note = payload.status, payload.note.strip()
    item.checked_by = actor.id if payload.status != "pending" else None
    item.checked_at = datetime.now(UTC) if payload.status != "pending" else None
    audit(
        session,
        "dossier.check_updated",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "item_id": item.id, "status": item.status},
    )
    session.commit()
    return dossiers.checklist_data(item)


@router.post("/approve")
def approve(
    company_id: int, notice_id: str, payload: FinalApproval, session: SessionDep, actor: Admin
) -> dict[str, int]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    approval = dossiers.approve(
        session,
        case,
        revision=payload.expected_revision,
        document_id=payload.prepared_document_id,
        note=payload.note,
        actor_id=actor.id,
    )
    session.commit()
    return {"approval_id": approval.id, "revision": approval.revision}


@router.get("/approvals/{approval_id}/package")
def package(
    company_id: int, notice_id: str, approval_id: int, session: SessionDep, actor: Admin
) -> StreamingResponse:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    chunks = dossiers.export_package(session, case, approval_id)
    audit(
        session,
        "dossier.package_downloaded",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "approval_id": approval_id},
    )
    session.commit()
    return StreamingResponse(
        chunks,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="candidature-{case.id}-{approval_id}.zip"'
        },
    )
