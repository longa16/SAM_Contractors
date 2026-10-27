import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.opportunity_analyst import triage
from app.config import Settings
from app.db.base import session_factory
from app.db.models import AgentReview, Company, CompanyCase, Opportunity, ScreeningResult
from app.errors import ConfigurationError, Conflict, NotFound
from app.schemas.agent_reviews import ExaminationReport, ReviewDecision
from app.schemas.company import CompanyProfile
from app.services.audit import audit
from app.services.cases import CompanyStateService
from app.services.review_context import build_snapshot, context_hash, context_manifest

log = logging.getLogger(__name__)

ERRORS = {
    "rate_limited": "Le quota ou la limite de débit du fournisseur LLM est atteint. "
    "Réessayez plus tard.",
    "provider_auth": "Le fournisseur LLM refuse l’accès. Vérifiez la clé et les droits du serveur.",
    "model_unavailable": "Le modèle configuré n’est pas accessible. "
    "Vérifiez HERMES_MODEL sur le serveur.",
    "runtime_error": "Hermes n’a pas terminé l’examen. "
    "Vérifiez le moteur et le fournisseur, puis relancez.",
    "timeout": "Le délai d’examen est dépassé. Vous pouvez lancer un nouvel examen.",
    "invalid_report": "Le rapport est incomplet ou ses citations ne sont pas vérifiables. "
    "Relancez l’examen.",
    "interrupted": "L’exécution a été interrompue. Vous pouvez lancer un nouvel examen.",
}


def provider_key(settings: Settings) -> SecretStr | None:
    return settings.hermes_api_key or (
        settings.groq_api_key
        if settings.hermes_base_url.rstrip("/") == "https://api.groq.com/openai/v1"
        else None
    )


def configuration_status(settings: Settings) -> dict[str, Any]:
    key = provider_key(settings)
    if not settings.hermes_enabled:
        code, message = (
            "disabled",
            "Hermes est désactivé. Ajoutez HERMES_ENABLED=true dans .env, "
            "puis redémarrez le serveur.",
        )
    elif not settings.hermes_python.is_file():
        code, message = (
            "runtime_missing",
            "Le Python Hermes est introuvable. "
            "Exécutez uv run python -m scripts.install_hermes, puis redémarrez le serveur. "
            "Vérifiez HERMES_PYTHON si vous avez défini un chemin personnalisé.",
        )
    elif key is None or not key.get_secret_value().strip():
        code, message = (
            "api_key_missing",
            "La clé du fournisseur est manquante. Configurez GROQ_API_KEY pour Groq "
            "ou HERMES_API_KEY pour un autre fournisseur, puis redémarrez le serveur.",
        )
    else:
        code, message = "ready", "Hermes est configuré."
    return {"ready": code == "ready", "code": code, "message": message}


def configured(settings: Settings) -> bool:
    return bool(configuration_status(settings)["ready"])


def require_examination(session: Session, case: CompanyCase) -> None:
    if case.application_stage != "examining":
        raise Conflict("Acceptez la demande ou initiez la candidature avant de lancer l’examen.")
    company = session.get_one(Company, case.company_id)
    opp = session.get_one(Opportunity, case.notice_id)
    if not company.is_active or not company.profile:
        raise Conflict("L’entreprise doit être active et son profil renseigné.")
    if not opp.active or (
        opp.response_deadline_utc and opp.response_deadline_utc < datetime.now(UTC)
    ):
        raise Conflict("Cette offre est inactive ou expirée.")


def enqueue(session: Session, case: CompanyCase, actor_id: int, settings: Settings) -> AgentReview:
    if not configured(settings):
        raise ConfigurationError(str(configuration_status(settings)["message"]))
    # The outer write also makes the nested unique-constraint insert atomic on SQLite.
    session.execute(
        update(CompanyCase)
        .where(CompanyCase.id == case.id)
        .values(updated_at=CompanyCase.updated_at)
    )
    session.refresh(case)
    require_examination(session, case)
    active = session.scalar(select(AgentReview).where(AgentReview.active_case_id == case.id))
    if active:
        return active
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        session.scalar(
            select(func.count()).select_from(AgentReview).where(AgentReview.created_at >= today)
        )
        or 0
    )
    if count >= settings.hermes_daily_run_limit:
        raise Conflict("Le nombre maximal d’examens Hermes pour la journée est atteint.")
    manifest = context_manifest(session, case)
    row = AgentReview(
        case_id=case.id,
        active_case_id=case.id,
        requested_by=actor_id,
        input_hash=context_hash(manifest),
        snapshot=build_snapshot(session, case, manifest),
        model=settings.hermes_model,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        active = session.scalar(select(AgentReview).where(AgentReview.active_case_id == case.id))
        if active:
            return active
        raise Conflict("Une autre exécution a modifié ce dossier. Actualisez.") from None
    audit(
        session,
        "agent.examination_queued",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor_id),
        meta={"company_id": case.company_id, "run_id": row.id},
    )
    return row


def get_run(session: Session, case: CompanyCase, run_id: int) -> AgentReview:
    row = session.get(AgentReview, run_id)
    if not row or row.case_id != case.id:
        raise NotFound("Examen introuvable pour cette candidature.")
    return row


def serialize_run(row: AgentReview, current_hash: str) -> dict[str, Any]:
    warnings = list(row.snapshot.get("warnings", []))
    if row.status == "succeeded":
        read = {
            call.get("source_id")
            for call in row.tool_calls or []
            if call.get("name") == "read_source"
        }
        unread = [source["title"] for source in row.snapshot["sources"] if source["id"] not in read]
        if unread:
            warnings.append(
                f"{len(unread)} source(s) non consultée(s) par Hermes : " + "; ".join(unread[:10])
            )
    return {
        "id": row.id,
        "status": row.status,
        "model": row.model,
        "stale": row.input_hash != current_hash,
        "report": row.report,
        "tool_calls": row.tool_calls or [],
        "error": ERRORS.get(row.error_code or ""),
        "warnings": warnings,
        "sources": [{"id": s["id"], "title": s["title"]} for s in row.snapshot["sources"]],
        "review_decision": row.review_decision,
        "review_note": row.review_note,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def cancel(session: Session, case: CompanyCase, row: AgentReview, actor_id: int) -> None:
    changed = session.execute(
        update(AgentReview)
        .where(AgentReview.id == row.id, AgentReview.status.in_(["queued", "running"]))
        .values(
            status="cancelled", active_case_id=None, lease_until=None, finished_at=datetime.now(UTC)
        )
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Cette exécution est déjà terminée. Actualisez.")
    audit(
        session,
        "agent.examination_cancelled",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor_id),
        meta={"company_id": case.company_id, "run_id": row.id},
    )


def decide(
    session: Session, case: CompanyCase, row: AgentReview, actor_id: int, payload: ReviewDecision
) -> None:
    session.execute(
        update(CompanyCase)
        .where(CompanyCase.id == case.id)
        .values(updated_at=CompanyCase.updated_at)
    )
    session.refresh(case)
    require_examination(session, case)
    if row.status != "succeeded" or row.input_hash != context_hash(context_manifest(session, case)):
        raise Conflict("Un rapport terminé et à jour est nécessaire pour cette validation.")
    changed = session.execute(
        update(AgentReview)
        .where(AgentReview.id == row.id, AgentReview.review_decision.is_(None))
        .values(
            review_decision=payload.decision,
            review_note=payload.note,
            reviewed_by=actor_id,
            reviewed_at=datetime.now(UTC),
        )
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Ce rapport a déjà été contrôlé. Lancez un nouvel examen pour le reprendre.")
    if payload.decision == "accepted":
        # Refresh objective eligibility locally; the agent never writes a Bid or a pipeline stage.
        company = session.get_one(Company, case.company_id)
        result = triage(
            session.get_one(Opportunity, case.notice_id),
            CompanyProfile.model_validate(company.profile),
            datetime.now(UTC),
        )
        session.add(
            ScreeningResult(
                case_id=case.id,
                stage=1,
                score=result.score,
                recommendation=result.recommendation,
                data=result.model_dump(mode="json"),
                model="local-after-hermes-review",
                prompt_version="examination.v1",
                input_hash=row.input_hash,
            )
        )
        if case.status == "new":
            CompanyStateService(session).transition(
                case, "screening", actor_id=str(actor_id), role="admin"
            )
        case.analysis_status, case.screening_stage = "complete", "stage1"
    audit(
        session,
        "agent.examination_reviewed",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor_id),
        meta={"company_id": case.company_id, "run_id": row.id, "decision": payload.decision},
    )


def validate_result(
    snapshot: dict[str, Any], result: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(json.dumps(result)) > 200000:
        raise ValueError("Oversize result")
    report = ExaminationReport.model_validate(result["report"])
    sources = {s["id"]: s["text"] for s in snapshot["sources"]}
    calls = result.get("tool_calls", [])
    if not isinstance(calls, list) or not 1 <= len(calls) <= 64:
        raise ValueError("Missing tool trace")
    read = set()
    trace = []
    for call in calls:
        if call.get("name") not in {"read_source", "search_sources"}:
            raise ValueError("Unexpected tool")
        source_id = call.get("source_id")
        if call["name"] == "read_source":
            if source_id not in sources:
                raise ValueError("Unknown source")
            read.add(source_id)
        trace.append(
            {"name": call["name"], "source_id": source_id if source_id in sources else None}
        )
    citations = [c for item in report.requirements for c in item.citations]
    citations.extend(c for finding in report.eligibility for c in finding.citations)
    required_sources = {
        "opportunity",
        "company_profile",
        "company_statement",
        "contractor_inventory",
    }.intersection(sources)
    if not required_sources.issubset(read):
        raise ValueError(
            "The offer and contractor context must be read before a report is accepted"
        )
    if not citations:
        raise ValueError("At least one verifiable citation is required")
    for citation in citations:
        if citation.source_id not in read or " ".join(citation.quote.split()) not in " ".join(
            sources[citation.source_id].split()
        ):
            raise ValueError("Unverifiable citation")
    return report.model_dump(mode="json"), trace


def recover_expired(session: Session) -> None:
    session.execute(
        update(AgentReview)
        .where(AgentReview.status == "running", AgentReview.lease_until < datetime.now(UTC))
        .values(
            status="failed",
            active_case_id=None,
            error_code="interrupted",
            lease_until=None,
            finished_at=datetime.now(UTC),
        )
    )


def claim(engine: Engine, settings: Settings) -> int | None:
    with session_factory(engine).begin() as session:
        recover_expired(session)
        row = session.scalar(
            select(AgentReview)
            .where(AgentReview.status == "queued")
            .order_by(AgentReview.id)
            .limit(1)
        )
        if row is None:
            return None
        now = datetime.now(UTC)
        changed = session.execute(
            update(AgentReview)
            .where(AgentReview.id == row.id, AgentReview.status == "queued")
            .values(
                status="running",
                started_at=now,
                lease_until=now + timedelta(seconds=settings.hermes_timeout_seconds + 30),
            )
        )
        return row.id if changed.rowcount == 1 else None  # type: ignore[attr-defined]


def finish(engine: Engine, run_id: int, result: dict[str, Any] | None, error: str | None) -> None:
    with session_factory(engine).begin() as session:
        row = session.get_one(AgentReview, run_id)
        report, calls = None, None
        if result is not None and not error:
            try:
                report, calls = validate_result(row.snapshot, result)
            except (ValidationError, ValueError, KeyError, TypeError, AttributeError):
                error = "invalid_report"
        error = error if error in ERRORS else ("runtime_error" if report is None else None)
        changed = session.execute(
            update(AgentReview)
            .where(AgentReview.id == run_id, AgentReview.status == "running")
            .values(
                status="failed" if error else "succeeded",
                report=report,
                tool_calls=calls,
                error_code=error,
                active_case_id=None,
                lease_until=None,
                finished_at=datetime.now(UTC),
            )
        )
        if changed.rowcount == 1:  # type: ignore[attr-defined]
            case = session.get_one(CompanyCase, row.case_id)
            audit(
                session,
                "agent.examination_finished",
                "company_case",
                str(case.id),
                actor_type="agent",
                actor_id="hermes",
                meta={
                    "company_id": case.company_id,
                    "run_id": row.id,
                    "status": "failed" if error else "succeeded",
                },
            )

            log.info(
                "hermes_examination_finished",
                extra={
                    "fields": {
                        "run_id": run_id,
                        "status": "failed" if error else "succeeded",
                        "error_code": error,
                    }
                },
            )
