import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from promptpotter.application.jobs.reaper import periodic_sweep, reap_cycle_by_id
from promptpotter.application.jobs.registry import Job, JobRegistry, default_jobs_dir
from promptpotter.config.logging import setup_logging, silence_proactor_disconnect_noise
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, webapp_static_root
from promptpotter.config.settings import APP_VERSION, settings
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
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
        reap_cycle_by_id(DEFAULT_PROJECTS_ROOT, job.hop)

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
    logger.info("Shutting down %s", settings.BRAND_SERVICE_NAME)


app = FastAPI(
    title=settings.BRAND_SERVICE_NAME,
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
# `{"error", "message", "error_id", "details"?}` at the top level (no `detail`
# wrapper). Three handlers feed it: typed PotterError (the application taxonomy),
# FastAPI's request-validation 422, and the catch-all 500. No route raises
# HTTPException.
def _error_response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    exc_info: bool = False,
) -> JSONResponse:
    """Mint the trace id, log it, serialize the envelope — one seam, all errors. EVERY error carries an ``error_id``, so a bug
    report quotes it instead of a wall-clock guess."""
    error_id = uuid.uuid4().hex[:12]
    logger.log(
        logging.ERROR if exc_info else logging.WARNING,
        "api error [%s] %s %s -> %d %s",
        error_id,
        request.method,
        request.url.path,
        status,
        code,
        exc_info=exc_info,
    )
    body: dict[str, object] = {"error": code, "message": message, "error_id": error_id}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(PotterError)
async def potter_error_handler(request: Request, exc: PotterError) -> JSONResponse:
    # The one mapping seam for the application error taxonomy: each subclass
    # carries its own status + code + optional structured details. Routes that
    # need extra context still catch the specific subclass and add it first.
    return _error_response(
        request,
        status=exc.http_status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's request-shape validation (bad body/query/header) → the same
    # envelope; the per-field error list rides `details.errors`.
    return _error_response(
        request,
        status=422,
        code="request_invalid",
        message="Request failed validation.",
        details={"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # An unhandled error is by definition one we can't describe, so the id is the
    # only way back to the traceback — `exc_info` puts it beside the same handle.
    return _error_response(
        request,
        status=500,
        code="internal_error",
        message="Internal server error",
        exc_info=True,
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


class SecurityHeadersMiddleware:
    """The single response-header seam — never add a second middleware beside it. Pure ASGI, not
    ``BaseHTTPMiddleware``, which buffers the body and breaks the SSE feed's disconnect/shutdown teardown."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith("/api/v1/")
        is_https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", "strict-origin-when-cross-origin")
                if is_https:
                    headers.setdefault(
                        "strict-transport-security", "max-age=63072000; includeSubDomains"
                    )
                headers.setdefault(
                    "content-security-policy",
                    "default-src 'none'; frame-ancestors 'none'"
                    if is_api
                    else "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
                )
                if is_api:
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)


# Health check
_health = APIRouter(tags=["Health"])


@_health.get("/health")
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.BRAND_SERVICE_NAME,
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
WEBAPP_DIR = webapp_static_root()
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


__all__ = ["WEBAPP_DIR", "app", "lifespan"]
