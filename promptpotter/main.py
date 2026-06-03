"""
PromptPotter Optimizer API — main FastAPI application entry point.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference

from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
from promptpotter.application.jobs import (
    JobRegistry,
    LaunchError,
    QuotaExceededError,
    default_jobs_dir,
)
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import APP_VERSION, settings
from promptpotter.infrastructure.identity import (
    build_identity_bundle,
    default_identity_paths,
)
from promptpotter.presentation import api
from promptpotter.presentation.api.middleware import install_oidc_middleware

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""
    logger.info("Starting %s v%s", app.title, app.version)
    logger.info("Environment: %s", settings.ENVIRONMENT)
    app.state.identity_bundle = build_identity_bundle(default_identity_paths())
    app.state.job_registry = JobRegistry(default_jobs_dir())
    app.state.draft_campaigns = DraftCampaignRegistry()
    logger.info("Webapp available at: /")
    logger.info("API docs available at: /docs")
    yield
    logger.info("Shutting down PromptPotter Optimizer")


app = FastAPI(
    title="PromptPotter Optimizer",
    description="API-first prompt optimization service",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/docs", include_in_schema=False)
async def scalar_docs() -> Response:
    doc: Response = get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )
    return doc


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    # User-scoped abuse limit (rate / concurrent / daily-campaigns). Maps to 429
    # for EVERY route — the dispatcher path converts early for its ack flow, this
    # catches the routes that call the launcher directly (mint-campaign-from-draft).
    return JSONResponse(status_code=429, content={"error": exc.code, "message": str(exc)})


@app.exception_handler(LaunchError)
async def launch_error_handler(request: Request, exc: LaunchError) -> JSONResponse:
    # Malformed launch (bad payload / dataset not found / not owned) → 422.
    return JSONResponse(status_code=422, content={"error": "payload_invalid", "message": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OIDC middleware — populates request.state.identity_ctx from the opaque
# session cookie. Per ADR-0002 no-drift gate #2: tokens never appear past
# this boundary; downstream code sees only IdentityContext.
install_oidc_middleware(app)


@app.middleware("http")
async def no_store_on_api(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Set ``Cache-Control: no-store`` on every ``/api/v1/*`` response.

    The API is the webapp's live polling surface — ``dashboard.json``,
    per-cycle round files, file listings, active-session pointer. Any
    browser/intermediate caching is a freshness bug, not an optimization,
    because we already serve from in-memory state and the on-disk files
    are tiny. Static webapp assets at the root are unaffected (served
    by StaticFiles with its own caching defaults).
    """
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Health check
_health = APIRouter(tags=["Health"])


@_health.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "PromptPotter Optimizer",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": APP_VERSION,
    }


# Include routers. Each router owns its own tags (and prefix where it maps to a
# single resource); the mount supplies only the shared /api/v1 version prefix.
app.include_router(_health, prefix="/api/v1")
app.include_router(api.backends_router, prefix="/api/v1")
app.include_router(api.campaigns_router, prefix="/api/v1")
app.include_router(api.active_router, prefix="/api/v1")
app.include_router(api.datasets_router, prefix="/api/v1")
app.include_router(api.measurements_router, prefix="/api/v1")
app.include_router(api.verify_router, prefix="/api/v1")
app.include_router(api.commands_router, prefix="/api/v1")
app.include_router(api.auth_router, prefix="/api/v1")

# Static webapp mount — read-only operator dashboard at the domain root
# (Next.js export from webapp/, built via `npm run build` in that directory).
# The app owns `/`; the API is the carved-out `/api/v1` namespace. This mount
# is a catch-all and MUST stay the last route registered — every API router
# plus FastAPI's auto /docs + /openapi.json are matched first by Starlette's
# in-order resolution. `html=True` serves out/index.html at `/`.
WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp" / "out"
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


__all__ = ["WEBAPP_DIR", "app", "lifespan"]
