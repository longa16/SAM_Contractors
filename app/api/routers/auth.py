from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import InternalUser, SessionDep
from app.schemas.api import Login
from app.services.auth import authenticate, issue_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(payload: Login, session: SessionDep, request: Request) -> dict[str, Any]:
    user = authenticate(session, payload.email, payload.password.get_secret_value())
    token = issue_token(user, request.app.state.settings)
    session.commit()
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(actor: InternalUser) -> dict[str, Any]:
    return {
        "id": actor.id,
        "email": actor.email,
        "role": actor.role,
        "company_id": actor.company_id,
    }
