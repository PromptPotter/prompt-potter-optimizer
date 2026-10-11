import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from scalar_fastapi import get_scalar_api_reference
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.jobs.reaper import periodic_sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.logging import setup_logging, silence_proactor_disconnect_noise
from promptpotter.config.paths import (
    DEFAULT_PROJECTS_ROOT,
    user_data_root,
    webapp_static_root,
)
from promptpotter.config.settings import APP_VERSION, non_utf8_encoding, settings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.presentation.admin_bot import notify_operator
from promptpotter.presentation.api.deps import auth_is_open
from promptpotter.presentation.api.middleware.oidc import install_oidc_middleware
from promptpotter.presentation.api.routers.active import active_router
from promptpotter.presentation.api.routers.auth import auth_router
from promptpotter.presentation.api.routers.backends import backends_router
from promptpotter.presentation.api.routers.campaigns import campaigns_router
from promptpotter.presentation.api.routers.commands import commands_router
from promptpotter.presentation.api.routers.datasets import datasets_router
from promptpotter.presentation.api.routers.diagnostics import diagnostics_router
from promptpotter.presentation.api.routers.origins import origins_router
from promptpotter.presentation.terminal.server_banner import render_server_banner
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PotterError

setup_logging()
logger = logging.getLogger(__name__)


def _telegram_shutdown_notice(registry: JobRegistry) -> None:
    """A clean exit 0 leaves ``Restart=on-failure`` quiet; a SIGKILL or OOM never reaches this hook."""
    running = registry.list_running()
    if running:
        interrupted = ", ".join(f"{job.hop.campaign_id}/{job.hop.cycle_id}" for job in running)
        notify_operator(
            f"⚠ {settings.BRAND_SERVICE_NAME} stopped with {len(running)} run(s) in flight: "
            f"{interrupted}\nResume from the campaign once it is back up."
        )
    else:
        notify_operator(f"{settings.BRAND_SERVICE_NAME} stopped (nothing running).")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    silence_proactor_disconnect_noise()
    complete_registries()
    bundle = build_identity_bundle(default_identity_paths())
    app.state.identity_bundle = bundle
    print(
        render_server_banner(
            brand_name=settings.BRAND_SERVICE_NAME,
            version=app.version,
            environment=settings.ENVIRONMENT,
            data_root=user_data_root(),
            auth_open=auth_is_open(bundle),
            providers=bundle.config.configured,
            non_utf8=non_utf8_encoding(),
        ),
        flush=True,
    )

    # The registry ends cycles whose JOB is proven dead; the sweep clears dead cycles it never saw.
    registry = JobRegistry.attach()
    app.state.job_registry = registry
    sweep_task = asyncio.create_task(periodic_sweep(DEFAULT_PROJECTS_ROOT))
    yield
    sweep_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep_task
    await asyncio.to_thread(_telegram_shutdown_notice, registry)
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


# The ONE flat envelope (`api-openapi.yaml::ErrorEnvelope`); no route raises HTTPException.
def _error_response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    exc_info: bool = False,
) -> JSONResponse:
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
    return _error_response(
        request,
        status=exc.http_status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        status=422,
        code="request_invalid",
        message="Request failed validation.",
        details={"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # `exc_info` logs the traceback beside the `error_id`, the only way back to it.
    return _error_response(
        request,
        status=500,
        code="internal_error",
        message="Internal server error",
        exc_info=True,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tokens never appear past this boundary (ADR-0002): downstream code sees only IdentityContext.
install_oidc_middleware(app)


class SecurityHeadersMiddleware:
    """The ONE header seam. Pure ASGI: ``BaseHTTPMiddleware`` buffers the body and breaks SSE teardown."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith("/api/v1/")
        is_https = scope.get("scheme") == "https"
        began = time.perf_counter()

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
                    elapsed_ms = (time.perf_counter() - began) * 1000
                    headers["server-timing"] = f"app;dur={elapsed_ms:.1f}"
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Inside the header seam; GZip passes ``text/event-stream`` through, so SSE is never buffered.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(SecurityHeadersMiddleware)


_health = APIRouter(tags=["Health"])


class HealthResponse(StrictModel):
    status: str
    service: str
    timestamp: str
    version: str = Field(description="`APP_VERSION` — the browser's one source of it")


@_health.get("/health")
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        service=settings.BRAND_SERVICE_NAME,
        timestamp=utcnow_iso(),
        version=APP_VERSION,
    )


app.include_router(_health, prefix="/api/v1")
app.include_router(backends_router, prefix="/api/v1")
app.include_router(campaigns_router, prefix="/api/v1")
app.include_router(active_router, prefix="/api/v1")
app.include_router(datasets_router, prefix="/api/v1")
app.include_router(origins_router, prefix="/api/v1")
app.include_router(diagnostics_router, prefix="/api/v1")
app.include_router(commands_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

# A catch-all, so it MUST stay the last route registered: Starlette resolves in order.
WEBAPP_DIR = webapp_static_root()
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


__all__ = ["WEBAPP_DIR", "app", "lifespan"]
