import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from scalar_fastapi import get_scalar_api_reference
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware

from promptpotter.application import health
from promptpotter.application.account import record_refusal
from promptpotter.application.datasets.startability import offer_faults
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
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.presentation.admin_bot import check_health_and_tell, notify_operator
from promptpotter.presentation.api.deps import (
    auth_is_open,
    build_stores_from_identity,
    resolve_identity,
)
from promptpotter.presentation.api.middleware.headers import ResponseHeadersMiddleware
from promptpotter.presentation.api.middleware.oidc import install_oidc_middleware
from promptpotter.presentation.api.routers.active import active_router
from promptpotter.presentation.api.routers.auth import auth_router
from promptpotter.presentation.api.routers.backends import backends_router
from promptpotter.presentation.api.routers.campaigns import campaigns_router
from promptpotter.presentation.api.routers.commands import commands_router
from promptpotter.presentation.api.routers.datasets import datasets_router
from promptpotter.presentation.api.routers.hierarchy import hierarchy_router
from promptpotter.presentation.api.routers.origins import origins_router
from promptpotter.presentation.api.routers.site_records import site_records_router
from promptpotter.presentation.terminal.server_banner import render_server_banner
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    PayloadInvalidError,
    PotterError,
    UnauthorizedError,
    graceful,
)

setup_logging()
logger = logging.getLogger(__name__)


def _telegram_shutdown_notice(registry: JobRegistry) -> None:
    """A clean exit 0 leaves ``Restart=on-failure`` quiet; a SIGKILL or OOM never reaches this hook."""
    running = registry.list_running()
    if running:
        interrupted = ", ".join(job.what for job in running)
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
    # Before any user can find out: what is broken is told now, not at its first use.
    checked = await asyncio.to_thread(check_health_and_tell)
    print(
        render_server_banner(
            brand_name=settings.BRAND_SERVICE_NAME,
            version=app.version,
            data_root=user_data_root(),
            auth_open=auth_is_open(),
            providers=bundle.config.configured,
            broken=[health.row(c) for c in checked if c.reading.state == "broken"],
            non_utf8=non_utf8_encoding(),
            offer=settings.dataset_offer,
            offer_faults=offer_faults(),
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


class ErrorEnvelope(StrictModel):
    """What every refusal and failure answers, whatever raised it."""

    error: str = Field(description="The code a client branches on.")
    message: str = Field(description="The sentence an operator reads, naming its remedy.")
    error_id: str = Field(description="Names the server log line that holds this failure.")
    details: dict[str, Any] | None


app = FastAPI(
    title=settings.BRAND_SERVICE_NAME,
    description="API-first prompt optimization service",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    responses={"default": {"model": ErrorEnvelope}},
)


@app.get("/docs", include_in_schema=False)
async def scalar_docs() -> Response:
    doc: Response = get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )
    return doc


def _bank_refusal(request: Request, subject: str, status: int) -> None:
    """An attempt (a write, or anything that broke) by a signed-in person lands on their trail."""
    if request.method in ("GET", "HEAD", "OPTIONS") and status < 500:
        return
    try:
        identity = resolve_identity(request)
    except UnauthorizedError:
        return
    # The person's response outranks the record of it; the log line still names the refusal.
    with graceful("could not bank the refusal on the account's trail"):
        record_refusal(build_stores_from_identity(identity), subject=subject)


# The ONE flat envelope; no route raises HTTPException, so routing alone does.
def _error_response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    exc_info: bool = False,
) -> JSONResponse:
    error_id = uuid.uuid4().hex[:12]
    reason = f"/{details['reason']}" if details and "reason" in details else ""
    logger.log(
        logging.ERROR if exc_info else logging.WARNING,
        "api error [%s] %s %s -> %d %s%s",
        error_id,
        request.method,
        request.url.path,
        status,
        code,
        reason,
        exc_info=exc_info,
    )
    facts = "".join(
        f" {key}={value}"
        for key, value in (details or {}).items()
        if key != "reason" and isinstance(value, (str, int))
    )
    _bank_refusal(
        request,
        f"{request.method} {request.url.path} -> {status} {code}{reason} [{error_id}]{facts}: {message}",
        status,
    )
    body = ErrorEnvelope(error=code, message=message, error_id=error_id, details=details or None)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


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
    # Located in the payload, as every other entry point's refusal of the same model is.
    located = (
        {**error, "loc": error["loc"][1:]} if error["loc"][:1] == ("body",) else error
        for error in exc.errors()
    )
    return await potter_error_handler(request, PayloadInvalidError.refusing(located))


_COMMANDS = f"/api/v1{commands_router.prefix}/"


@app.exception_handler(HTTPException)
async def routing_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    asked = request.url.path.removeprefix(_COMMANDS)
    if request.url.path.startswith(_COMMANDS) and asked not in CommandKind:
        return _error_response(
            request,
            status=404,
            code="command_kind_unknown",
            message=f"No command kind {asked!r}; the kinds are {', '.join(CommandKind)}.",
        )
    return _error_response(
        request,
        status=exc.status_code,
        code=HTTPStatus(exc.status_code).name.lower(),
        message=exc.detail,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, _exc: Exception) -> JSONResponse:
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

# Tokens never appear past this boundary (decisions.md § 0002): downstream code sees only IdentityContext.
install_oidc_middleware(app)


WEBAPP_DIR = webapp_static_root()

# Inside the header seam; GZip passes ``text/event-stream`` through, so SSE is never buffered.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(ResponseHeadersMiddleware, webapp_dir=WEBAPP_DIR)


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


API_ROUTERS: tuple[APIRouter, ...] = (
    _health,
    backends_router,
    campaigns_router,
    active_router,
    hierarchy_router,
    datasets_router,
    origins_router,
    commands_router,
    auth_router,
    site_records_router,
)
for _router in API_ROUTERS:
    app.include_router(_router, prefix="/api/v1")

# A catch-all, so it MUST stay the last route registered: Starlette resolves in order.
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
