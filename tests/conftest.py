from __future__ import annotations

import os
import tempfile

# CI's configuration wherever the suite is launched, set before anything reads it: no desk `.env`,
# no desk workspace.
_HOME = tempfile.TemporaryDirectory(prefix="pp-tests-", ignore_cleanup_errors=True)
os.environ["PROMPTPOTTER_HOME"] = _HOME.name
os.environ["PROMPTPOTTER_ENV_FILE"] = os.path.join(_HOME.name, ".env")

from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import workspace
from offline_world import (
    NOT_A_KEY,
    PROVIDER_KEYS,
    FakeBackend,
    FakeLLM,
    Router,
    install_network,
    refuse_network,
)
from pydantic import SecretStr
from world import World

from promptpotter.application.maintenance.landing import complete_registries
from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.registry import send_pacing
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store.archive_queries import OpenAt
from promptpotter.infrastructure.store.stores import Stores


@pytest.fixture(autouse=True, scope="session")
def completed_registries() -> None:
    complete_registries()


@pytest.fixture(autouse=True)
def refused_network() -> Iterator[None]:
    """No fact sends for real: every send is refused and named, so a fact needing a network edge
    asks for `offline`, whose world answers over this one for its span."""
    refused: set[str] = set()
    restore = refuse_network(refused)
    yield
    restore()
    assert not refused, f"a fact sent without asking for `offline`: {sorted(refused)}"


@pytest.fixture(autouse=True)
def own_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each fact's data root is its own: a door that resolves the home (`paths.py::user_data_root`)
    lands in this fact's tree, never the session's, which no fact leaves a file in. A directory of
    `tmp_path`, never `tmp_path` itself: a landing proves every file of the data root, and a fact's
    scratch is none of them; pytest's retention takes it with `tmp_path`."""
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(tmp_path / "home"))


@pytest.fixture
def optimizing_memory() -> Iterator[None]:
    """What an optimizing run remembers (`scoring_scope.py::run_memory`), for a test driving a cycle
    outside the entry that scopes it; unasked, a memory read raises as it does outside a run."""
    with scoped(memory=OpenAt()):
        yield


@pytest.fixture
def built_stores() -> Stores:
    return workspace()


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Router]:
    """The offline run's world; a journey sets what its edges answer (`llm.says`, `backend.faults`,
    `rates` — then `pricing.refresh_rates(force=True)` fetches them).

    Its provider caps no rate and binds one account, the terminal's, as a sending entry does."""
    for key in PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, SecretStr(NOT_A_KEY))
    monkeypatch.setattr(settings, "RATE_LIMITS", {})
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    restore = install_network(world)
    send_pacing.cache_clear()
    with scoped(account=workspace()):
        yield world
    restore()
    send_pacing.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"


@pytest.fixture
def world(built_stores: Stores, offline: Router, tmp_path: Path) -> World:
    """The edge tier's world (`world.py`): campaigns run through the real entry, offline."""
    return World(built_stores, offline, tmp_path / "world")
