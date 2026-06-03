"""Shared APIRouter for the campaigns package.

Every submodule (``registry``, ``cycles``, ``ledger``, ``lineage``,
``files``) decorates this one router object. The package ``__init__``
imports the submodules so their route decorators run, then re-exports the
populated router for ``main.py`` to mount.
"""

from __future__ import annotations

from fastapi import APIRouter

campaigns_router = APIRouter(tags=["Campaigns"])
