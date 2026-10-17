from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.routers import auth, companies, opportunities, system
from app.config import ROOT, Settings
from app.db.base import make_engine
from app.errors import DomainError
from app.llm.client import GroqClient
from app.logging_setup import request_id, setup_logging
from app.services.screening import ScreeningService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings)
    engine = make_engine(settings.database_url)
    required_heads = set(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="GovContractAI", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.state.screening = ScreeningService(engine, settings, GroqClient(settings))
    app.include_router(auth.router)
    app.include_router(companies.router)
    app.include_router(opportunities.router)
    app.include_router(system.router)

    @app.middleware("http")
    async def correlation(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Request IDs are generated locally rather than trusting unbounded caller input.
        token = request_id.set(uuid4().hex)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id.get()
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
            )
            return response
        finally:
            request_id.reset(token)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Vérifiez les champs du formulaire.",
                    "fields": [
                        {"loc": list(item["loc"]), "type": item["type"]} for item in error.errors()
                    ],
                }
            },
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready() -> JSONResponse:
        try:
            with engine.connect() as conn:
                actual_heads = set(
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalars()
                )
                if actual_heads != required_heads:
                    return JSONResponse({"status": "migration_required"}, status_code=503)
            return JSONResponse({"status": "ready"})
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app


app = create_app()
