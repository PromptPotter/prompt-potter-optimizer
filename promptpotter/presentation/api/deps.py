"""Shared FastAPI dependencies + small helpers for the read API routers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
from promptpotter.application.jobs import JobRegistry
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store import Stores, build_stores, cycle_dir_for
from promptpotter.shared.errors import NotFoundError, ServiceUnavailableError, UnauthorizedError
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


def read_text_or_404(path: Path, label: str) -> str:
    """Read *path* as UTF-8 text or raise 404 with *label* in the message."""
    if not path.exists():
        raise NotFoundError(f"{label} not found at {path.name}")
    return path.read_text(encoding="utf-8")


def get_cycle_dir_or_404(campaign_id: str, cycle_id: str, store: Stores) -> Path:
    """Resolve the per-cycle dir or raise 404 if it doesn't exist.

    The shared seam for the routers that read under a cycle dir — folds the
    repeated ``cycle_dir_for(...)`` + ``exists()`` + ``NotFoundError`` block.
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    if not cycle_dir.exists():
        raise NotFoundError(f"Cycle '{campaign_id}/{cycle_id}' not found")
    return cycle_dir


def get_draft_registry(request: Request) -> DraftCampaignRegistry:
    """Pull the draft-campaign registry off ``app.state``; missing is a programmer error."""
    registry: DraftCampaignRegistry | None = getattr(request.app.state, "draft_campaigns", None)
    if registry is None:
        raise ServiceUnavailableError("draft-campaign registry not initialised")
    return registry


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
    "get_backend_or_404",
    "get_cycle_dir_or_404",
    "get_draft_registry",
    "get_job_registry",
    "read_text_or_404",
    "resolve_identity",
]
