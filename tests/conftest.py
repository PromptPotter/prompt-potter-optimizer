"""Shared test fixtures.

One fixture survives the suite cut: a real ``Stores`` on a temp tree, used by
the resume data-integrity tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
