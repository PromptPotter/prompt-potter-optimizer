"""Shared ``APIRouter`` for the campaigns package — every submodule decorates this one object, and the package
``__init__`` imports them so the decorators run before the populated router is re-exported."""

from __future__ import annotations

from fastapi import APIRouter

campaigns_router = APIRouter(tags=["Campaigns"])
