"""Shared FastAPI dependencies + small helpers for the read API routers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.errors import NotFoundError, ServiceUnavailableError, UnauthorizedError
from promptpotter.shared.identity import IdentityContext

PROMPTPOTTER_AUTH_OFF_ENV = "PROMPTPOTTER_AUTH"


def _auth_off() -> bool:
    return os.environ.get(PROMPTPOTTER_AUTH_OFF_ENV, "").strip().lower() == "off"


def resolve_identity(request: Request) -> IdentityContext:
    """Stage-1 identity resolver.

    ``PROMPTPOTTER_AUTH=off`` → :func:`registered_or_default_identity`, the
    *same* single-operator resolution the CLI uses: a registered developer
    (default-claim marker) resolves to their own tenant, else anonymous
    ``default``. This keeps the auth-off web surface and terminal runs in one
    workspace — auth-off web reading empty ``default`` while the CLI wrote to
    the registered tenant was the drift. Otherwise consume the
    :class:`IdentityContext` populated by :func:`install_oidc_middleware`;
    missing/expired session raises 401 ``unauthenticated``. Every other API
    site keeps consuming :data:`IdentityDep` / :data:`StoreDep` unchanged.
    """
    if _auth_off():
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


def get_draft_registry(request: Request) -> DraftCampaignRegistry:
    """Pull the draft-campaign registry off ``app.state``; missing is a programmer error."""
    registry: DraftCampaignRegistry | None = getattr(request.app.state, "draft_campaigns", None)
    if registry is None:
        raise ServiceUnavailableError("draft-campaign registry not initialised")
    return registry


__all__ = [
    "IdentityDep",
    "StoreDep",
    "build_stores_from_identity",
    "get_backend_or_404",
    "get_draft_registry",
    "read_text_or_404",
    "resolve_identity",
]
