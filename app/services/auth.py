from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Company, User
from app.errors import ConfigurationError, Conflict, Unauthorized
from app.services.audit import audit

passwords = PasswordHash.recommended()
# A dummy hash avoids a cheap user-existence timing oracle.
DUMMY_HASH = passwords.hash("unavailable-account-placeholder")


def create_user(
    session: Session, email: str, password: str, role: str, company_id: int | None, *, actor_id: str
) -> User:
    if len(password) < 12:
        raise Conflict("Le mot de passe doit contenir au moins 12 caractères.")
    if role not in {"admin", "business_developer", "ops_manager", "subcontractor"}:
        raise Conflict("Rôle invalide.")
    if role == "subcontractor":
        raise Conflict("Les invitations de sous-traitants sont introduites en Phase 4.")
    if (role == "admin" and company_id is not None) or (role != "admin" and company_id is None):
        raise Conflict(
            "Un administrateur supervise la plateforme ; les autres rôles ont une entreprise."
        )
    if company_id is not None and session.get(Company, company_id) is None:
        raise Conflict("Entreprise inconnue.")
    user = User(
        email=email.strip().lower(),
        password_hash=passwords.hash(password),
        role=role,
        company_id=company_id,
    )
    try:
        with session.begin_nested():
            session.add(user)
            session.flush()
    except IntegrityError:
        raise Conflict("Compte déjà existant.") from None
    audit(
        session,
        "user.created",
        "user",
        str(user.id),
        actor_type="user",
        actor_id=actor_id,
        after={"role": role, "company_id": company_id},
    )
    return user


def authenticate(session: Session, email: str, password: str) -> User:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    encoded = user.password_hash if user else DUMMY_HASH
    valid = passwords.verify(password, encoded)
    if not user or not valid or not user.is_active:
        audit(session, "auth.login_failed", "auth", "login")
        session.commit()
        raise Unauthorized("Identifiants invalides.")
    if user.company_id is not None:
        company = session.get(Company, user.company_id)
        if not company or not company.is_active:
            raise Unauthorized("Compte indisponible.")
    audit(session, "auth.login", "user", str(user.id), actor_type="user", actor_id=str(user.id))
    return user


def signing_key(settings: Settings) -> str:
    if not settings.jwt_secret or len(settings.jwt_secret.get_secret_value()) < 32:
        raise ConfigurationError("Configurer JWT_SECRET avec au moins 32 caractères aléatoires.")
    return settings.jwt_secret.get_secret_value()


def issue_token(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
            "iss": settings.jwt_issuer,
            "aud": "govcontractai-api",
        },
        signing_key(settings),
        algorithm="HS256",
    )


def decode_token(token: str, settings: Settings) -> int:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key(settings),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience="govcontractai-api",
            options={"require": ["exp", "iat", "sub"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
        raise Unauthorized("Session invalide ou expirée.") from None
