from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import (
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from promptpotter.shared.identity import IdentityContext

PROMPTPOTTER_AUTH_OFF_ENV = "PROMPTPOTTER_AUTH"


def _auth_off() -> bool:
    return os.environ.get(PROMPTPOTTER_AUTH_OFF_ENV, "").strip().lower() == "off"


def _dev_without_providers(request: Request) -> bool:
    """Development with ZERO auth providers can't complete a login, so treat it as auth-off. Gated on
    ``ENVIRONMENT == "development"`` EXPLICITLY — a misconfigured production stays 401, never opens."""
    if settings.ENVIRONMENT != "development":
        return False
    bundle = getattr(request.app.state, "identity_bundle", None)
    return bundle is not None and not bundle.config.configured


def resolve_identity(request: Request) -> IdentityContext:
    """Stage-1 identity resolver. Auth-off resolves the SAME single operator the CLI does, so a registered
    developer's terminal runs and their web session share one workspace; otherwise 401 without a session."""
    if _auth_off() or _dev_without_providers(request):
        return registered_or_default_identity()
    identity_ctx: IdentityContext | None = getattr(request.state, "identity_ctx", None)
    if identity_ctx is None:
        raise UnauthorizedError("sign-in required")
    return identity_ctx


IdentityDep = Annotated[IdentityContext, Depends(resolve_identity)]


def build_stores_from_identity(identity: IdentityDep) -> Stores:
    """Bridge :data:`IdentityDep` into :func:`build_stores`. The API serves one process-global workspace:
    an inner sandbox is reached by DESCENT (``?descend=``), never by rebuilding a store at another root."""
    return build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)


StoresDep = Annotated[Stores, Depends(build_stores_from_identity)]


def get_backend_or_404(backend_id: str, stores: Stores) -> BackendConnection:
    backend = stores.backends.get(backend_id)
    if not backend:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    return backend


def get_cycle_dir_or_404(campaign_id: str, cycle_id: str, stores: Stores) -> Path:
    cycle_dir = cycle_dir_for(stores.base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id))
    if not cycle_dir.exists():
        raise NotFoundError(f"Cycle '{campaign_id}/{cycle_id}' not found")
    return cycle_dir


def decode_descend(descend: str | None) -> CyclePath:
    """Parse a ``?descend=`` tail into a :data:`CyclePath`, mirroring the webapp's ``encodeDescend``. The
    wire CODEC and nothing more — component validation and the descent itself are ``descend_store``'s."""
    if not descend:
        return ()
    hops: list[CycleHop] = []
    for seg in descend.split("~"):
        cmp, sep, cyc = seg.partition("::")
        if not sep or not cmp or not cyc:
            raise BadRequestError(f"Malformed descend hop: {seg!r}")
        hops.append(CycleHop(campaign_id=cmp, cycle_id=cyc))
    return tuple(hops)


def get_job_registry(request: Request) -> JobRegistry:
    """The process-wide :class:`JobRegistry` off ``app.state`` — the same instance the launcher writes to,
    so a read route sees every in-flight run. Missing is a programmer error, surfaced as 503."""
    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise ServiceUnavailableError("job registry not initialised")
    return registry


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]


__all__ = [
    "IdentityDep",
    "JobRegistryDep",
    "StoresDep",
    "build_stores_from_identity",
    "decode_descend",
    "get_backend_or_404",
    "get_cycle_dir_or_404",
    "resolve_identity",
]
