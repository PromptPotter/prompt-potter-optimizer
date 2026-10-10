from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import workspace

from promptpotter.application.optimizer_manifest import _BOUND, resolve_optimizer
from promptpotter.infrastructure.store.stores import Stores


@pytest.fixture(autouse=True)
def bound_potter() -> Iterator[None]:
    """What every run seam binds (`runner/entry.py`), for a test driving a node without one."""
    token = _BOUND.set(resolve_optimizer("potter", {}))
    yield
    _BOUND.reset(token)


@pytest.fixture
def built_stores(tmp_path: Path) -> Stores:
    return workspace(tmp_path)
