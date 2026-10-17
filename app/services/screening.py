import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.opportunity_analyst import merge_analysis, triage
from app.config import Settings
from app.db.base import session_factory
from app.db.models import (
    AgentRun,
    Company,
    CompanyCase,
    LLMUsage,
    Opportunity,
    ScreeningLock,
    ScreeningResult,
)
from app.errors import Conflict, DomainError, LLMOutputError
from app.llm.client import LLMClient
from app.llm.structured import PROMPT_VERSION, structured_call
from app.schemas.company import CompanyProfile
from app.schemas.screening import ScreeningLLMOutput, ScreeningOutput
from app.services.audit import audit
from app.services.cases import CompanyStateService, get_case
from app.services.descriptions import fetch_description

log = logging.getLogger(__name__)


def fingerprint(opp: Opportunity, profile: CompanyProfile, model: str, now: datetime) -> str:
    payload = {
        "notice_id": opp.notice_id,
        "raw": {k: v for k, v in opp.raw_payload.items() if k != "_synced_at"},
        "profile": profile.model_dump(),
        "model": model,
        "prompt": PROMPT_VERSION,
        "scoring": "triage.v1",
        "day": now.date().isoformat(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ScreeningService:
    def __init__(self, engine: Engine, settings: Settings, llm: LLMClient) -> None:
        self.engine, self.settings, self.llm = engine, settings, llm
        self.factory = session_factory(engine)

    def run(self, company_id: int, notice_id: str, *, force: bool = False) -> ScreeningOutput:
        owner = uuid4().hex
        case_id: int | None = None
        now = datetime.now(UTC)
        try:
            with self.factory.begin() as session:
                company = session.get_one(Company, company_id)
                if not company.is_active or not company.profile:
                    raise Conflict(
                        "Renseignez et validez le profil de l’entreprise avant le screening."
                    )
                profile = CompanyProfile.model_validate(company.profile)
                case = get_case(session, company_id, notice_id, create=True)
                case_id = case.id
                opp = session.get_one(Opportunity, notice_id)
                key = fingerprint(opp, profile, self.settings.llm_model, now)
                previous = session.scalar(
                    select(ScreeningResult)
                    .where(ScreeningResult.case_id == case_id, ScreeningResult.input_hash == key)
                    .order_by(ScreeningResult.id.desc())
                    .limit(1)
                )
                if previous and not force:
                    return ScreeningOutput.model_validate(previous.data)
                session.execute(
                    delete(ScreeningLock).where(
                        ScreeningLock.case_id == case_id, ScreeningLock.expires_at < now
                    )
                )
                session.add(
                    ScreeningLock(
                        case_id=case_id, owner=owner, expires_at=now + timedelta(minutes=10)
                    )
                )
                session.flush()
                result = triage(opp, profile, now)
                self._persist(session, case, result, key, "local", now)
                if opp.response_deadline_utc and opp.response_deadline_utc < now:
                    if case.status in {"new", "screening", "bid", "no-bid"}:
                        CompanyStateService(session).transition(
                            case, "expired", actor_id="system", role="system"
                        )
                elif case.status == "new" and result.recommendation != "PASS":
                    CompanyStateService(session).transition(
                        case, "screening", actor_id="opportunity_analyst", role="system"
                    )
                case.screening_stage, case.analysis_status = "stage1", "triaged"
                description, description_url = opp.description_text, opp.description_url
            if result.hard_blocked or result.score < profile.screening.stage1_threshold:
                return result
            usage_id = self._reserve(company_id, case_id, profile)
            if usage_id is None:
                self._flag(case_id, "daily_cap")
                return result
            start = time.monotonic()
            with self.factory.begin() as session:
                run = AgentRun(
                    agent_name="opportunity_analyst",
                    entity_id=f"{company_id}:{case_id}",
                    input_hash=key,
                    status="running",
                    model=self.settings.llm_model,
                    prompt_version=PROMPT_VERSION,
                )
                session.add(run)
                session.flush()
                run_id = run.id
            try:
                if not description:
                    description = fetch_description(description_url, self.settings)
                    with self.factory.begin() as session:
                        opp = session.get_one(Opportunity, notice_id)
                        opp.description_text, opp.description_fetched_at = (
                            description,
                            datetime.now(UTC),
                        )
                        audit(session, "description.fetched", "opportunity", notice_id)
                analysis, tokens_in, tokens_out = structured_call(
                    self.llm,
                    ScreeningLLMOutput,
                    description,
                    max_tokens=self.settings.llm_max_tokens,
                )
                if any(
                    not factor.evidence or factor.evidence not in description
                    for factor in analysis.positive_factors + analysis.blocking_factors
                ):
                    raise LLMOutputError("Facteur IA sans preuve vérifiable dans la description.")
                merged = merge_analysis(result, analysis, profile)
                with self.factory.begin() as session:
                    case = session.get_one(CompanyCase, case_id)
                    # Verify ownership before publishing a late result from an expired worker.
                    lock = session.get(ScreeningLock, case_id)
                    if not lock or lock.owner != owner or lock.expires_at < datetime.now(UTC):
                        raise Conflict("L’analyse a expiré ; relancez-la.")
                    self._persist(session, case, merged, key, self.settings.llm_model, now)
                    case.screening_stage, case.analysis_status = "stage2", "complete"
                    if case.status == "new" and merged.recommendation != "PASS":
                        CompanyStateService(session).transition(
                            case, "screening", actor_id="opportunity_analyst", role="system"
                        )
                    run = session.get_one(AgentRun, run_id)
                    run.status, run.output, run.finished_at = (
                        "success",
                        merged.model_dump(),
                        datetime.now(UTC),
                    )
                    run.tokens_in, run.tokens_out = tokens_in, tokens_out
                    run.latency_ms = int((time.monotonic() - start) * 1000)
                    usage = session.get_one(LLMUsage, usage_id)
                    usage.tokens_in, usage.tokens_out = tokens_in, tokens_out
                return merged
            except Exception as error:
                code = error.code if isinstance(error, DomainError) else type(error).__name__
                with self.factory.begin() as session:
                    case = session.get_one(CompanyCase, case_id)
                    case.analysis_status = (
                        "needs_description" if code == "sam_api_error" else "failed"
                    )
                    run = session.get_one(AgentRun, run_id)
                    run.status, run.error, run.finished_at = "failed", code, datetime.now(UTC)
                    run.latency_ms = int((time.monotonic() - start) * 1000)
                    audit(
                        session,
                        "screening.failed",
                        "company_case",
                        str(case_id),
                        actor_type="agent",
                        actor_id="opportunity_analyst",
                        meta={"company_id": company_id, "error": code},
                    )
                log.warning(
                    "screening_deferred",
                    extra={
                        "fields": {"company_id": company_id, "case_id": case_id, "error_type": code}
                    },
                )
                return result
        except IntegrityError:
            raise Conflict("Une analyse de ce dossier est déjà en cours.") from None
        finally:
            if case_id is not None:
                with self.factory.begin() as session:
                    session.execute(
                        delete(ScreeningLock).where(
                            ScreeningLock.case_id == case_id, ScreeningLock.owner == owner
                        )
                    )

    def _persist(
        self,
        session: Session,
        case: CompanyCase,
        result: ScreeningOutput,
        key: str,
        model: str,
        now: datetime,
    ) -> None:
        session.add(
            ScreeningResult(
                case_id=case.id,
                stage=result.stage,
                score=result.score,
                recommendation=result.recommendation,
                data=result.model_dump(),
                model=model,
                prompt_version=PROMPT_VERSION,
                input_hash=key,
            )
        )
        if result.stage == 1:
            session.add(
                AgentRun(
                    agent_name="opportunity_triage",
                    entity_id=f"{case.company_id}:{case.id}",
                    input_hash=key,
                    status="success",
                    model="local",
                    prompt_version="triage.v1",
                    output=result.model_dump(),
                    started_at=now,
                    finished_at=now,
                )
            )
        audit(
            session,
            "screening.completed",
            "company_case",
            str(case.id),
            actor_type="agent",
            actor_id="opportunity_analyst",
            after={"stage": result.stage, "score": result.score},
            meta={"company_id": case.company_id},
        )

    def _reserve(self, company_id: int, case_id: int, profile: CompanyProfile) -> int | None:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.factory.begin() as session:
            # All requests serialize on the same database row, including across companies/workers.
            # The oldest company is a stable lock row; companies cannot be deleted by this API.
            first_company = session.scalar(select(Company.id).order_by(Company.id).limit(1))
            session.execute(
                update(Company).where(Company.id == first_company).values(name=Company.name)
            )
            global_count = (
                session.scalar(
                    select(func.count())
                    .select_from(LLMUsage)
                    .where(LLMUsage.created_at >= midnight)
                )
                or 0
            )
            company_count = (
                session.scalar(
                    select(func.count())
                    .select_from(LLMUsage)
                    .where(LLMUsage.created_at >= midnight, LLMUsage.company_id == company_id)
                )
                or 0
            )
            used = (
                session.scalar(
                    select(func.coalesce(func.sum(LLMUsage.reserved_tokens), 0)).where(
                        LLMUsage.created_at >= midnight
                    )
                )
                or 0
            )
            # Conservative byte-bound reservation includes repair and provider retries.
            reservation = (
                (self.settings.max_desc_chars * 4 + 20000 + self.settings.llm_max_tokens)
                * 2
                * self.settings.llm_retry_attempts
            )
            if (
                global_count >= self.settings.screening_stage2_daily_cap
                or company_count >= profile.screening.stage2_daily_cap
                or used + reservation > self.settings.llm_daily_token_budget
            ):
                audit(
                    session,
                    "screening.quota_deferred",
                    "company_case",
                    str(case_id),
                    meta={"company_id": company_id},
                )
                return None
            usage = LLMUsage(company_id=company_id, case_id=case_id, reserved_tokens=reservation)
            session.add(usage)
            session.flush()
            return usage.id

    def _flag(self, case_id: int, status: str) -> None:
        with self.factory.begin() as session:
            case = session.get_one(CompanyCase, case_id)
            case.analysis_status = status
            audit(
                session,
                "screening.deferred",
                "company_case",
                str(case_id),
                meta={"company_id": case.company_id, "status": status},
            )

    def batch(self) -> int:
        count = 0
        with self.factory() as session:
            companies = session.scalars(select(Company).where(Company.is_active.is_(True))).all()
            opportunities = session.scalars(
                select(Opportunity).where(Opportunity.active.is_(True))
            ).all()
            for company in companies:
                if not company.profile:
                    continue
                profile = CompanyProfile.model_validate(company.profile)
                ranked = sorted(
                    opportunities,
                    key=lambda opp: (-triage(opp, profile, datetime.now(UTC)).score, opp.notice_id),
                )
                for opp in ranked:
                    self.run(company.id, opp.notice_id)
                    count += 1
        return count
