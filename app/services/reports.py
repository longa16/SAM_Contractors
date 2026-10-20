import csv
import io
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from tempfile import SpooledTemporaryFile
from typing import Any

from sqlalchemy import String, and_, case, cast, extract, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import ApplicationMilestone, AuditLog, Company, CompanyCase, Opportunity, User
from app.errors import Conflict

STAGES = {
    "interested": "Vœu reçu",
    "examining": "Examen en cours",
    "preparing": "Préparation du dossier",
    "ready": "Prêt pour envoi",
    "submitted": "Envoyé / En suivi",
    "won": "Marché gagné",
    "lost": "Non retenue",
    "withdrawn": "Candidature arrêtée",
}


def bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    if start and end and start > end:
        raise Conflict("La date de début doit précéder la date de fin.")
    return (
        datetime.combine(start, time.min, tzinfo=UTC) if start else None,
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
        if end and end < date.max
        else None,
    )


def stage_expression() -> Any:
    return func.coalesce(
        CompanyCase.application_stage,
        case((CompanyCase.bid_decision == "bid", "preparing"), else_="interested"),
    )


def case_filters(company_id: int | None, start: date | None, end: date | None) -> list[Any]:
    lower, upper = bounds(start, end)
    filters = [or_(CompanyCase.application_stage.is_not(None), CompanyCase.bid_decision == "bid")]
    if company_id is not None:
        filters.append(CompanyCase.company_id == company_id)
    activity = func.coalesce(CompanyCase.application_updated_at, CompanyCase.updated_at)
    if lower:
        filters.append(activity >= lower)
    if upper:
        filters.append(activity < upper)
    return filters


def statistics(
    session: Session, company_id: int | None, start: date | None, end: date | None
) -> dict[str, Any]:
    filters = case_filters(company_id, start, end)
    stage = stage_expression()
    counts: dict[str, int] = dict(
        session.execute(select(stage, func.count()).where(*filters).group_by(stage)).all()
    )
    total = sum(counts.values())
    decided = counts.get("won", 0) + counts.get("lost", 0)
    outcome_rate = round(100 * counts.get("won", 0) / decided, 1) if decided else None
    lower, upper = bounds(start, end)
    events = [ApplicationMilestone.case_id == CompanyCase.id]
    if company_id is not None:
        events.append(CompanyCase.company_id == company_id)
    if lower:
        events.append(ApplicationMilestone.created_at >= lower)
    if upper:
        events.append(ApplicationMilestone.created_at < upper)
    year, month = (
        extract("year", ApplicationMilestone.created_at),
        extract("month", ApplicationMilestone.created_at),
    )
    monthly = session.execute(
        select(year, month, ApplicationMilestone.to_stage, func.count())
        .select_from(ApplicationMilestone)
        .join(CompanyCase, CompanyCase.id == ApplicationMilestone.case_id)
        .where(*events)
        .group_by(year, month, ApplicationMilestone.to_stage)
        .order_by(year, month)
    ).all()
    per_company = session.execute(
        select(Company.id, Company.name, func.count())
        .join(CompanyCase, CompanyCase.company_id == Company.id)
        .where(*filters)
        .group_by(Company.id, Company.name)
        .order_by(func.count().desc(), Company.name)
    ).all()
    return {
        "total": total,
        "stages": counts,
        "won": counts.get("won", 0),
        "lost": counts.get("lost", 0),
        "in_progress": sum(
            counts.get(s, 0) for s in ["interested", "examining", "preparing", "ready", "submitted"]
        ),
        "success_rate": outcome_rate,
        "decided": decided,
        "period_basis": "Dossiers dont la dernière activité est dans la période. "
        "Le taux est gagnés / (gagnés + non retenus).",
        "monthly_events": [
            {"month": f"{int(y):04d}-{int(m):02d}", "stage": s, "count": n}
            for y, m, s, n in monthly
        ],
        "companies": [{"id": i, "name": name, "count": n} for i, name, n in per_company],
    }


def audit_query(
    company_id: int | None, start: date | None, end: date | None, action: str | None
) -> Any:
    statement = select(AuditLog)
    if company_id is not None:
        statement = statement.where(
            or_(
                AuditLog.meta["company_id"].as_integer() == company_id,
                and_(AuditLog.entity_type == "company", AuditLog.entity_id == str(company_id)),
                and_(
                    AuditLog.entity_type == "company_case",
                    AuditLog.entity_id.in_(
                        select(cast(CompanyCase.id, String)).where(
                            CompanyCase.company_id == company_id
                        )
                    ),
                ),
                and_(
                    AuditLog.entity_type == "user",
                    AuditLog.entity_id.in_(
                        select(cast(User.id, String)).where(User.company_id == company_id)
                    ),
                ),
            )
        )
    lower, upper = bounds(start, end)
    if lower:
        statement = statement.where(AuditLog.ts_utc >= lower)
    if upper:
        statement = statement.where(AuditLog.ts_utc < upper)
    if action:
        statement = statement.where(AuditLog.action == action)
    return statement.order_by(AuditLog.id.desc())


def public_history_query(company_id: int, start: date | None, end: date | None) -> Any:
    statement = (
        select(ApplicationMilestone, Opportunity.title, CompanyCase.notice_id)
        .join(CompanyCase, CompanyCase.id == ApplicationMilestone.case_id)
        .join(Opportunity, Opportunity.notice_id == CompanyCase.notice_id)
    )
    statement = statement.where(CompanyCase.company_id == company_id)
    lower, upper = bounds(start, end)
    if lower:
        statement = statement.where(ApplicationMilestone.created_at >= lower)
    if upper:
        statement = statement.where(ApplicationMilestone.created_at < upper)
    return statement.order_by(ApplicationMilestone.id.desc())


def history(
    session: Session,
    company_id: int | None,
    start: date | None,
    end: date | None,
    *,
    admin: bool,
    page: int,
    action: str | None = None,
) -> dict[str, Any]:
    if admin:
        statement = audit_query(company_id, start, end, action)
        rows = session.scalars(statement.offset((page - 1) * 30).limit(31)).all()
        items = [
            {
                "id": r.id,
                "date": r.ts_utc.isoformat(),
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "actor": r.actor_id,
                "before": r.before,
                "after": r.after,
                "meta": r.meta,
            }
            for r in rows[:30]
        ]
    else:
        assert company_id is not None
        statement = public_history_query(company_id, start, end)
        rows_public = session.execute(statement.offset((page - 1) * 30).limit(31)).all()
        items = [
            {
                "id": r.id,
                "date": r.created_at.isoformat(),
                "action": STAGES[r.to_stage],
                "title": title,
                "notice_id": notice_id,
                "summary": r.summary,
            }
            for r, title, notice_id in rows_public[:30]
        ]
        rows = rows_public
    return {"items": items, "has_next": len(rows) > 30, "page": page}


def safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet programs must treat user-provided titles as text, never formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def csv_stream(headers: list[str], rows: Iterable[list[Any]]) -> Iterator[bytes]:
    binary = SpooledTemporaryFile(max_size=1024 * 1024)
    writer_stream = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
    try:
        writer = csv.writer(writer_stream)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([safe_cell(value) for value in row])
        writer_stream.flush()
        writer_stream.detach()
        binary.seek(0)
    except Exception:
        writer_stream.close()
        binary.close()
        raise

    def chunks() -> Iterator[bytes]:
        try:
            while chunk := binary.read(65536):
                yield chunk
        finally:
            binary.close()

    return chunks()


def applications_csv(
    session: Session, company_id: int | None, start: date | None, end: date | None
) -> Iterator[bytes]:
    statement = (
        select(CompanyCase, Opportunity, Company.name)
        .join(Opportunity, Opportunity.notice_id == CompanyCase.notice_id)
        .join(Company, Company.id == CompanyCase.company_id)
    )
    statement = statement.where(*case_filters(company_id, start, end)).order_by(CompanyCase.id)

    def rows() -> Iterator[list[Any]]:
        for case_row, opp, company_name in session.execute(statement).yield_per(200):
            stage = case_row.application_stage or "preparing"
            yield [
                company_name,
                opp.notice_id,
                opp.title,
                STAGES[stage],
                opp.response_deadline_utc.isoformat() if opp.response_deadline_utc else "",
                case_row.application_submitted_at.isoformat()
                if case_row.application_submitted_at
                else "",
                (case_row.application_updated_at or case_row.updated_at).isoformat(),
            ]

    return csv_stream(
        [
            "Entreprise",
            "Notice SAM",
            "Appel d’offres",
            "Étape actuelle",
            "Échéance UTC",
            "Envoi déclaré UTC",
            "Dernière activité UTC",
        ],
        rows(),
    )


def history_csv(
    session: Session, company_id: int | None, start: date | None, end: date | None, *, admin: bool
) -> Iterator[bytes]:
    def rows() -> Iterator[list[Any]]:
        if admin:
            for record in session.scalars(audit_query(company_id, start, end, None)).yield_per(200):
                yield [
                    record.ts_utc.isoformat(),
                    record.action,
                    record.entity_type,
                    record.entity_id,
                    record.actor_id,
                ]
        else:
            assert company_id is not None
            for record, title, notice in session.execute(
                public_history_query(company_id, start, end)
            ).yield_per(200):
                yield [
                    record.created_at.isoformat(),
                    STAGES[record.to_stage],
                    title,
                    notice,
                    record.summary,
                ]

    return csv_stream(
        ["Date UTC", "Action", "Type / offre", "Identifiant", "Acteur / compte rendu"], rows()
    )
