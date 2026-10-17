from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.base import session_factory
from app.db.models import Company, User
from app.errors import Forbidden, Unauthorized
from app.services.auth import decode_token

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with session_factory(request.app.state.engine)() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if not credentials:
        raise Unauthorized("Connexion requise.")
    user_id = decode_token(credentials.credentials, request.app.state.settings)
    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise Unauthorized("Compte indisponible.")
    if user.company_id is not None:
        company = session.get(Company, user.company_id)
        if not company or not company.is_active:
            raise Unauthorized("Compte indisponible.")
    return user


def require_role(*roles: str) -> Callable[..., User]:
    def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in roles:
            raise Forbidden("Votre rôle ne permet pas cette action.")
        return user

    return dependency


InternalUser = Annotated[User, Depends(require_role("admin", "business_developer", "ops_manager"))]
Decider = Annotated[User, Depends(require_role("admin", "business_developer"))]
Admin = Annotated[User, Depends(require_role("admin"))]
