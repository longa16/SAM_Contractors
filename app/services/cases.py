from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.opportunity_analyst import triage
from app.db.models import Company, CompanyCase, Opportunity, ScreeningResult, User
from app.errors import Conflict, Forbidden, InvalidTransition, NotFound
from app.schemas.company import CompanyProfile
from app.services.audit import audit


def authorize_company(session: Session, actor: User, company_id: int | None) -> Company:
    selected = company_id if actor.role == "admin" else actor.company_id
    if actor.role == "subcontractor":
        raise Forbidden("Accès réservé aux équipes clientes.")
    if actor.role != "admin" and company_id is not None and company_id != actor.company_id:
        raise NotFound("Entreprise introuvable.")
    if selected is None:
        raise Conflict("Choisissez une entreprise pour accéder à son espace privé.")
    company = session.get(Company, selected)
    if not company or not company.is_active:
        raise NotFound("Entreprise introuvable.")
    return company


def get_case(
    session: Session, company_id: int, notice_id: str, *, create: bool = False
) -> CompanyCase:
    opp = session.get(Opportunity, notice_id)
    if not opp:
        raise NotFound("Opportunité introuvable.")
    case = session.scalar(
        select(CompanyCase).where(
            CompanyCase.company_id == company_id, CompanyCase.notice_id == notice_id
        )
    )
    if not case and create:
        case = CompanyCase(company_id=company_id, notice_id=notice_id)
        session.add(case)
        session.flush()
        audit(
            session, "case.created", "company_case", str(case.id), meta={"company_id": company_id}
        )
    if not case:
        raise NotFound("Dossier introuvable.")
    return case


class CompanyStateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def transition(
        self,
        case: CompanyCase,
        target: str,
        *,
        actor_id: str,
        role: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        opp = self.session.get_one(Opportunity, case.notice_id)
        if target in {"bid", "no-bid"}:
            if role != "admin":
                raise Forbidden("Seul l’administrateur peut valider une candidature.")
            if opp.response_deadline_utc and opp.response_deadline_utc < now:
                raise InvalidTransition("La date limite est dépassée.")
            if case.analysis_status == "stale_profile":
                raise InvalidTransition("Le profil a changé ; relancez le screening.")
            if target == "bid":
                company = self.session.get_one(Company, case.company_id)
                if (
                    not company.profile
                    or triage(opp, CompanyProfile.model_validate(company.profile), now).hard_blocked
                ):
                    raise InvalidTransition(
                        "L’avis ne respecte plus les critères obligatoires de l’entreprise."
                    )
            latest = self.session.scalar(
                select(ScreeningResult)
                .where(ScreeningResult.case_id == case.id)
                .order_by(ScreeningResult.id.desc())
                .limit(1)
            )
            if target == "bid" and latest and latest.data.get("hard_blocked"):
                raise InvalidTransition(
                    "Éligibilité bloquée : vérifiez les qualifications de l’entreprise."
                )
        allowed = {
            "new": {"screening", "expired"},
            "screening": {"bid", "no-bid", "expired"},
            "bid": {"expired"},
            "no-bid": {"expired"},
        }
        if target not in allowed.get(case.status, set()):
            raise InvalidTransition("Transition non autorisée.")
        if target == "no-bid" and not (reason or "").strip():
            raise InvalidTransition("Un motif est requis pour No-Bid.")
        before = {"status": case.status}
        values: dict[str, object] = {
            "status": target,
            "status_changed_by": actor_id,
            "status_changed_at": now,
            "updated_at": now,
        }
        if target in {"bid", "no-bid"}:
            values.update(bid_decision=target.replace("-", "_"), bid_decision_reason=reason)
        changed = self.session.execute(
            update(CompanyCase)
            .where(CompanyCase.id == case.id, CompanyCase.status == before["status"])
            .values(**values)
        )
        if changed.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidTransition("Le dossier a déjà changé ; actualisez la fiche.")
        audit(
            self.session,
            "case.transition",
            "company_case",
            str(case.id),
            actor_type="system" if role == "system" else "user",
            actor_id=actor_id,
            before=before,
            after={"status": target},
            meta={"company_id": case.company_id, "reason": reason},
        )


def catalog_company_id(session: Session, actor: User, company_id: int | None) -> int | None:
    """Platform administrators can read SAM data without selecting a private workspace."""
    if actor.role == "admin" and company_id is None:
        return None
    return authorize_company(session, actor, company_id).id
