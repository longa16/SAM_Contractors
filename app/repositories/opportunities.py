from sqlalchemy.orm import Session

from app.db.models import Opportunity, OpportunityState
from app.schemas.sam import NormalizedOpportunity


def upsert(session: Session, record: NormalizedOpportunity) -> str:
    existing = session.get(Opportunity, record.notice_id)
    if existing is None:
        session.add(Opportunity(**record.model_dump(), first_seen_at=record.synced_at))
        session.flush()
        session.add(OpportunityState(notice_id=record.notice_id))
        return "inserted"
    if record.posted_date < existing.posted_date:
        return "skipped_duplicate"
    # Enrichment is cached independently, and invalidated only when the SAM source changes.
    if existing.description_url != record.description_url:
        existing.description_text = None
        existing.description_fetched_at = None
    for key, value in record.model_dump().items():
        setattr(existing, key, value)
    return "updated"
