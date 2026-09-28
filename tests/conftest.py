"""Shared test fixtures.

Two fixtures: a real ``Stores`` on a temp tree, used by the resume data-integrity tests, and
the optimizer binding every run seam makes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from promptpotter.application.optimizer_manifest import _BOUND, resolve_optimizer
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import default_identity


@pytest.fixture(autouse=True)
def bound_potter() -> Iterator[None]:
    """The optimizer every run seam binds before a node is read (`runner/entry.py`), bound for
    the tests that drive a node without that seam."""
    token = _BOUND.set(resolve_optimizer("potter", {}))
    yield
    _BOUND.reset(token)


@pytest.fixture
def built_stores(tmp_path: Path) -> Stores:
    """A real ``Stores`` rooted in ``tmp_path`` (the default identity)."""
    return build_stores(
        default_identity(),
        projects_root=tmp_path / "projects",
        benchmarks_root=tmp_path / "datasets",
    )
