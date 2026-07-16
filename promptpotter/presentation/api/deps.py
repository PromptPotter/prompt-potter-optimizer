"""Shared FastAPI dependencies + small helpers for the read API routers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store import Stores, build_stores, cycle_dir_for
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
    """Development with ZERO auth providers configured can't complete a login —
    the auth wall would only dead-end the webapp (404 ``provider_not_configured``,
    401 ``/auth/me``). Treat that like ``PROMPTPOTTER_AUTH=off``: resolve the single
    local operator, giving CLI↔webapp identity parity with no flag.

    Gated on ``ENVIRONMENT == "development"`` (the permissive value, explicitly —
    not ``!= production``) so a production deploy that misconfigures providers stays
    STRICT (401), never silently opens to ``default``. Configure any provider (e.g.
    the local Dex OIDC harness) and the real sign-in flow returns automatically."""
    if settings.ENVIRONMENT != "development":
        return False
    bundle = getattr(request.app.state, "identity_bundle", None)
    return bundle is not None and not bundle.config.configured


def resolve_identity(request: Request) -> IdentityContext:
    """Stage-1 identity resolver.

    ``PROMPTPOTTER_AUTH=off`` (or development with no providers configured —
    :func:`_dev_without_providers`) → :func:`registered_or_default_identity`, the
    *same* single-operator resolution the CLI uses: a registered developer
    (default-claim marker) resolves to their own tenant, else anonymous
    ``default``. This keeps the auth-off web surface and terminal runs in one
    workspace — auth-off web reading empty ``default`` while the CLI wrote to
    the registered tenant was the drift. Otherwise consume the
    :class:`IdentityContext` populated by :func:`install_oidc_middleware`;
    missing/expired session raises 401 ``unauthenticated``. Every other API
    site keeps consuming :data:`IdentityDep` / :data:`StoreDep` unchanged.
    """
    if _auth_off() or _dev_without_providers(request):
        return registered_or_default_identity()
    identity_ctx: IdentityContext | None = getattr(request.state, "identity_ctx", None)
    if identity_ctx is None:
        raise UnauthorizedError("sign-in required")
    return identity_ctx


IdentityDep = Annotated[IdentityContext, Depends(resolve_identity)]


def build_stores_from_identity(identity: IdentityDep) -> Stores:
    """FastAPI factory bridging :data:`IdentityDep` into :func:`build_stores`."""
    return build_stores(identity)


StoreDep = Annotated[Stores, Depends(build_stores_from_identity)]


def get_backend_or_404(backend_id: str, store: Stores) -> BackendConnection:
    """Return the registered backend or raise 404."""
    backend = store.backends.get(backend_id)
    if not backend:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    return backend


def get_cycle_dir_or_404(campaign_id: str, cycle_id: str, store: Stores) -> Path:
    """Resolve the per-cycle dir or raise 404 if it doesn't exist.

    The shared seam for the routers that read under a cycle dir — folds the
    repeated ``cycle_dir_for(...)`` + ``exists()`` + ``NotFoundError`` block.
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    if not cycle_dir.exists():
        raise NotFoundError(f"Cycle '{campaign_id}/{cycle_id}' not found")
    return cycle_dir


def warming_payload(campaign_id: str, cycle_id: str) -> dict[str, Any]:
    """The canonical ``warming_up`` dashboard shape — served at 200 (not 404)
    before a fresh campaign flushes its first ``dashboard.json`` snapshot, so
    the webapp renders an "initialising" placeholder instead of appearing
    offline. One contract for both dashboard routes; callers layer their own
    extras (the active route's runtime flags) on top.
    """
    return {
        "warming_up": True,
        "campaign_id": campaign_id,
        "cycle_id": cycle_id,
        "phase_hint": "origin",
    }


def decode_descend(descend: str | None) -> CyclePath:
    """Parse a ``?descend=`` tail into a :data:`CyclePath` of descent hops.

    Mirrors the webapp's ``encodeDescend`` (``webapp/lib/ids.ts``): ``~``-joined
    ``campaign::cycle`` hops, empty/absent → no descent. This is the wire CODEC and
    nothing more — component-char validation and the descent itself belong to
    ``descend_store`` (``infrastructure/store/stores.py``); here we only reject a
    structurally malformed hop (missing ``::``) as a 400 rather than let it 500
    downstream.
    """
    if not descend:
        return ()
    hops: list[CycleHop] = []
    for seg in descend.split("~"):
        cmp, sep, cyc = seg.partition("::")
        if not sep or not cmp or not cyc:
            raise BadRequestError(f"Malformed descend hop: {seg!r}")
        hops.append(CycleHop(cmp, cyc))
    return tuple(hops)


def get_job_registry(request: Request) -> JobRegistry:
    """Pull the process-wide :class:`JobRegistry` off ``app.state``.

    The same instance the launcher writes to — so a read route sees every
    in-flight run across all tenants. Missing is a programmer error (lifespan
    sets it), surfaced as 503 like the draft registry.
    """
    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise ServiceUnavailableError("job registry not initialised")
    return registry


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]


__all__ = [
    "IdentityDep",
    "JobRegistryDep",
    "StoreDep",
    "build_stores_from_identity",
    "decode_descend",
    "get_backend_or_404",
    "get_cycle_dir_or_404",
    "get_job_registry",
    "resolve_identity",
    "warming_payload",
]
