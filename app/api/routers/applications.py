from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app.api.deps import Decider, InternalUser, SessionDep
from app.db.models import CompanyCase, Opportunity
from app.errors import InvalidTransition
from app.services.audit import audit
from app.services.cases import authorize_company, get_case

router = APIRouter(prefix="/api/companies/{company_id}/applications", tags=["applications"])


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["interested", "preparing", "submitted", "won", "lost", "withdrawn"]
    notes: str = Field(default="", max_length=5000)


@router.get("")
def applications(
    company_id: int,
    session: SessionDep,
    actor: InternalUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    criteria = [
        CompanyCase.company_id == company.id,
        or_(CompanyCase.application_stage.is_not(None), CompanyCase.bid_decision == "bid"),
    ]
    total = session.scalar(select(func.count()).select_from(CompanyCase).where(*criteria)) or 0
    rows = session.execute(
        select(CompanyCase, Opportunity)
        .join(Opportunity, Opportunity.notice_id == CompanyCase.notice_id)
        .where(*criteria)
        .order_by(CompanyCase.updated_at.desc(), CompanyCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": total,
        "page": page,
        "items": [
            {
                "notice_id": opp.notice_id,
                "title": opp.title,
                "deadline": opp.response_deadline_utc.isoformat()
                if opp.response_deadline_utc
                else None,
                "decision": case.bid_decision,
                "status": case.status,
                "stage": case.application_stage or "preparing",
                "notes": case.application_notes or "",
                "submitted_at": case.application_submitted_at.isoformat()
                if case.application_submitted_at
                else None,
                "updated_at": (case.application_updated_at or case.updated_at).isoformat(),
            }
            for case, opp in rows
        ],
    }


@router.put("/{notice_id}")
def update_application(
    company_id: int,
    notice_id: str,
    payload: ApplicationUpdate,
    session: SessionDep,
    actor: Decider,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id, create=True)
    previous = case.application_stage or (
        "preparing" if case.bid_decision == "bid" else "interested"
    )
    allowed = {
        "interested": {"preparing", "withdrawn"},
        "preparing": {"interested", "submitted", "withdrawn"},
        "submitted": {"won", "lost", "withdrawn"},
        "won": set(),
        "lost": set(),
        "withdrawn": {"interested", "preparing"},
    }
    if payload.stage != previous and payload.stage not in allowed[previous]:
        raise InvalidTransition("Cette évolution de candidature n’est pas autorisée.")
    if payload.stage in {"submitted", "won", "lost"} and case.bid_decision != "bid":
        raise InvalidTransition("Validez d’abord la décision Bid avant de déclarer un dépôt.")
    before = {"stage": case.application_stage, "notes": case.application_notes}
    now = datetime.now(UTC)
    case.application_stage = payload.stage
    case.application_notes = payload.notes.strip()
    case.application_updated_at = case.updated_at = now
    if payload.stage == "submitted" and case.application_submitted_at is None:
        case.application_submitted_at = now
    audit(
        session,
        "application.updated",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        before=before,
        after={"stage": payload.stage, "notes": case.application_notes},
        meta={"company_id": company.id, "manual_declaration": True},
    )
    session.commit()
    return {"notice_id": notice_id, "stage": case.application_stage}


@router.post("/{notice_id}/follow")
def follow_application(
    company_id: int, notice_id: str, session: SessionDep, actor: Decider
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id, create=True)
    if case.application_stage is None:
        case.application_stage = "preparing" if case.bid_decision == "bid" else "interested"
        case.application_updated_at = case.updated_at = datetime.now(UTC)
        audit(
            session,
            "application.followed",
            "company_case",
            str(case.id),
            actor_type="user",
            actor_id=str(actor.id),
            meta={"company_id": company.id},
        )
    session.commit()
    return {"stage": case.application_stage}
