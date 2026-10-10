from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, Request

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CyclePath, decode_cycle_path
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import (
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from promptpotter.shared.identity import IdentityContext

PROMPTPOTTER_AUTH_OFF_ENV = "PROMPTPOTTER_AUTH"


def auth_is_open(bundle: IdentityBundle | None) -> bool:
    if os.environ.get(PROMPTPOTTER_AUTH_OFF_ENV, "").strip().lower() == "off":
        return True
    # Development is asked EXPLICITLY: a misconfigured production stays 401, never opens.
    return (
        settings.ENVIRONMENT == "development"
        and bundle is not None
        and not bundle.config.configured
    )


def resolve_identity(request: Request) -> IdentityContext:
    if auth_is_open(getattr(request.app.state, "identity_bundle", None)):
        return registered_or_default_identity()
    identity_ctx: IdentityContext | None = getattr(request.state, "identity_ctx", None)
    if identity_ctx is None:
        raise UnauthorizedError("sign-in required")
    return identity_ctx


IdentityDep = Annotated[IdentityContext, Depends(resolve_identity)]


def build_stores_from_identity(identity: IdentityDep) -> Stores:
    """An inner sandbox is reached by DESCENT (``?descend=``), never by a store at another root."""
    return build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)


StoresDep = Annotated[Stores, Depends(build_stores_from_identity)]


def get_backend_or_404(backend_id: str, stores: Stores) -> BackendConnection:
    backend = stores.backends.get(backend_id)
    if not backend:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    return backend


def decode_descend(descend: str | None) -> CyclePath:
    try:
        return decode_cycle_path(descend or "")
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


def get_job_registry(request: Request) -> JobRegistry:
    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise ServiceUnavailableError("job registry not initialised")
    return registry


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]


__all__ = [
    "IdentityDep",
    "JobRegistryDep",
    "StoresDep",
    "auth_is_open",
    "build_stores_from_identity",
    "decode_descend",
    "get_backend_or_404",
    "resolve_identity",
]
