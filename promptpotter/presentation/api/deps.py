from __future__ import annotations

import inspect
import re
from typing import Annotated, Any, get_origin

from fastapi import Depends, Header, Query, Request, UploadFile
from pydantic import Field, create_model
from pydantic.fields import FieldInfo

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.caller import Caller
from promptpotter.application.commands.dispatcher import MAX_IDEMPOTENCY_KEY_CHARS
from promptpotter.application.jobs.quota import refuse_request_rate
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.read_params import ReadParams
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.command_kinds import CommandKind, ReadKind
from promptpotter.domain.strict_model import MAX_UPLOAD_BYTES, RequestBody
from promptpotter.infrastructure.identity.accounts import IdentityBundle
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import (
    PayloadInvalidError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from promptpotter.shared.identity import IdentityContext


def auth_is_open() -> bool:
    """Only ever said: a box with no sign-in provider configured answers 401, never opens."""
    return settings.PROMPTPOTTER_AUTH == "off"


def resolve_identity(request: Request) -> IdentityContext:
    if auth_is_open():
        return registered_or_default_identity()
    identity_ctx: IdentityContext | None = getattr(request.state, "identity_ctx", None)
    if identity_ctx is None:
        raise UnauthorizedError("sign-in required")
    return identity_ctx


IdentityDep = Annotated[IdentityContext, Depends(resolve_identity)]


def build_stores_from_identity(identity: IdentityDep) -> Stores:
    """An inner sandbox is reached by DESCENT (``?descend=``), never by a store at another root."""
    return build_stores(identity, projects_root=default_projects_root())


StoresDep = Annotated[Stores, Depends(build_stores_from_identity)]


def get_job_registry(request: Request) -> JobRegistry:
    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise ServiceUnavailableError("job registry not initialised")
    return registry


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]


def require_identity_bundle(request: Request) -> IdentityBundle:
    """The install's sign-in configuration, set at startup; refused until it is."""
    bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
    if bundle is None:
        raise ServiceUnavailableError(
            "identity backend not initialised", code="identity_not_initialised"
        )
    return bundle


def _drawn(request: Request, identity: IdentityDep) -> None:
    """The request drawn on its principal's rate, read or write as the kind its route is named by
    (every session route is named by one), before anything is built for it."""
    route = request.scope.get("route")
    refuse_request_rate(identity, read=isinstance(_NAMED[getattr(route, "name", "")], ReadKind))


def open_caller(
    request: Request,
    _rate: Annotated[None, Depends(_drawn)],
    stores: StoresDep,
    jobs: JobRegistryDep,
) -> Caller:
    """A session mount's gate: the request drawn on its rate first (`_drawn`, solved before the
    stores), then its caller on the request's one `StoresDep`. It reads only: an account opens at
    sign-in or at its first act (`account_of`), never at a read."""
    configured = tuple(require_identity_bundle(request).config.configured)
    return Caller(stores, jobs, configured)


_NAMED: dict[str, CommandKind | ReadKind] = {k.value: k for k in (*CommandKind, *ReadKind)}


CallerDep = Annotated[Caller, Depends(open_caller)]


def read_input(model: type[ReadParams]) -> Any:
    """A read route's one input: its row's `ReadParams` model, each field a path parameter where
    the model's `route` names it, else a query one on a GET (a list repeats) and a field of one
    `RequestBody` named for the model on a POST. Each keeps its field's own constraints and
    description, so a value the model refuses is refused before the route runs."""
    in_path = set(re.findall(r"\{(\w+)", model.route))
    params: list[inspect.Parameter] = []
    carried: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if model.method == "POST" and (field.alias or name) not in in_path:
            carried[name] = (field.annotation, field)
            continue
        spec: FieldInfo = (
            Query(description=field.description)
            if get_origin(field.annotation) is list
            # FastAPI makes a plain field a path parameter where the route's path names it.
            else Field(description=field.description)
        )
        spec.metadata = list(field.metadata)
        params.append(
            inspect.Parameter(
                field.alias or name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[field.annotation, spec],
                default=inspect.Parameter.empty
                if field.is_required()
                else field.get_default(call_default_factory=True),
            )
        )
    body: type[RequestBody] | None = None
    if carried:
        body = create_model(
            f"{model.__name__.removesuffix('Params')}Body", __base__=RequestBody, **carried
        )
        params.append(inspect.Parameter("body", inspect.Parameter.KEYWORD_ONLY, annotation=body))

    def build(**sent: Any) -> ReadParams:
        if body is not None:
            posted = sent.pop("body")
            sent |= {name: getattr(posted, name) for name in carried}
        return PayloadInvalidError.validating(model, sent)

    signature = inspect.Signature(params, return_annotation=model)
    build.__signature__ = signature  # type: ignore[attr-defined]
    return Depends(build)


async def bounded_read(upload: UploadFile) -> bytes:
    """At most one byte past ``MAX_UPLOAD_BYTES``: the bytes' own bound (`UploadBytes`) refuses
    that byte, so this read holds no policy of its own."""
    return await upload.read(MAX_UPLOAD_BYTES + 1)


def sent_idempotency_key(
    key: Annotated[str, Header(alias="Idempotency-Key", max_length=MAX_IDEMPOTENCY_KEY_CHARS)] = "",
) -> str:
    """Read off the header alone: `CommandCall` refuses a command that names no key."""
    return key


IdempotencyKeyDep = Annotated[str, Depends(sent_idempotency_key)]
