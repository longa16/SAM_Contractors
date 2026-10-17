from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import Decider, InternalUser, SessionDep
from app.db.models import Opportunity, OpportunityComment
from app.errors import NotFound
from app.repositories.catalog import latest_screening, list_catalog, serialize
from app.schemas.api import CommentCreate, Decision
from app.services.audit import audit
from app.services.cases import CompanyStateService, authorize_company, get_case
from app.services.screening import ScreeningService

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
def opportunities(
    session: SessionDep,
    actor: InternalUser,
    company_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: str | None = None,
    recommendation: str | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    naics: str | None = None,
    state: str | None = None,
    set_aside: str | None = None,
    deadline_before: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["score", "deadline"] = "score",
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    return list_catalog(
        session,
        company.id,
        page=page,
        page_size=page_size,
        status=status,
        recommendation=recommendation,
        min_score=min_score,
        naics=naics,
        state=state,
        set_aside=set_aside,
        deadline_before=deadline_before,
        q=q,
        sort=sort,
    )


@router.get("/{notice_id}")
def detail(
    notice_id: str, session: SessionDep, actor: InternalUser, company_id: int | None = None
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    opp = session.get(Opportunity, notice_id)
    if not opp:
        raise NotFound("Opportunité introuvable.")
    from app.db.models import CompanyCase

    case = session.scalar(
        select(CompanyCase).where(
            CompanyCase.company_id == company.id, CompanyCase.notice_id == notice_id
        )
    )
    return serialize(opp, case, latest_screening(session, case.id) if case else None, detail=True)


@router.post("/{notice_id}/screening")
def screening(
    notice_id: str,
    session: SessionDep,
    actor: Decider,
    request: Request,
    company_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    service: ScreeningService = request.app.state.screening
    # Release the read transaction before the service acquires its own write lock.
    selected = company.id
    session.rollback()
    result = service.run(selected, notice_id, force=force)
    return result.model_dump()


@router.post("/{notice_id}/decision")
def decision(
    notice_id: str,
    payload: Decision,
    session: SessionDep,
    actor: Decider,
    company_id: int | None = None,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    CompanyStateService(session).transition(
        case,
        payload.decision.replace("_", "-"),
        actor_id=str(actor.id),
        role=actor.role,
        reason=payload.reason,
    )
    session.commit()
    return {"notice_id": notice_id, "status": case.status}


@router.get("/{notice_id}/comments")
def comments(
    notice_id: str, session: SessionDep, actor: InternalUser, company_id: int | None = None
) -> list[dict[str, Any]]:
    company = authorize_company(session, actor, company_id)
    from app.db.models import CompanyCase

    case = session.scalar(
        select(CompanyCase).where(
            CompanyCase.company_id == company.id, CompanyCase.notice_id == notice_id
        )
    )
    if not case:
        if session.get(Opportunity, notice_id) is None:
            raise NotFound("Opportunité introuvable.")
        return []
    return [
        {
            "id": row.id,
            "body": row.body,
            "author_id": row.author_id,
            "created_at": row.created_at.isoformat(),
        }
        for row in session.scalars(
            select(OpportunityComment)
            .where(OpportunityComment.case_id == case.id)
            .order_by(OpportunityComment.id)
        ).all()
    ]


@router.post("/{notice_id}/comments", status_code=201)
def add_comment(
    notice_id: str,
    payload: CommentCreate,
    session: SessionDep,
    actor: InternalUser,
    company_id: int | None = None,
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id, create=True)
    comment = OpportunityComment(case_id=case.id, author_id=actor.id, body=payload.body)
    session.add(comment)
    session.flush()
    audit(
        session,
        "comment.created",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company.id, "comment_id": comment.id},
    )
    session.commit()
    return {"id": comment.id, "body": comment.body}
