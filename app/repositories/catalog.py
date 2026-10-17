from datetime import datetime
from typing import Any

from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CompanyCase, Opportunity, ScreeningResult
from app.errors import Conflict


def latest_screening(session: Session, case_id: int) -> ScreeningResult | None:
    return session.scalar(
        select(ScreeningResult)
        .where(ScreeningResult.case_id == case_id)
        .order_by(ScreeningResult.id.desc())
        .limit(1)
    )


def serialize(
    opp: Opportunity,
    case: CompanyCase | None,
    screening: ScreeningResult | None,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "notice_id": opp.notice_id,
        "title": opp.title,
        "agency": opp.agency_path.split(".")[-1],
        "agency_path": opp.agency_path,
        "naics_code": opp.naics_code,
        "pop_state": opp.pop_state,
        "place": opp.place_of_performance_raw or opp.office_address,
        "set_aside": opp.set_aside_label,
        "deadline": opp.response_deadline_utc.isoformat() if opp.response_deadline_utc else None,
        "status": case.status if case else "new",
        "pipeline_stage": case.pipeline_stage if case else None,
        "analysis_status": case.analysis_status if case else "pending",
        "screening_stage": case.screening_stage if case else "none",
        "screening": screening.data if screening else None,
    }
    if detail:
        result.update(
            {
                "solicitation_number": opp.solicitation_number,
                "description_text": opp.description_text,
                "office_address": opp.office_address,
                "resource_links": opp.resource_links,
                "sam_url": f"https://sam.gov/opp/{opp.notice_id}/view",
                "decision_reason": case.bid_decision_reason if case else None,
            }
        )
    return result


def list_catalog(
    session: Session,
    company_id: int,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    recommendation: str | None = None,
    min_score: float | None = None,
    naics: str | None = None,
    state: str | None = None,
    set_aside: str | None = None,
    deadline_before: datetime | None = None,
    q: str | None = None,
    sort: str = "score",
) -> dict[str, Any]:
    if deadline_before and deadline_before.tzinfo is None:
        raise Conflict("deadline_before doit préciser un fuseau horaire.")
    latest = (
        select(func.max(ScreeningResult.id).label("id"))
        .group_by(ScreeningResult.case_id)
        .subquery()
    )
    statement = select(Opportunity, CompanyCase, ScreeningResult).outerjoin(
        CompanyCase,
        (CompanyCase.notice_id == Opportunity.notice_id) & (CompanyCase.company_id == company_id),
    )
    statement = statement.outerjoin(
        ScreeningResult,
        (ScreeningResult.case_id == CompanyCase.id) & ScreeningResult.id.in_(select(latest.c.id)),
    )
    if status:
        statement = statement.where(func.coalesce(CompanyCase.status, "new") == status)
    if recommendation:
        statement = statement.where(ScreeningResult.recommendation == recommendation)
    if min_score is not None:
        statement = statement.where(ScreeningResult.score >= min_score)
    if naics:
        statement = statement.where(Opportunity.naics_code == naics)
    if state:
        statement = statement.where(Opportunity.pop_state == state)
    if set_aside:
        statement = statement.where(Opportunity.set_aside_category == set_aside)
    if deadline_before:
        statement = statement.where(Opportunity.response_deadline_utc <= deadline_before)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(Opportunity.title.ilike(f"%{escaped}%", escape="\\"))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    ordering = (
        Opportunity.response_deadline_utc.asc().nullslast()
        if sort == "deadline"
        else ScreeningResult.score.desc().nullslast()
    )
    rows = session.execute(
        statement.order_by(
            *(
                [
                    sql_case(
                        (ScreeningResult.recommendation == "RECOMMENDED", 0),
                        (ScreeningResult.recommendation == "REVIEW", 1),
                        (ScreeningResult.recommendation.is_(None), 2),
                        else_=3,
                    )
                ]
                if sort == "score"
                else []
            ),
            ordering,
            Opportunity.notice_id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [serialize(opp, case, screening) for opp, case, screening in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
