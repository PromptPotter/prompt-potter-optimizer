"""Shared FastAPI dependencies + small helpers for the read API routers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException

from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store import Stores, build_stores

StoreDep = Annotated[Stores, Depends(build_stores)]


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


__all__ = ["StoreDep", "get_backend_or_404", "read_text_or_404"]
