from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import Admin, Decider, InternalUser, SessionDep
from app.db.models import Company, CompanyCase
from app.schemas.api import CompanyCreate, UserCreate
from app.schemas.company import CompanyProfile
from app.services.audit import audit
from app.services.auth import create_user
from app.services.cases import authorize_company

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
        name=payload.name, profile=payload.profile.model_dump() if payload.profile else None
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
    company_id: int, payload: CompanyProfile, session: SessionDep, actor: Decider
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    before = company.profile
    company.profile = payload.model_dump()
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
