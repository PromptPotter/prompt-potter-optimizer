import asyncio
import contextlib
import inspect
import logging
import math
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import NamedTuple

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from scalar_fastapi import get_scalar_api_reference
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from promptpotter.application import health, notices
from promptpotter.application.account import record_refusal
from promptpotter.application.commands.dispatcher import command_answer
from promptpotter.application.cycle_reads import FEED_SUBJECT_HEADER
from promptpotter.application.datasets.startability import offer_faults
from promptpotter.application.jobs.interlock import minted_producer
from promptpotter.application.jobs.quota import RateLimitedError
from promptpotter.application.jobs.reaper import periodic_sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.landing import land_at_entry
from promptpotter.config.logging import setup_logging, silence_proactor_disconnect_noise
from promptpotter.config.paths import (
    default_projects_root,
    jobs_dir,
    user_data_root,
    webapp_static_root,
)
from promptpotter.config.settings import APP_VERSION, non_utf8_encoding, settings
from promptpotter.domain.command_kinds import CommandAcceptedBody, CommandKind, ReadKind
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.strict_model import MAX_UPLOAD_BYTES, RequestBody, StrictModel
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.outbound import notify_operator
from promptpotter.presentation.api.deps import (
    auth_is_open,
    build_stores_from_identity,
    open_caller,
    resolve_identity,
)
from promptpotter.presentation.api.middleware.headers import ResponseHeadersMiddleware
from promptpotter.presentation.api.middleware.oidc import install_oidc_middleware
from promptpotter.presentation.api.middleware.visits import OutsideVisitsMiddleware
from promptpotter.presentation.api.routers.auth import auth_flow_router
from promptpotter.presentation.api.routers.commands import commands_router
from promptpotter.presentation.api.routers.reads import READ_ROUTES, reads_router
from promptpotter.presentation.terminal.server_banner import render_server_banner
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    ContentTooLargeError,
    PayloadInvalidError,
    PotterError,
    UnauthorizedError,
    graceful,
)

setup_logging()
logger = logging.getLogger(__name__)


def _telegram_shutdown_notice(registry: JobRegistry) -> None:
    """A clean exit 0 leaves ``Restart=on-failure`` quiet; a SIGKILL or OOM never reaches this hook."""
    own = minted_producer(registry.jobs_dir)
    running = [] if own is None else [j for j in registry.list_running() if j.producer_id == own]
    if running:
        interrupted = ", ".join(job.what for job in running)
        notify_operator(
            f"⚠ {settings.BRAND_SERVICE_NAME} stopped with {len(running)} run(s) in flight: "
            f"{interrupted}\nResume from the campaign once it is back up.",
            ring=True,
        )
    else:
        notify_operator(
            f"{settings.BRAND_SERVICE_NAME} stopped (nothing of its own running).", ring=False
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    silence_proactor_disconnect_noise()
    land_at_entry(every_treatment=True)
    bundle = build_identity_bundle(default_identity_paths())
    app.state.identity_bundle = bundle
    # Before any user can find out: what is broken is told now, not at its first use.
    checked = await asyncio.to_thread(notices.check_and_tell)
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
    projects_root = default_projects_root()
    registry = JobRegistry.attach(jobs_dir(projects_root))
    app.state.job_registry = registry
    sweep_task = asyncio.create_task(periodic_sweep(projects_root))
    yield
    sweep_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep_task
    await asyncio.to_thread(_telegram_shutdown_notice, registry)
    logger.info("Shutting down %s", settings.BRAND_SERVICE_NAME)


class ErrorEnvelope(Refusal):
    """What every refusal and failure answers, whatever raised it: a refusal's `Refusal`, named by
    its log line."""

    error_id: str = Field(description="Names the server log line that holds this failure.")


app = FastAPI(
    title=settings.BRAND_SERVICE_NAME,
    description="API-first prompt optimization service",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
    responses={"default": {"model": ErrorEnvelope}},
)

# The contract the repo already publishes as `openapi.generated.json`, served beside its reader.
_contract = APIRouter(include_in_schema=False)
_OPENAPI_PATH = "/openapi.json"


@_contract.get(_OPENAPI_PATH)
async def openapi_document() -> JSONResponse:
    return JSONResponse(app.openapi())


@_contract.get("/docs")
async def scalar_docs() -> Response:
    doc: Response = get_scalar_api_reference(openapi_url=_OPENAPI_PATH, title=app.title)
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
    banked: bool = True,
) -> JSONResponse:
    error_id = uuid.uuid4().hex[:12]
    reason = f"/{details['reason']}" if details and "reason" in details else ""
    # A refusal is the server doing its job, as loud as the access line it repeats; a 5xx is a failure.
    logger.log(
        logging.ERROR if status >= 500 else logging.INFO,
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
    if banked:
        _bank_refusal(
            request,
            f"{request.method} {request.url.path} -> {status} {code}{reason} [{error_id}]{facts}: {message}",
            status,
        )
    body = ErrorEnvelope(code=code, message=message, error_id=error_id, details=details or {})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


@app.exception_handler(PotterError)
async def potter_error_handler(request: Request, exc: PotterError) -> JSONResponse:
    refused = Refusal.of(exc)
    return _error_response(
        request,
        status=exc.http_status,
        code=refused.code,
        message=refused.message,
        details=refused.details,
        headers=(
            {"Retry-After": str(math.ceil(exc.retry_after_s))}
            if isinstance(exc, RateLimitedError)
            else None
        ),
        banked=not isinstance(exc, RateLimitedError),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Located in the payload, as every other entry point's refusal of the same model is.
    located = (
        {**error, "loc": error["loc"][1:]} if error["loc"][:1] == ("body",) else error
        for error in exc.errors()
    )
    return await potter_error_handler(request, PayloadInvalidError.refusing(located))


_API = "/api/v1"
_COMMANDS = f"{_API}{commands_router.prefix}/"


@app.exception_handler(HTTPException)
async def routing_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(cause := exc.__cause__, PotterError):
        # FastAPI wraps what its body parse raised (`BodyBoundMiddleware`'s refusal) as a 400.
        return await potter_error_handler(request, cause)
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


class RouterMount(NamedTuple):
    router: APIRouter
    prefix: str
    # Applied at the mount, so no route under it can forget it; `None` is public by declaration.
    gate: Callable[..., object] | None


API_ROUTERS: tuple[RouterMount, ...] = (
    RouterMount(_contract, "", None),
    RouterMount(_health, _API, None),
    RouterMount(reads_router, _API, open_caller),
    RouterMount(commands_router, _API, open_caller),
    RouterMount(auth_flow_router, _API, None),
)
_ROUTES = [
    route for mount in API_ROUTERS for route in mount.router.routes if isinstance(route, APIRoute)
]


def _off_catalogue(route: APIRoute) -> str | None:
    """A session route out of the catalogue: named by no kind (its rate draw would 500 its first
    request, `deps.py::_drawn`), a read's route other than the one its row's loop made, or a
    command route answering other than the kind applies."""
    where = f"{sorted(route.methods or ())} {route.path}"
    if route.name in ReadKind:
        made = route is READ_ROUTES[ReadKind(route.name)]
        return None if made else f"{where} serves read {route.name!r} beside its row's route"
    kind = next((kind for kind in CommandKind if kind == route.name), None)
    if kind is None:
        return f"{where} is named {route.name!r}, no kind"
    applied = command_answer(kind)
    # A task kind applying no model answers its launch, whose handle its route projects.
    if applied is None and kind.answering == "task":
        return None
    # Set by FastAPI on every route, though its stubs do not declare it.
    served = vars(route)["response_model"]
    if served != (applied or CommandAcceptedBody):
        return f"{where} serves {served!r}; {kind} applies {applied!r}"
    return None


# Refused at import, over the copies the mounts carry: a session route is named by its row.
if _faults := [
    fault
    for mount in API_ROUTERS
    if mount.gate is open_caller
    for route in mount.router.routes
    if isinstance(route, APIRoute) and (fault := _off_catalogue(route)) is not None
]:
    raise RuntimeError("the surface is out of the catalogue:\n  " + "\n  ".join(_faults))
for _mount in API_ROUTERS:
    app.include_router(
        _mount.router,
        prefix=_mount.prefix,
        dependencies=[] if _mount.gate is None else [Depends(_mount.gate)],
    )


def _request_bodies() -> Iterator[type[BaseModel]]:
    for route in _ROUTES:
        # FastAPI sets `dependant` on the instance outside `APIRoute`'s declared attributes.
        for param in vars(route)["dependant"].body_params:
            body = param.field_info.annotation
            if isinstance(body, type) and issubclass(body, BaseModel):
                yield body


def _open_request_objects() -> set[str]:
    opened: set[str] = set()
    for body in _request_bodies():
        schema = body.model_json_schema()
        opened |= {
            node["title"]
            for node in (schema, *schema.get("$defs", {}).values())
            if "properties" in node and node.get("additionalProperties") is not False
        }
    return opened


# A request object refuses an unknown key (`StrictModel`), or a misspelled knob runs as its default.
assert not (_open := _open_request_objects()), f"request bodies keep unknown keys: {sorted(_open)}"
_deferred = sorted(
    {body.__name__ for body in _request_bodies() if not issubclass(body, RequestBody)}
)
assert not _deferred, f"request bodies that are no `RequestBody`: {_deferred}"

# The largest body any route accepts: an upload at its cap, beside the one form part bounded by the
# `max_part_size` a route's form parse runs with (FastAPI calls `Request.form()` bare).
MAX_REQUEST_BYTES = (
    MAX_UPLOAD_BYTES + inspect.signature(Request.form).parameters["max_part_size"].default
)


class BodyBoundMiddleware:
    """Pure ASGI, so a stream passes through untouched: a body past `MAX_REQUEST_BYTES` is refused
    by its declared length before a byte is read, and by the bytes counted as they arrive, before
    any parse spools it — ahead of every gate a route's dependencies hold. A counted overrun raises
    into the read that met it, and the app's own handler answers and banks that one refusal."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
            await _too_large(scope)(scope, receive, send)
            return
        counted = 0

        async def bounded() -> Message:
            nonlocal counted
            message = await receive()
            counted += len(message.get("body", b""))
            if counted > MAX_REQUEST_BYTES:
                raise ContentTooLargeError(_TOO_LARGE)
            return message

        await self.app(scope, bounded, send)


_TOO_LARGE = f"A request body holds at most {MAX_REQUEST_BYTES} bytes."


def _too_large(scope: Scope) -> JSONResponse:
    return _error_response(
        Request(scope),
        status=ContentTooLargeError.http_status,
        code=ContentTooLargeError.code,
        message=_TOO_LARGE,
    )


# Innermost of the stack, so the refusal carries every header the outer seams add.
app.add_middleware(BodyBoundMiddleware)

# Credentialed, so never `*`: the methods the routes serve, and the headers a client sends beyond
# the CORS-safelisted ones (the command key, the validators a conditional read revalidates with, and
# the feed subject a read names).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    # Starlette types `methods` optional; an `APIRoute` always sets it.
    allow_methods=sorted({method for route in _ROUTES for method in route.methods or ()}),
    allow_headers=["Idempotency-Key", "If-Modified-Since", "If-None-Match", FEED_SUBJECT_HEADER],
)

# Tokens never appear past this boundary (decisions.md § 0002): downstream code sees only IdentityContext.
install_oidc_middleware(app)
# Outside the OIDC seam, so it reads the session that seam resolved.
app.add_middleware(OutsideVisitsMiddleware)


WEBAPP_DIR = webapp_static_root()

# Inside the header seam; GZip passes ``text/event-stream`` through, so SSE is never buffered.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(ResponseHeadersMiddleware, webapp_dir=WEBAPP_DIR)

# A catch-all, so it MUST stay the last route registered: Starlette resolves in order.
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
