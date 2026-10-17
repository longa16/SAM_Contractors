from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.config import Settings
from app.db.base import make_engine
from app.errors import DomainError
from app.logging_setup import request_id, setup_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings)
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="GovContractAI", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings

    @app.middleware("http")
    async def correlation(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Request IDs are generated locally rather than trusting unbounded caller input.
        token = request_id.set(uuid4().hex)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id.get()
            return response
        finally:
            request_id.reset(token)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready() -> JSONResponse:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM alembic_version LIMIT 1")).one()
            return JSONResponse({"status": "ready"})
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    return app


app = create_app()
