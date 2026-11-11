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

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.jobs.coupon import bind_account
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import OptimizerChoice
from promptpotter.infrastructure.llm import pricing
from promptpotter.infrastructure.llm.base import wire_client
from promptpotter.infrastructure.llm.registry import send_pacing
from promptpotter.infrastructure.store.archive_queries import OpenAt
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.run_scope import scoped


@pytest.fixture(autouse=True, scope="session")
def completed_registries() -> None:
    complete_registries(every_treatment=False)


@pytest.fixture(autouse=True)
def bound_potter() -> Iterator[None]:
    """The selection an optimizing run scopes (`runner/entry.py`), for a test driving a node
    without one."""
    with scoped(optimizer=select_optimizer(OptimizerChoice())):
        yield


@pytest.fixture
def optimizing_memory() -> Iterator[None]:
    """What an optimizing run remembers (`loop_start.py::run_memory`), for a test driving a cycle
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
    # The fetched price list lands in this journey's tree, never the checkout's cache.
    monkeypatch.setattr(pricing, "cache_path", lambda: tmp_path / "fetched_rates.json")
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    restore = install_network(world)
    send_pacing.cache_clear()
    wire_client.cache_clear()
    unbind = bind_account(workspace(tmp_path / "account"))
    yield world
    unbind()
    restore()
    send_pacing.cache_clear()
    wire_client.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"
