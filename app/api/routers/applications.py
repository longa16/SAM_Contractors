from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import Admin, InternalUser, SessionDep
from app.db.models import ApplicationMilestone, Company, CompanyCase, Opportunity
from app.errors import InvalidTransition
from app.services.audit import audit
from app.services.cases import authorize_company, get_case
from app.services.dossiers import require_approval

router = APIRouter(tags=["applications"])
PREFIX = "/api/companies/{company_id}/applications"
NEXT = {
    "interested": "examining",
    "examining": "preparing",
    "preparing": "ready",
    "ready": "submitted",
}
Stage = Literal[
    "interested", "examining", "preparing", "ready", "submitted", "won", "lost", "withdrawn"
]


class NotesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(max_length=5000)


class Validation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_stage: Stage
    summary: str = Field(min_length=1, max_length=2000)
    outcome: Literal["won", "lost", "withdrawn"] | None = None
    evidence: str = Field(default="", max_length=500)


def current_stage(case: CompanyCase) -> str:
    return case.application_stage or ("preparing" if case.bid_decision == "bid" else "interested")


def list_applications(
    session: Session, company_id: int | None, page: int, page_size: int, *, admin: bool
) -> dict[str, Any]:
    criteria = [or_(CompanyCase.application_stage.is_not(None), CompanyCase.bid_decision == "bid")]
    if company_id is not None:
        criteria.append(CompanyCase.company_id == company_id)
    total = session.scalar(select(func.count()).select_from(CompanyCase).where(*criteria)) or 0
    rows = session.execute(
        select(CompanyCase, Opportunity, Company)
        .join(Opportunity, Opportunity.notice_id == CompanyCase.notice_id)
        .join(Company, Company.id == CompanyCase.company_id)
        .where(*criteria)
        .order_by(CompanyCase.updated_at.desc(), CompanyCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for case, opp, company in rows:
        history = session.scalars(
            select(ApplicationMilestone)
            .where(ApplicationMilestone.case_id == case.id)
            .order_by(ApplicationMilestone.id)
        ).all()
        stage = current_stage(case)
        item = {
            "company_id": company.id,
            "company_name": company.name,
            "notice_id": opp.notice_id,
            "title": opp.title,
            "deadline": opp.response_deadline_utc.isoformat()
            if opp.response_deadline_utc
            else None,
            "decision": case.bid_decision if admin else None,
            "stage": stage,
            "next_stage": NEXT.get(stage),
            "notes": (case.application_notes or "") if admin else "",
            "submitted_at": case.application_submitted_at.isoformat()
            if case.application_submitted_at
            else None,
            "updated_at": (case.application_updated_at or case.updated_at).isoformat(),
            "history": [
                {
                    "from_stage": h.from_stage,
                    "to_stage": h.to_stage,
                    "summary": h.summary,
                    "created_at": h.created_at.isoformat(),
                    **({"evidence": h.evidence} if admin else {}),
                }
                for h in history
            ],
        }
        items.append(item)
    return {"total": total, "page": page, "items": items}


@router.get(PREFIX)
def applications(
    company_id: int,
    session: SessionDep,
    actor: InternalUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    return list_applications(session, company.id, page, page_size, admin=actor.role == "admin")


@router.get("/api/admin/applications")
def admin_applications(
    session: SessionDep,
    actor: Admin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return list_applications(session, None, page, page_size, admin=True)


@router.put(PREFIX + "/{notice_id}")
def update_notes(
    company_id: int, notice_id: str, payload: NotesUpdate, session: SessionDep, actor: Admin
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    case.application_notes = payload.notes.strip()
    audit(
        session,
        "application.notes_updated",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id},
    )
    session.commit()
    return {"stage": current_stage(case)}


@router.post(PREFIX + "/{notice_id}/validate")
def validate_stage(
    company_id: int, notice_id: str, payload: Validation, session: SessionDep, actor: Admin
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    previous = current_stage(case)
    if previous != payload.expected_stage or previous in {"won", "lost", "withdrawn"}:
        raise InvalidTransition("L’étape a déjà changé ; actualisez le dossier.")
    target = payload.outcome or NEXT.get(previous)
    if target is None or (payload.outcome in {"won", "lost"} and previous != "submitted"):
        raise InvalidTransition("Validez chaque étape dans l’ordre avant de déclarer le résultat.")
    if not payload.summary.strip():
        raise InvalidTransition("Renseignez le compte rendu visible par l’entreprise.")
    if target in {"preparing", "ready", "submitted", "won", "lost"} and case.bid_decision != "bid":
        raise InvalidTransition(
            "Analysez l’offre et validez la décision Bid avant de préparer le dossier."
        )
    if target == "submitted" and not payload.evidence.strip():
        raise InvalidTransition("Indiquez une référence de l’envoi réellement effectué.")
    if target in {"ready", "submitted"}:
        # Serialize dossier edits and stage progression on the case row.
        lock = session.execute(
            update(CompanyCase)
            .where(
                CompanyCase.id == case.id, CompanyCase.application_stage == case.application_stage
            )
            .values(updated_at=CompanyCase.updated_at)
        )
        if lock.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidTransition("L’étape a changé ; actualisez le dossier.")
        require_approval(session, case)
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "application_stage": target,
        "application_updated_at": now,
        "updated_at": now,
    }
    if target == "submitted":
        values["application_submitted_at"] = now
    expected = (
        CompanyCase.application_stage.is_(None)
        if case.application_stage is None
        else CompanyCase.application_stage == previous
    )
    changed = session.execute(
        update(CompanyCase)
        .where(CompanyCase.id == case.id, expected, CompanyCase.bid_decision == case.bid_decision)
        .values(**values)
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise InvalidTransition("L’étape a déjà été validée ; actualisez le dossier.")
    session.add(
        ApplicationMilestone(
            case_id=case.id,
            from_stage=previous,
            to_stage=target,
            summary=payload.summary.strip(),
            evidence=payload.evidence.strip() or None,
            validated_by=actor.id,
            created_at=now,
        )
    )
    audit(
        session,
        "application.stage_validated",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        before={"stage": previous},
        after={"stage": target},
        meta={"company_id": company.id},
    )
    session.commit()
    return {"stage": target}


@router.post(PREFIX + "/{notice_id}/follow")
def follow_application(
    company_id: int, notice_id: str, session: SessionDep, actor: InternalUser
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id, create=True)
    opp = session.get_one(Opportunity, notice_id)
    if case.application_stage is None:
        if not opp.active or (
            opp.response_deadline_utc and opp.response_deadline_utc < datetime.now(UTC)
        ):
            raise InvalidTransition("Cette offre est inactive ou expirée.")
        stage = current_stage(case)
        changed = session.execute(
            update(CompanyCase)
            .where(CompanyCase.id == case.id, CompanyCase.application_stage.is_(None))
            .values(
                application_stage=stage,
                application_updated_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        if changed.rowcount == 1:  # type: ignore[attr-defined]
            audit(
                session,
                "application.wish_created",
                "company_case",
                str(case.id),
                actor_type="user",
                actor_id=str(actor.id),
                meta={"company_id": company.id},
            )
    session.commit()
    return {"stage": current_stage(case)}
