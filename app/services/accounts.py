import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.base import session_factory
from app.db.models import AccountAction, AuthThrottle, Company, User
from app.errors import Conflict, DomainError, NotFound
from app.services.audit import audit
from app.services.auth import create_user, passwords, signing_key
from app.services.mailer import queue_message


class TooManyRequests(DomainError):
    status_code = 429
    code = "too_many_requests"


def rate_limit(
    engine: Engine, settings: Settings, subject: str, limit: int, minutes: int = 60
) -> None:
    key = hashlib.sha256((signing_key(settings) + ":" + subject).encode()).hexdigest()
    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        row = session.get(AuthThrottle, key)
        if row is None:
            try:
                with session.begin_nested():
                    row = AuthThrottle(key=key, count=0, window_started_at=now)
                    session.add(row)
                    session.flush()
            except IntegrityError:
                row = session.get_one(AuthThrottle, key)
        if row.window_started_at <= now - timedelta(minutes=minutes):
            row.count, row.window_started_at = 0, now
            session.flush()
        changed = session.execute(
            update(AuthThrottle)
            .where(AuthThrottle.key == key, AuthThrottle.count < limit)
            .values(count=AuthThrottle.count + 1)
        )
        if changed.rowcount != 1:  # type: ignore[attr-defined]
            raise TooManyRequests("Trop de demandes. Réessayez plus tard.")


def issue_action(session: Session, settings: Settings, user: User, kind: str, actor_id: str) -> int:
    # Serialize link issuance with password and membership changes.
    session.execute(update(User).where(User.id == user.id).values(auth_version=User.auth_version))
    session.refresh(user)
    now = datetime.now(UTC)
    session.execute(
        update(AccountAction)
        .where(AccountAction.user_id == user.id, AccountAction.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    duration = (
        timedelta(hours=settings.invite_expiry_hours)
        if kind == "invite"
        else timedelta(minutes=settings.reset_expiry_minutes)
    )
    action = AccountAction(
        user_id=user.id,
        kind=kind,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        auth_version=user.auth_version,
        expires_at=now + duration,
    )
    session.add(action)
    session.flush()
    link = settings.public_app_url.rstrip("/") + "/#account=" + token
    subject = (
        "Votre invitation GovContractAI"
        if kind == "invite"
        else "Réinitialiser votre mot de passe GovContractAI"
    )
    opening = (
        "Choisissez votre mot de passe pour accéder à votre espace."
        if kind == "invite"
        else "Une réinitialisation du mot de passe a été demandée."
    )
    body = (
        opening
        + f"\n\n{link}\n\nCe lien est personnel, temporaire et utilisable une seule fois."
        + f"\nExpiration : {action.expires_at.isoformat()}"
        + "\nSi vous n’êtes pas à l’origine de cette demande, ignorez ce message."
    )
    mail = queue_message(session, settings, action, user.email, subject, body)
    audit(
        session,
        "account." + kind + "_requested",
        "user",
        str(user.id),
        actor_type="user",
        actor_id=actor_id,
        meta={"company_id": user.company_id, "mail_id": mail.id},
    )
    return mail.id


def invite(
    session: Session, settings: Settings, *, email: str, company_id: int, role: str, actor_id: str
) -> tuple[User, int]:
    company = session.get(Company, company_id)
    if not company or not company.is_active:
        raise NotFound("Entreprise introuvable.")
    # Start a real write transaction before the nested duplicate-email savepoint on SQLite.
    session.execute(update(Company).where(Company.id == company.id).values(name=Company.name))
    user = create_user(
        session, email, secrets.token_urlsafe(40), role, company_id, actor_id=actor_id
    )
    user.is_active, user.invitation_pending = False, True
    session.flush()
    return user, issue_action(session, settings, user, "invite", actor_id)


def accept(session: Session, token: str, password: str) -> None:
    action = session.scalar(
        select(AccountAction).where(
            AccountAction.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    now = datetime.now(UTC)
    if action is None or action.used_at is not None or action.expires_at <= now:
        raise Conflict("Ce lien est invalide, expiré ou déjà utilisé. Demandez un nouveau lien.")
    user = session.get_one(User, action.user_id)
    if (
        user.auth_version != action.auth_version
        or (action.kind == "invite" and not user.invitation_pending)
        or (action.kind == "reset" and not user.is_active)
    ):
        raise Conflict("Ce lien n’est plus utilisable. Demandez un nouveau lien.")
    if user.company_id is not None:
        company = session.get(Company, user.company_id)
        if company is None or not company.is_active:
            raise Conflict("Cet espace est indisponible.")
    changed = session.execute(
        update(User)
        .where(User.id == user.id, User.auth_version == action.auth_version)
        .values(
            password_hash=passwords.hash(password),
            auth_version=User.auth_version + 1,
            invitation_pending=False,
            is_active=True,
        )
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Ce lien a déjà été utilisé.")
    consumed = session.execute(
        update(AccountAction)
        .where(AccountAction.id == action.id, AccountAction.used_at.is_(None))
        .values(used_at=now)
    )
    if consumed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Ce lien a déjà été utilisé.")
    session.execute(
        update(AccountAction)
        .where(AccountAction.user_id == user.id, AccountAction.used_at.is_(None))
        .values(used_at=now)
    )
    audit(
        session,
        "account.password_set",
        "user",
        str(user.id),
        actor_type="user",
        actor_id=str(user.id),
        meta={"company_id": user.company_id},
    )


def change_password(session: Session, user: User, current: str, new: str) -> None:
    if not passwords.verify(current, user.password_hash):
        raise Conflict("Le mot de passe actuel est incorrect.")
    changed = session.execute(
        update(User)
        .where(User.id == user.id, User.auth_version == user.auth_version)
        .values(password_hash=passwords.hash(new), auth_version=User.auth_version + 1)
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Le compte a changé ; reconnectez-vous.")
    session.execute(
        update(AccountAction)
        .where(AccountAction.user_id == user.id, AccountAction.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    audit(
        session,
        "account.password_changed",
        "user",
        str(user.id),
        actor_type="user",
        actor_id=str(user.id),
        meta={"company_id": user.company_id},
    )


def member_data(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "company_id": user.company_id,
        "is_active": user.is_active,
        "invitation_pending": user.invitation_pending,
        "created_at": user.created_at.isoformat(),
    }


def members(session: Session, company_id: int | None, page: int) -> dict[str, Any]:
    statement = select(User)
    if company_id is not None:
        statement = statement.where(User.company_id == company_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(statement.order_by(User.email).offset((page - 1) * 20).limit(20)).all()
    return {"items": [member_data(user) for user in rows], "total": total}


def update_member(session: Session, user_id: int, *, active: bool, role: str, actor: User) -> User:
    user = session.get(User, user_id)
    if not user:
        raise NotFound("Utilisateur introuvable.")
    if user.role == "admin":
        raise Conflict("La gestion des comptes administrateurs est réservée à leur titulaire.")
    if user.invitation_pending and active:
        raise Conflict("Cet utilisateur doit d’abord accepter son invitation.")
    before = {"role": user.role, "is_active": user.is_active}
    values: dict[str, Any] = {
        "role": role,
        "is_active": active,
        "auth_version": User.auth_version + 1,
    }
    if not active:
        values["invitation_pending"] = False
    session.execute(update(User).where(User.id == user.id).values(**values))
    session.refresh(user)
    session.execute(
        update(AccountAction)
        .where(AccountAction.user_id == user.id, AccountAction.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    audit(
        session,
        "account.member_updated",
        "user",
        str(user.id),
        actor_type="user",
        actor_id=str(actor.id),
        before=before,
        after={"role": role, "is_active": active},
        meta={"company_id": user.company_id},
    )
    return user
