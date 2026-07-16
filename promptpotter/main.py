"""
PromptPotter Optimizer API — main FastAPI application entry point.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference

from promptpotter.application.jobs.reaper import periodic_sweep, reap_cycle_by_id
from promptpotter.application.jobs.registry import Job, JobRegistry, default_jobs_dir
from promptpotter.config.logging import setup_logging, silence_proactor_disconnect_noise
from promptpotter.config.settings import APP_VERSION, settings
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.store.layout import DEFAULT_PROJECTS_ROOT, REPO_ROOT
from promptpotter.presentation.api.middleware.oidc import install_oidc_middleware
from promptpotter.presentation.api.routers.active import active_router
from promptpotter.presentation.api.routers.auth import auth_router
from promptpotter.presentation.api.routers.backends import backends_router
from promptpotter.presentation.api.routers.campaigns import campaigns_router
from promptpotter.presentation.api.routers.commands import commands_router
from promptpotter.presentation.api.routers.datasets import datasets_router
from promptpotter.presentation.api.routers.origins import origins_router
from promptpotter.presentation.api.routers.verify import verify_router
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PotterError

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""
    logger.info("Starting %s v%s", app.title, app.version)
    logger.info("Environment: %s", settings.ENVIRONMENT)
    # Quiet the benign Windows ProactorEventLoop disconnect noise (bpo-39010)
    # that fires when a browser tab drops a kept-alive socket.
    silence_proactor_disconnect_noise()
    app.state.identity_bundle = build_identity_bundle(default_identity_paths())

    # Liveness reconciler. The registry stamps a cycle terminal the moment its
    # API job is proven dead (torn task, or stale-on-restart) via on_reap; the
    # background periodic sweep clears CLI-launched dead cycles the registry
    # never saw, for the server's whole uptime (not just at boot). Both keep the
    # OS-style dock and the on-disk truth honest — a vanished producer is not a
    # live unit. See application/jobs/reaper.py.
    def _on_reap(job: Job) -> None:
        reap_cycle_by_id(DEFAULT_PROJECTS_ROOT, job.campaign_id, job.cycle_id)

    registry = JobRegistry(
        default_jobs_dir(), capacity=settings.MACHINE_RUN_CAPACITY, on_reap=_on_reap
    )
    app.state.job_registry = registry
    sweep_task = asyncio.create_task(periodic_sweep(DEFAULT_PROJECTS_ROOT))
    logger.info("Webapp available at: /")
    logger.info("API docs available at: /docs")
    yield
    sweep_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep_task
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


# Every API error serializes to the ONE flat envelope declared in
# docs/specs/m12-api-openapi.yaml#/components/schemas/ErrorEnvelope —
# `{"error", "message", "details"?}` at the top level (no `detail` wrapper).
# Three handlers feed it: typed PotterError (the application taxonomy), FastAPI's
# request-validation 422, and the catch-all 500. No route raises HTTPException.
def _envelope(
    code: str, message: str, details: dict[str, object] | None = None
) -> dict[str, object]:
    body: dict[str, object] = {"error": code, "message": message}
    if details:
        body["details"] = details
    return body


@app.exception_handler(PotterError)
async def potter_error_handler(request: Request, exc: PotterError) -> JSONResponse:
    # The one mapping seam for the application error taxonomy: each subclass
    # carries its own status + code + optional structured details. Routes that
    # need extra context still catch the specific subclass and add it first.
    return JSONResponse(
        status_code=exc.http_status, content=_envelope(exc.code, exc.message, exc.details)
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's request-shape validation (bad body/query/header) → the same
    # envelope; the per-field error list rides `details.errors`.
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "request_invalid", "Request failed validation.", {"errors": exc.errors()}
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500, content=_envelope("internal_error", "Internal server error")
    )


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
async def response_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Security + freshness response headers, in one pass (the single
    response-header seam — don't add a second middleware beside this).

    Security headers on **every** response: ``X-Content-Type-Options: nosniff``,
    ``X-Frame-Options: DENY`` + CSP ``frame-ancestors 'none'`` (clickjacking, both
    header generations), ``Referrer-Policy``, and HSTS when the request arrived over
    https (behind the Cloudflare tunnel uvicorn sees the forwarded proto via
    ``--proxy-headers``; plain-http local dev gets no HSTS, correctly). The CSP is
    **strict on JSON API paths** (``default-src 'none'`` — a JSON body is never a
    document, so this only bites a direct browser navigation) and **frame-only on the
    webapp document** so its own same-origin Next.js assets still load. A tighter
    document CSP (``script-src``/``style-src`` with a nonce) is a follow-up — it needs
    a webapp smoke test to avoid breaking the UI, so it is deliberately not set here.

    Plus ``Cache-Control: no-store`` on ``/api/v1/*`` — the live polling surface
    (``dashboard.json``, per-cycle round files, active-session pointer) must never be
    cached: a freshness bug, not an optimization, since we serve from memory + tiny
    on-disk files. Static webapp assets at the root keep StaticFiles' own caching.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    is_api = request.url.path.startswith("/api/v1/")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'"
        if is_api
        else "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    )
    if is_api:
        response.headers["Cache-Control"] = "no-store"
    return response


# Health check
_health = APIRouter(tags=["Health"])


@_health.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "PromptPotter Optimizer",
        "timestamp": utcnow_iso(),
        "version": APP_VERSION,
    }


# Include routers. Each router owns its own tags (and prefix where it maps to a
# single resource); the mount supplies only the shared /api/v1 version prefix.
app.include_router(_health, prefix="/api/v1")
app.include_router(backends_router, prefix="/api/v1")
app.include_router(campaigns_router, prefix="/api/v1")
app.include_router(active_router, prefix="/api/v1")
app.include_router(datasets_router, prefix="/api/v1")
app.include_router(origins_router, prefix="/api/v1")
app.include_router(verify_router, prefix="/api/v1")
app.include_router(commands_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

# Static webapp mount — read-only operator dashboard at the domain root
# (Next.js export from webapp/, built via `npm run build` in that directory).
# The app owns `/`; the API is the carved-out `/api/v1` namespace. This mount
# is a catch-all and MUST stay the last route registered — every API router
# plus FastAPI's auto /docs + /openapi.json are matched first by Starlette's
# in-order resolution. `html=True` serves out/index.html at `/`.
WEBAPP_DIR = REPO_ROOT / "webapp" / "out"
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


__all__ = ["WEBAPP_DIR", "app", "lifespan"]
