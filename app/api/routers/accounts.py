from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from sqlalchemy import select, update

from app.api.deps import Admin, InternalUser, SessionDep
from app.db.models import AccountAction, EmailOutbox, User
from app.errors import Conflict, NotFound
from app.schemas.accounts import (
    AcceptAction,
    ChangePassword,
    ForgotPassword,
    Invitation,
    MemberUpdate,
)
from app.services import accounts, mailer
from app.services.audit import audit
from app.services.cases import authorize_company

router = APIRouter(prefix="/api", tags=["accounts"])
GENERIC_RESET = (
    "Si un compte actif correspond à cette adresse, un lien de réinitialisation sera envoyé."
)


@router.post("/auth/forgot-password")
def forgot(
    payload: ForgotPassword, session: SessionDep, request: Request, tasks: BackgroundTasks
) -> dict[str, str]:
    settings, engine = request.app.state.settings, request.app.state.engine
    host = request.client.host if request.client else "unknown"
    accounts.rate_limit(engine, settings, "forgot-ip:" + host, 20)
    accounts.rate_limit(engine, settings, "forgot-email:" + payload.email.strip().lower(), 5)
    user = session.scalar(
        select(User).where(User.email == payload.email.strip().lower(), User.is_active.is_(True))
    )
    if user is not None:
        from app.db.models import Company

        company = session.get(Company, user.company_id) if user.company_id else None
        if user.company_id is None or (company is not None and company.is_active):
            mail_id = accounts.issue_action(session, settings, user, "reset", "self-service")
            session.commit()
            tasks.add_task(mailer.deliver, engine, settings, mail_id)
    message = (
        GENERIC_RESET
        if settings.mail_mode == "smtp"
        else (
            "Si un compte actif correspond à cette adresse, un lien est disponible "
            "dans la boîte de test de l’administrateur. "
            "Aucun email réel n’est envoyé en mode local."
        )
    )
    return {"message": message}


@router.post("/auth/accept-action")
def accept_action(payload: AcceptAction, session: SessionDep, request: Request) -> dict[str, str]:
    accounts.rate_limit(
        request.app.state.engine,
        request.app.state.settings,
        "accept-ip:" + (request.client.host if request.client else "unknown"),
        30,
        15,
    )
    accounts.accept(session, payload.token, payload.password.get_secret_value())
    session.commit()
    return {"message": "Mot de passe enregistré. Connectez-vous avec vos identifiants."}


@router.post("/auth/change-password")
def change_password(
    payload: ChangePassword, session: SessionDep, actor: InternalUser
) -> dict[str, str]:
    accounts.change_password(
        session,
        actor,
        payload.current_password.get_secret_value(),
        payload.password.get_secret_value(),
    )
    session.commit()
    return {
        "message": "Mot de passe modifié. Reconnectez-vous ; les sessions précédentes sont fermées."
    }


@router.post("/auth/logout")
def logout(session: SessionDep, actor: InternalUser) -> dict[str, str]:
    session.execute(
        update(User).where(User.id == actor.id).values(auth_version=User.auth_version + 1)
    )
    from datetime import UTC, datetime

    session.execute(
        update(AccountAction)
        .where(AccountAction.user_id == actor.id, AccountAction.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    audit(
        session,
        "account.logout",
        "user",
        str(actor.id),
        actor_type="user",
        actor_id=str(actor.id),
        meta={"company_id": actor.company_id},
    )
    session.commit()
    return {"status": "logged_out"}


@router.get("/users")
def members(
    session: SessionDep,
    actor: Admin,
    company_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    if company_id is not None:
        authorize_company(session, actor, company_id)
    return accounts.members(session, company_id, page)


@router.post("/invitations", status_code=201)
def invitation(
    payload: Invitation, session: SessionDep, actor: Admin, request: Request, tasks: BackgroundTasks
) -> dict[str, Any]:
    accounts.rate_limit(
        request.app.state.engine, request.app.state.settings, "invite-admin:" + str(actor.id), 30
    )
    user, mail_id = accounts.invite(
        session,
        request.app.state.settings,
        email=payload.email,
        company_id=payload.company_id,
        role=payload.role,
        actor_id=str(actor.id),
    )
    session.commit()
    tasks.add_task(mailer.deliver, request.app.state.engine, request.app.state.settings, mail_id)
    return {
        "user": accounts.member_data(user),
        "mail_id": mail_id,
        "delivery_mode": request.app.state.settings.mail_mode,
    }


@router.patch("/users/{user_id}")
def edit_member(
    user_id: int, payload: MemberUpdate, session: SessionDep, actor: Admin
) -> dict[str, Any]:
    user = accounts.update_member(
        session, user_id, active=payload.is_active, role=payload.role, actor=actor
    )
    session.commit()
    return accounts.member_data(user)


@router.post("/users/{user_id}/resend-invitation")
def resend(
    user_id: int, session: SessionDep, actor: Admin, request: Request, tasks: BackgroundTasks
) -> dict[str, Any]:
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("Utilisateur introuvable.")
    if not user.invitation_pending:
        raise Conflict("Ce compte n’a pas d’invitation en attente.")
    accounts.rate_limit(
        request.app.state.engine, request.app.state.settings, "resend:" + str(user.id), 5
    )
    mail_id = accounts.issue_action(
        session, request.app.state.settings, user, "invite", str(actor.id)
    )
    session.commit()
    tasks.add_task(mailer.deliver, request.app.state.engine, request.app.state.settings, mail_id)
    return {"mail_id": mail_id, "delivery_mode": request.app.state.settings.mail_mode}


@router.post("/users/{user_id}/reset-password")
def request_reset(
    user_id: int, session: SessionDep, actor: Admin, request: Request, tasks: BackgroundTasks
) -> dict[str, Any]:
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("Utilisateur introuvable.")
    if not user.is_active:
        raise Conflict("Réactivez le compte ou renvoyez son invitation avant la réinitialisation.")
    accounts.rate_limit(
        request.app.state.engine, request.app.state.settings, "admin-reset:" + str(user.id), 5
    )
    mail_id = accounts.issue_action(
        session, request.app.state.settings, user, "reset", str(actor.id)
    )
    session.commit()
    tasks.add_task(mailer.deliver, request.app.state.engine, request.app.state.settings, mail_id)
    return {"mail_id": mail_id, "delivery_mode": request.app.state.settings.mail_mode}


@router.get("/mail/outbox")
def outbox(
    session: SessionDep,
    actor: Admin,
    request: Request,
    company_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    if company_id is not None:
        authorize_company(session, actor, company_id)
    return mailer.outbox(session, request.app.state.settings, company_id, page)


@router.post("/mail/outbox/{mail_id}/retry")
def retry_mail(
    mail_id: int, session: SessionDep, actor: Admin, request: Request, tasks: BackgroundTasks
) -> dict[str, str]:
    mail = session.get(EmailOutbox, mail_id)
    if mail is None:
        raise NotFound("Message introuvable.")
    action = session.get_one(AccountAction, mail.action_id)
    from datetime import UTC, datetime

    if action.used_at or action.expires_at <= datetime.now(UTC):
        raise Conflict(
            "Ce lien a expiré ou a été remplacé. Générez une nouvelle invitation ou demande."
        )
    if (
        request.app.state.settings.mail_mode != "smtp"
        or mail.status not in {"pending", "failed"}
        or mail.attempts >= 5
    ):
        raise Conflict(
            "Ce message ne peut pas être renvoyé. Générez un nouveau lien si nécessaire."
        )
    audit(
        session,
        "account.mail_retry_requested",
        "user",
        str(action.user_id),
        actor_type="user",
        actor_id=str(actor.id),
    )
    session.commit()
    tasks.add_task(mailer.deliver, request.app.state.engine, request.app.state.settings, mail_id)
    return {"status": "queued"}
