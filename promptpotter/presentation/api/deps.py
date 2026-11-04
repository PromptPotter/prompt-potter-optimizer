from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request, UploadFile

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.datasets.ingest import MAX_UPLOAD_BYTES
from promptpotter.application.jobs.quota import refuse_request_rate
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import (
    BadRequestError,
    ContentTooLargeError,
    NotFoundError,
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


def admit_session(request: Request, identity: IdentityDep) -> None:
    """A read is what the catalogue names one (`ReadKind`), whatever its verb."""
    route = request.scope.get("route")
    refuse_request_rate(identity, read=getattr(route, "name", None) in ReadKind)


async def read_upload(request: Request, upload: UploadFile) -> bytes:
    """Refused past ``MAX_UPLOAD_BYTES`` by the declared length, and still counted while streaming:
    the header can lie or be absent."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise _too_large(declared)
    buf = bytearray()
    while chunk := await upload.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            raise _too_large(len(buf))
    return bytes(buf)


def _too_large(observed: int | str) -> ContentTooLargeError:
    return ContentTooLargeError(
        f"Upload {observed} bytes exceeds the per-file cap of {MAX_UPLOAD_BYTES} bytes."
    )


def require_site_records_token(request: Request) -> None:
    """Unset, the door does not exist. Set, it opens to the bearer of it and to no session."""
    token = settings.SITE_RECORDS_TOKEN.get_secret_value().strip()
    if not token:
        raise NotFoundError("Not found")
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
        raise UnauthorizedError("site-records token required")


def require_idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not key or not key.strip():
        raise BadRequestError(
            "Idempotency-Key header is required on every command.",
            code="idempotency_key_missing",
        )
    return key.strip()


IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def get_job_registry(request: Request) -> JobRegistry:
    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise ServiceUnavailableError("job registry not initialised")
    return registry


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]
