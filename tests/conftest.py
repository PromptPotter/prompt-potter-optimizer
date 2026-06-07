"""Shared test fixtures — the foundation every test rides.

Builders and on-disk seeding live in ``_factories``; this exposes them as
fixtures so a test body is arrange (fixture) → act → assert, never inline
scaffolding.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _factories import CycleHandle, seed_campaign_cycle

from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.identity import default_identity


@pytest.fixture
def built_stores(tmp_path: Path) -> Stores:
    """A real ``Stores`` rooted in ``tmp_path`` (the default identity)."""
    return build_stores(
        default_identity(),
        projects_root=tmp_path / "projects",
        datasets_root=tmp_path / "datasets",
    )


@pytest.fixture
def seeded_campaign_cycle(built_stores: Stores) -> tuple[Stores, CycleHandle]:
    """One canonical campaign + cycle on disk; returns ``(stores, handle)``."""
    handle = seed_campaign_cycle(built_stores.base_dir, with_ledger=True)
    return built_stores, handle


@pytest.fixture
def patch_pointer_root(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> tuple[Stores, Path]:
    """Redirect the module-level pointer root at the foundation stores' projects
    root, so bare ``save_active_pointer`` calls deep inside fork machinery land in
    the temp tree. Returns ``(stores, active-pointer path)``."""
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.DEFAULT_PROJECTS_ROOT", built_stores.projects_root
    )
    ptr = built_stores.projects_root / built_stores.tenant_id / ".workspace" / "active_session.json"
    return built_stores, ptr
