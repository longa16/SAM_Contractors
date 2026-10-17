from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import OpportunityState
from app.errors import InvalidTransition
from app.services.audit import audit


class OpportunityStateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def expire(self, notice_id: str, now: datetime) -> bool:
        state = self.session.get(OpportunityState, notice_id)
        if state is None or state.status not in {"new", "screening", "bid", "no-bid"}:
            return False
        before = {"status": state.status}
        state.status = "expired"
        state.status_changed_at = state.updated_at = now
        state.status_changed_by = "system"
        audit(
            self.session,
            "opportunity.expired",
            "opportunity",
            notice_id,
            before=before,
            after={"status": "expired"},
        )
        return True

    def transition(
        self,
        notice_id: str,
        target: str,
        actor: str,
        role: str,
        now: datetime,
        reason: str | None = None,
    ) -> None:
        state = self.session.get(OpportunityState, notice_id)
        if state is None:
            raise InvalidTransition("Opportunité introuvable.")
        allowed = {"new": {"screening"}, "screening": {"bid", "no-bid"}}
        if target not in allowed.get(state.status, set()):
            raise InvalidTransition("Transition non autorisée.")
        if target in {"bid", "no-bid"} and role not in {"admin", "business_developer"}:
            raise InvalidTransition("Rôle non autorisé.")
        if target == "no-bid" and not (reason or "").strip():
            raise InvalidTransition("Un motif est requis.")
        before = {"status": state.status}
        state.status = target
        state.status_changed_at = state.updated_at = now
        state.status_changed_by = actor
        if target in {"bid", "no-bid"}:
            state.bid_decision = target.replace("-", "_")
            state.bid_decision_reason = reason
        audit(
            self.session,
            "opportunity.transition",
            "opportunity",
            notice_id,
            actor_type="user" if role != "system" else "system",
            actor_id=actor,
            before=before,
            after={"status": target},
            meta={"reason": reason},
        )
