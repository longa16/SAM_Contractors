import base64
import hashlib
import logging
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.base import session_factory
from app.db.models import AccountAction, EmailOutbox
from app.errors import Conflict
from app.services.auth import signing_key

log = logging.getLogger(__name__)


def cipher(settings: Settings) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(signing_key(settings).encode()).digest()))


def queue_message(
    session: Session,
    settings: Settings,
    action: AccountAction,
    recipient: str,
    subject: str,
    body: str,
) -> EmailOutbox:
    mail = EmailOutbox(
        action_id=action.id,
        recipient=recipient,
        subject=subject,
        encrypted_body=cipher(settings).encrypt(body.encode()),
        status="local" if settings.mail_mode == "local" else "pending",
    )
    session.add(mail)
    session.flush()
    return mail


def deliver(engine: Engine, settings: Settings, mail_id: int) -> None:
    if settings.mail_mode != "smtp":
        return
    factory = session_factory(engine)
    with factory.begin() as session:
        mail = session.get(EmailOutbox, mail_id)
        if mail is None or mail.status not in {"pending", "failed"} or mail.attempts >= 5:
            return
        action = session.get_one(AccountAction, mail.action_id)
        if action.used_at or action.expires_at <= datetime.now(UTC):
            mail.status = "cancelled"
            return
        claimed = session.execute(
            update(EmailOutbox)
            .where(
                EmailOutbox.id == mail.id,
                EmailOutbox.status == mail.status,
                EmailOutbox.attempts == mail.attempts,
            )
            .values(status="sending", attempts=mail.attempts + 1)
        )
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            return
        recipient, subject, encrypted = mail.recipient, mail.subject, mail.encrypted_body
    try:
        if not settings.smtp_host:
            raise Conflict("Serveur SMTP non configuré.")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = settings.mail_from, recipient, subject
        message.set_content(cipher(settings).decrypt(encrypted).decode())
        context = ssl.create_default_context()
        connection: smtplib.SMTP = (
            smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=context,
            )
            if settings.smtp_security == "ssl"
            else smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
            )
        )
        with connection:
            if settings.smtp_security == "starttls":
                connection.starttls(context=context)
            if settings.smtp_username:
                connection.login(
                    settings.smtp_username,
                    settings.smtp_password.get_secret_value() if settings.smtp_password else "",
                )
            connection.send_message(message)
    except Exception:
        # Never log server responses, email bodies or temporary links.
        with factory.begin() as session:
            mail = session.get_one(EmailOutbox, mail_id)
            mail.status = "failed"
            mail.last_error = "Envoi SMTP échoué. Vérifiez la configuration puis réessayez."
        log.warning("account_email_delivery_failed", extra={"fields": {"mail_id": mail_id}})
    else:
        with factory.begin() as session:
            mail = session.get_one(EmailOutbox, mail_id)
            mail.status, mail.last_error, mail.sent_at = "sent", None, datetime.now(UTC)


def outbox(
    session: Session, settings: Settings, company_id: int | None, page: int
) -> dict[str, Any]:
    from app.db.models import User

    statement = select(EmailOutbox, AccountAction).join(
        AccountAction, AccountAction.id == EmailOutbox.action_id
    )
    if company_id is not None:
        statement = statement.join(User, User.id == AccountAction.user_id).where(
            User.company_id == company_id
        )
    rows = session.execute(
        statement.order_by(EmailOutbox.id.desc()).offset((page - 1) * 20).limit(21)
    ).all()
    items = []
    for mail, action in rows[:20]:
        usable = action.used_at is None and action.expires_at > datetime.now(UTC)
        item = {
            "id": mail.id,
            "recipient": mail.recipient,
            "subject": mail.subject,
            "status": mail.status,
            "error": mail.last_error,
            "attempts": mail.attempts,
            "created_at": mail.created_at.isoformat(),
            "expires_at": action.expires_at.isoformat(),
            "usable": usable,
        }
        if settings.mail_mode == "local" and usable:
            try:
                item["body"] = cipher(settings).decrypt(mail.encrypted_body).decode()
            except InvalidToken:
                item["error"] = (
                    "Message indisponible après changement de configuration. "
                    "Générez un nouveau lien."
                )
        items.append(item)
    return {"mode": settings.mail_mode, "items": items, "has_next": len(rows) > 20}
