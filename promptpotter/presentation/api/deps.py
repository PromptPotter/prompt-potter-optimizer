"""Shared FastAPI dependencies + small helpers for the read API routers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException

from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.identity import IdentityContext, default_identity


def resolve_identity() -> IdentityContext:
    """Stage-0: always the auth-off single-operator default.

    Stage 1 (M12) replaces this with OIDC verification — the only code change
    needed to enable multi-tenant SaaS. Every other API site keeps consuming
    :data:`IdentityDep` / :data:`StoreDep` without modification.
    """
    return default_identity()


IdentityDep = Annotated[IdentityContext, Depends(resolve_identity)]


def build_stores_from_identity(identity: IdentityDep) -> Stores:
    """FastAPI factory bridging :data:`IdentityDep` into :func:`build_stores`."""
    return build_stores(identity)


StoreDep = Annotated[Stores, Depends(build_stores_from_identity)]


def get_backend_or_404(backend_id: str, store: Stores) -> BackendConnection:
    """Return the registered backend or raise 404."""
    backend = store.backends.get(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")
    return backend


def read_text_or_404(path: Path, label: str) -> str:
    """Read *path* as UTF-8 text or raise 404 with *label* in the detail."""
    if not path.exists():
        raise HTTPException(404, f"{label} not found at {path.name}")
    return path.read_text(encoding="utf-8")


__all__ = [
    "IdentityDep",
    "StoreDep",
    "build_stores_from_identity",
    "get_backend_or_404",
    "read_text_or_404",
    "resolve_identity",
]
