from datetime import datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.db.models import CompanyCase, Opportunity, OpportunityState
from app.services.audit import audit
from app.services.cases import CompanyStateService
from app.services.state import OpportunityStateService


def cleanup_expired(session: Session, now: datetime) -> int:
    """Delete expired catalogue entries without destroying candidature history."""
    expired = select(Opportunity.notice_id).where(Opportunity.response_deadline_utc < now)
    states = OpportunityStateService(session)
    for notice_id in session.scalars(
        select(OpportunityState.notice_id).where(
            OpportunityState.notice_id.in_(expired),
            OpportunityState.status.in_(["new", "screening", "bid", "no-bid"]),
        )
    ).all():
        states.expire(notice_id, now)
    for case in session.scalars(
        select(CompanyCase).where(
            CompanyCase.notice_id.in_(expired),
            CompanyCase.status.in_(["new", "screening", "bid", "no-bid"]),
        )
    ).all():
        CompanyStateService(session).transition(
            case, "expired", actor_id="system", role="system", now=now
        )
    unlinked = expired.where(~exists().where(CompanyCase.notice_id == Opportunity.notice_id))
    session.execute(delete(OpportunityState).where(OpportunityState.notice_id.in_(unlinked)))
    result = session.execute(delete(Opportunity).where(Opportunity.notice_id.in_(unlinked)))
    count: int = result.rowcount  # type: ignore[attr-defined]
    if count:
        audit(session, "catalog.expired_purged", "catalog", "sam", after={"deleted": count})
    return count
