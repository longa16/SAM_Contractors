from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import InternalUser, SessionDep
from app.services import reports
from app.services.audit import audit
from app.services.cases import catalog_company_id

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/statistics")
def statistics(
    session: SessionDep,
    actor: InternalUser,
    company_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    company = catalog_company_id(session, actor, company_id)
    return reports.statistics(session, company, start, end)


@router.get("/history")
def history(
    session: SessionDep,
    actor: InternalUser,
    company_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    action: Annotated[str | None, Query(max_length=100)] = None,
) -> dict[str, Any]:
    company = catalog_company_id(session, actor, company_id)
    return reports.history(
        session, company, start, end, admin=actor.role == "admin", page=page, action=action
    )


@router.get("/export/{kind}")
def export(
    kind: Literal["applications", "history"],
    session: SessionDep,
    actor: InternalUser,
    company_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> StreamingResponse:
    company = catalog_company_id(session, actor, company_id)
    chunks = (
        reports.applications_csv(session, company, start, end)
        if kind == "applications"
        else reports.history_csv(session, company, start, end, admin=actor.role == "admin")
    )
    audit(
        session,
        "report.exported",
        "company" if company else "platform",
        str(company or "all"),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": company, "kind": kind},
    )
    session.commit()
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )
