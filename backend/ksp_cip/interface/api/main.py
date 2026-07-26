"""FastAPI application factory.

Three cross-cutting behaviours are installed here and nowhere else:

* **RFC 9457 problem responses** for every deliberate error, so a client never
  has to parse a stack trace or guess at a status code;
* **correlation IDs** on every request, propagated into logs and audit rows,
  which is what makes an audit trail reconstructable after the fact;
* **the built frontend**, mounted last so API routes always win.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...config import Settings, get_settings
from ...domain.errors import CIPError
from ...infrastructure.observability import (
    actor_var,
    correlation_id_var,
    get_logger,
    new_correlation_id,
)
from ..container import Container, build_container
from .routers import (
    admin,
    analytics,
    cases,
    chat,
    export,
    files,
    graph,
    health,
    investigation,
    session,
    voice,
)

LOGGER = get_logger(__name__)

DESCRIPTION = """
Conversational crime intelligence for the Karnataka State Police.

Every factual statement returned by this API carries the evidence it rests on
and the computation that produced it. Inferred relationships are labelled as
inferred; data from the synthetic financial extension is labelled as an
extension. The language model orchestrates and phrases — it is never the source
of a fact.
""".strip()


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or build_container(settings)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    app.state.container = container
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-Id"],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("X-Correlation-Id") or new_correlation_id()
        token = correlation_id_var.set(correlation_id)
        actor_token = actor_var.set("anonymous")
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
            actor_var.reset(actor_token)
        response.headers["X-Correlation-Id"] = correlation_id
        return response

    @app.exception_handler(CIPError)
    async def cip_error_handler(request: Request, exc: CIPError) -> JSONResponse:
        problem = exc.to_problem(instance=str(request.url.path))
        LOGGER.warning("request_failed", extra={"code": exc.code, "path": request.url.path})
        return JSONResponse(
            status_code=exc.http_status,
            content=problem,
            media_type="application/problem+json",
            headers={"X-Correlation-Id": correlation_id_var.get() or ""},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://cip.ksp.gov.in/problems/validation_error",
                "title": "Request failed validation",
                "status": 422,
                "detail": "One or more fields are missing or malformed.",
                "code": "validation_error",
                "instance": str(request.url.path),
                "context": {"errors": _clean_errors(exc.errors())},
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        if request.url.path.startswith(settings.api_prefix):
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "type": f"https://cip.ksp.gov.in/problems/http_{exc.status_code}",
                    "title": str(exc.detail),
                    "status": exc.status_code,
                    "detail": str(exc.detail),
                    "code": f"http_{exc.status_code}",
                    "instance": str(request.url.path),
                },
                media_type="application/problem+json",
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={
                "type": "https://cip.ksp.gov.in/problems/internal_error",
                "title": "Internal error",
                "status": 500,
                "detail": "The request could not be completed. The incident has been logged.",
                "code": "internal_error",
                "instance": str(request.url.path),
            },
            media_type="application/problem+json",
        )

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(session.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(cases.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(graph.router, prefix=prefix)
    app.include_router(investigation.router, prefix=prefix)
    app.include_router(export.router, prefix=prefix)
    app.include_router(files.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    app.include_router(voice.router, prefix=prefix)

    @app.on_event("startup")
    async def warm_caches() -> None:
        stats = container.warm()
        LOGGER.info("startup_warm", extra=stats)

    _mount_frontend(app)
    return app


def _clean_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip non-serialisable context objects out of pydantic error payloads."""
    cleaned = []
    for error in errors[:20]:
        cleaned.append({
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", "invalid value"),
            "type": error.get("type", "value_error"),
        })
    return cleaned


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React console when it exists.

    Mounted last and at the root so every API path takes precedence. Absence of
    a build is not an error — the backend is fully usable on its own.
    """
    dist = Path(__file__).resolve().parents[4] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="console")
        LOGGER.info("frontend_mounted", extra={"path": str(dist)})


app = None  # populated by run helpers; keeps `uvicorn ksp_cip.interface.api.main:app` honest


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
