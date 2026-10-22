from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import workspace
from offline_world import NOT_A_KEY, PROVIDER_KEYS, FakeBackend, FakeLLM, Router, install_network
from pydantic import SecretStr

from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import binding_optimizer
from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.identity import default_identity


@pytest.fixture(autouse=True, scope="session")
def completed_registries() -> None:
    complete_registries(every_treatment=False)


@pytest.fixture(autouse=True)
def bound_potter() -> Iterator[None]:
    """What every run seam binds (`runner/entry.py`), for a test driving a node without one."""
    with binding_optimizer(OptimizationConfig(degradation_threshold=0.0)):
        yield


@pytest.fixture
def built_stores(tmp_path: Path) -> Stores:
    return workspace(tmp_path)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Router]:
    """The offline run's world; a journey sets what its edges answer (`llm.says`, `backend.faults`).

    Its provider caps no rate and rates one account, as a sending entry binds it."""
    for key in PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, SecretStr(NOT_A_KEY))
    monkeypatch.setattr(settings, "RATE_LIMITS", {})
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    restore = install_network(world)
    get_llm_client.cache_clear()
    tenant = set_rate_tenant(str(default_identity().tenant_id))
    yield world
    tenant.var.reset(tenant)
    restore()
    get_llm_client.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"
