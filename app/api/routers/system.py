import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import func, select

from app.api.deps import Admin, InternalUser, SessionDep
from app.db.models import AuditLog, CompanyCase
from app.sam.sync_service import run_sync, sync_status
from app.services.audit import audit
from app.services.cases import authorize_company

router = APIRouter(prefix="/api", tags=["system"])
log = logging.getLogger(__name__)


@router.get("/sync/status")
def status(
    request: Request, session: SessionDep, actor: InternalUser, company_id: int | None = None
) -> dict[str, Any]:
    company = authorize_company(session, actor, company_id)
    data = sync_status(request.app.state.engine)
    stages = session.execute(
        select(CompanyCase.screening_stage, func.count())
        .where(CompanyCase.company_id == company.id)
        .group_by(CompanyCase.screening_stage)
    ).all()
    errors = (
        session.scalar(
            select(func.count())
            .select_from(CompanyCase)
            .where(
                CompanyCase.company_id == company.id,
                CompanyCase.analysis_status.in_(["failed", "needs_description"]),
            )
        )
        or 0
    )
    return {**data, "stages": dict(stages), "analysis_errors": errors}


@router.post("/sync/run", status_code=202)
def sync(
    request: Request, session: SessionDep, actor: Admin, tasks: BackgroundTasks
) -> dict[str, str]:
    audit(session, "sync.requested", "sync", "catalog", actor_type="user", actor_id=str(actor.id))
    session.commit()

    def execute() -> None:
        try:
            run_sync(request.app.state.settings, engine=request.app.state.engine)
            request.app.state.screening.batch()
        except Exception:
            log.error("requested_sync_failed")

    tasks.add_task(execute)
    return {"status": "accepted"}


@router.get("/audit")
def audit_log(
    session: SessionDep,
    actor: Admin,
    entity_type: str | None = None,
    entity_id: str | None = None,
    company_id: int | None = None,
    page: int = 1,
) -> dict[str, Any]:
    from app.errors import Conflict

    if page < 1:
        raise Conflict("Page invalide.")
    statement = select(AuditLog)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditLog.entity_id == entity_id)
    if company_id:
        statement = statement.where(AuditLog.meta["company_id"].as_integer() == company_id)
    rows = session.scalars(
        statement.order_by(AuditLog.id.desc()).offset((page - 1) * 50).limit(50)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "ts_utc": r.ts_utc.isoformat(),
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "before": r.before,
                "after": r.after,
                "meta": r.meta,
            }
            for r in rows
        ],
        "page": page,
    }
