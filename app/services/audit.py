from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def audit(
    session: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    *,
    actor_type: str = "system",
    actor_id: str = "system",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            meta=meta,
        )
    )
