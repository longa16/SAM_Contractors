from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import Admin, Contractor, InternalUser, SessionDep
from app.db.models import Company, CompanyCase
from app.errors import DomainError, NotFound
from app.schemas.api import CompanyCreate, UserCreate
from app.schemas.company import CompanyProfile, CompanyStatement, Reference, StrictModel
from app.services.audit import audit
from app.services.auth import create_user
from app.services.cases import authorize_company
from app.services.documents import MAX_PDF_BYTES, validate_pdf

router = APIRouter(prefix="/api", tags=["companies"])


@router.get("/companies")
def companies(session: SessionDep, actor: InternalUser) -> list[dict[str, Any]]:
    statement = select(Company).where(Company.is_active.is_(True))
    if actor.role != "admin":
        statement = statement.where(Company.id == actor.company_id)
    return [
        {"id": c.id, "name": c.name, "profile_complete": c.profile is not None}
        for c in session.scalars(statement.order_by(Company.name)).all()
    ]


@router.post("/companies", status_code=201)
def create_company(payload: CompanyCreate, session: SessionDep, actor: Admin) -> dict[str, Any]:
    company = Company(
        name=payload.name,
        profile=payload.profile.model_dump(mode="json") if payload.profile else None,
    )
    session.add(company)
    session.flush()
    audit(
        session,
        "company.created",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
    )
    session.commit()
    return {"id": company.id, "name": company.name}


@router.get("/companies/{company_id}/profile")
def get_profile(company_id: int, session: SessionDep, actor: InternalUser) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    return {"id": company.id, "name": company.name, "profile": company.profile}


@router.put("/companies/{company_id}/profile")
def update_profile(
    company_id: int, payload: CompanyProfile, session: SessionDep, actor: Contractor
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    before = company.profile
    company.profile = payload.model_dump(mode="json")
    if before != company.profile:
        for case in session.scalars(
            select(CompanyCase).where(CompanyCase.company_id == company.id)
        ).all():
            case.analysis_status = "stale_profile"
            audit(
                session,
                "screening.profile_invalidated",
                "company_case",
                str(case.id),
                actor_type="user",
                actor_id=str(actor.id),
                meta={"company_id": company.id},
            )
    audit(
        session,
        "company.profile_updated",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
        before=before,
        after=company.profile,
    )
    session.commit()
    return {"id": company.id, "profile": company.profile}


@router.post("/users", status_code=201)
def add_user(payload: UserCreate, session: SessionDep, actor: Admin) -> dict[str, Any]:
    user = create_user(
        session,
        payload.email,
        payload.password.get_secret_value(),
        payload.role,
        payload.company_id,
        actor_id=str(actor.id),
    )
    session.commit()
    return {"id": user.id, "email": user.email, "role": user.role, "company_id": user.company_id}


@router.get("/companies/{company_id}/statement")
def get_statement(company_id: int, session: SessionDep, actor: InternalUser) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    return {
        "company_id": company.id,
        "name": company.name,
        "statement": company.statement or CompanyStatement().model_dump(),
        "mode": company.statement_mode,
        "pdf_available": company.statement_pdf is not None,
    }


@router.put("/companies/{company_id}/statement")
def update_statement(
    company_id: int, payload: CompanyStatement, session: SessionDep, actor: Contractor
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    before = company.statement
    company.statement = payload.model_dump(mode="json")
    company.statement_mode = "form"
    audit(
        session,
        "company.statement_updated",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
        before=before,
        after=company.statement,
    )
    session.commit()
    return {"company_id": company.id, "statement": company.statement}


@router.put("/companies/{company_id}/statement/pdf")
async def upload_statement_pdf(
    company_id: int, request: Request, session: SessionDep, actor: Contractor
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/pdf":
        raise DomainError("Sélectionnez un document PDF.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_PDF_BYTES:
            raise DomainError("Le PDF doit faire au maximum 5 Mo.")
        content.extend(chunk)
    raw = bytes(content)
    await run_in_threadpool(validate_pdf, raw)
    company.statement_pdf = raw
    company.statement_mode = "pdf"
    audit(
        session,
        "company.statement_pdf_uploaded",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"bytes": len(raw)},
    )
    session.commit()
    return {"mode": "pdf", "pdf_available": True}


@router.get("/companies/{company_id}/statement/pdf")
def download_statement_pdf(company_id: int, session: SessionDep, actor: InternalUser) -> Response:
    company = authorize_company(session, actor, company_id)
    if company.statement_pdf is None:
        raise NotFound("Aucun PDF enregistré pour cette entreprise.")
    return Response(
        company.statement_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="company-statement.pdf"'},
    )


@router.delete("/companies/{company_id}/statement/pdf")
def delete_statement_pdf(company_id: int, session: SessionDep, actor: Contractor) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    company.statement_pdf = None
    company.statement_mode = "form"
    audit(
        session,
        "company.statement_pdf_deleted",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
    )
    session.commit()
    return {"mode": "form", "pdf_available": False}


@router.post("/companies/{company_id}/statement/pdf/activate")
def activate_statement_pdf(
    company_id: int, session: SessionDep, actor: Contractor
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    if company.statement_pdf is None:
        raise NotFound("Déposez d’abord votre PDF.")
    company.statement_mode = "pdf"
    audit(
        session,
        "company.statement_pdf_activated",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
    )
    session.commit()
    return {"mode": "pdf"}


class ExperienceUpdate(StrictModel):
    government_experience: str = Field(default="", max_length=10000)
    references: list[Reference] = Field(default_factory=list, max_length=30)


@router.patch("/companies/{company_id}/statement/experience")
def update_experience(
    company_id: int, payload: ExperienceUpdate, session: SessionDep, actor: Contractor
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    statement = CompanyStatement.model_validate(company.statement or {}).model_dump(mode="json")
    statement.update(payload.model_dump(mode="json"))
    company.statement = statement
    audit(
        session,
        "company.experience_updated",
        "company",
        str(company.id),
        actor_type="user",
        actor_id=str(actor.id),
    )
    session.commit()
    return {"statement": statement}
