"""Shared ``APIRouter`` for the datasets package — every submodule decorates this one object, and the
package ``__init__`` imports them so the decorators run before the populated router is re-exported."""

from __future__ import annotations

from fastapi import APIRouter

datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])
