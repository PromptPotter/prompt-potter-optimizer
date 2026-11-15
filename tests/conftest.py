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
from offline_world import NOT_A_KEY, PROVIDER_KEYS, FakeBackend, FakeLLM, Router, install_network
from pydantic import SecretStr

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
def own_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each fact's data root is its own: a door that resolves the home (`paths.py::user_data_root`)
    lands in this fact's tree, never the session's, which no fact leaves a file in. Beside
    `tmp_path`, never in it: a fact's own data root there is one a landing proves file by file."""
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(tmp_path.parent / f"{tmp_path.name}-home"))


@pytest.fixture
def optimizing_memory() -> Iterator[None]:
    """What an optimizing run remembers (`scoring_scope.py::run_memory`), for a test driving a cycle
    outside the entry that scopes it; unasked, a memory read raises as it does outside a run."""
    with scoped(memory=OpenAt()):
        yield


@pytest.fixture
def built_stores(tmp_path: Path) -> Stores:
    return workspace(tmp_path)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Router]:
    """The offline run's world; a journey sets what its edges answer (`llm.says`, `backend.faults`,
    `rates` — then `pricing.refresh_rates(force=True)` fetches them).

    Its provider caps no rate and binds one account, the terminal's, as a sending entry does."""
    for key in PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, SecretStr(NOT_A_KEY))
    monkeypatch.setattr(settings, "RATE_LIMITS", {})
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    restore = install_network(world)
    send_pacing.cache_clear()
    with scoped(account=workspace(tmp_path / "account")):
        yield world
    restore()
    send_pacing.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"
