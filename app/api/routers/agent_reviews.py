from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import Admin, SessionDep
from app.db.models import AgentReview
from app.schemas.agent_reviews import ReviewDecision
from app.services import agent_reviews as reviews
from app.services.cases import authorize_company, get_case
from app.services.review_context import context_hash, context_manifest

router = APIRouter(
    prefix="/api/companies/{company_id}/applications/{notice_id}/agent-examinations",
    tags=["agent-examinations"],
)


@router.get("")
def history(
    company_id: int,
    notice_id: str,
    session: SessionDep,
    actor: Admin,
    request: Request,
    page: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    reviews.recover_expired(session)
    current_hash = context_hash(context_manifest(session, case))
    query = select(AgentReview).where(AgentReview.case_id == case.id)
    rows = session.scalars(
        query.order_by(AgentReview.id.desc()).offset((page - 1) * 10).limit(10)
    ).all()
    total = (
        session.scalar(
            select(func.count()).select_from(AgentReview).where(AgentReview.case_id == case.id)
        )
        or 0
    )
    result = {
        "enabled": reviews.configured(request.app.state.settings),
        "page": page,
        "total": total,
        "items": [reviews.serialize_run(row, current_hash) for row in rows],
    }
    session.commit()
    return result


@router.post("", status_code=202)
def launch(
    company_id: int, notice_id: str, session: SessionDep, actor: Admin, request: Request
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    row = reviews.enqueue(session, case, actor.id, request.app.state.settings)
    session.commit()
    return {"id": row.id, "status": row.status}


@router.post("/{run_id}/cancel")
def cancel(
    company_id: int, notice_id: str, run_id: int, session: SessionDep, actor: Admin
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    reviews.cancel(session, case, reviews.get_run(session, case, run_id), actor.id)
    session.commit()
    return {"status": "cancelled"}


@router.post("/{run_id}/decision")
def decide(
    company_id: int,
    notice_id: str,
    run_id: int,
    payload: ReviewDecision,
    session: SessionDep,
    actor: Admin,
) -> dict[str, str]:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    reviews.decide(session, case, reviews.get_run(session, case, run_id), actor.id, payload)
    session.commit()
    return {"review_decision": payload.decision}


@router.get("/{run_id}/report")
def download(
    company_id: int, notice_id: str, run_id: int, session: SessionDep, actor: Admin
) -> JSONResponse:
    company = authorize_company(session, actor, company_id)
    case = get_case(session, company.id, notice_id)
    row = reviews.get_run(session, case, run_id)
    return JSONResponse(
        reviews.serialize_run(row, context_hash(context_manifest(session, case))),
        headers={"Content-Disposition": f'attachment; filename="examen-{run_id}.json"'},
    )
