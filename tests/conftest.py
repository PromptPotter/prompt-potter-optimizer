from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

# CI's configuration wherever the suite is launched, set before anything reads it: no desk `.env`,
# no desk workspace.
_HOME = tempfile.TemporaryDirectory(prefix="pp-tests-", ignore_cleanup_errors=True)
os.environ["PROMPTPOTTER_HOME"] = _HOME.name
os.environ["PROMPTPOTTER_ENV_FILE"] = os.path.join(_HOME.name, ".env")

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from factories import workspace
from offline_world import (
    Router,
    refuse_network,
)
from world import World, offline_network, shared_run

from promptpotter.application.maintenance.landing import complete_registries
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
def own_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Each fact's data root is its own: a door that resolves the home (`paths.py::user_data_root`)
    lands in this fact's tree, never the session's, which no fact leaves a file in. A directory of
    `tmp_path`, never `tmp_path` itself: a landing proves every file of the data root, and a fact's
    scratch is none of them; pytest's retention takes it with `tmp_path`. Its account is bound as a
    sending entry binds the terminal's, so a spend book (`factories.py::spend_book`) draws on it
    with no network asked for."""
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(tmp_path / "home"))
    with scoped(account=workspace()):
        yield


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
    `rates` — then `pricing.refresh_rates(force=True)` fetches them)."""
    with offline_network(monkeypatch) as world:
        yield world


_DSPY_CHILD = """
import json, sys
from pathlib import Path
from test_connectors import dspy_cells
from test_spend import dspy_billed
out = Path(sys.argv[1])
(out / "spend").mkdir(); (out / "connectors").mkdir()
print(json.dumps({"spend": dspy_billed(out / "spend"), "connectors": dspy_cells(out / "connectors")}))
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The facts reading the dspy child run first, so it runs while this process is still small:
    the gate meters the two together (`scripts/processes.py::close_job`)."""
    items.sort(key=lambda item: "dspy_child" not in getattr(item, "fixturenames", ()))


@pytest.fixture(scope="session")
def dspy_child(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """What every dspy cell paid, run once for the suite in a child, where dspy is imported."""
    out, tests = tmp_path_factory.mktemp("dspy"), Path(__file__).resolve().parent
    child = subprocess.run(
        [sys.executable, "-c", _DSPY_CHILD, str(out)],
        cwd=tests,
        env={
            **os.environ,
            # The repo root first: the child imports the promptpotter this suite runs, never the install's.
            "PYTHONPATH": os.pathsep.join(
                (str(tests.parent), str(tests), str(tests.parent / "scripts"))
            ),
            "PROMPTPOTTER_HOME": str(out / "home"),
            "PROMPTPOTTER_ENV_FILE": str(out / ".env"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, f"the dspy cells did not run: {child.stderr[-2000:]}"
    paid: dict[str, Any] = json.loads(child.stdout.splitlines()[-1])
    return paid


@pytest.fixture(autouse=True, scope="session")
def cold_start(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The process's first run pays its cold start (its first imports, the landing's key): paid
    once here, by a mint, never by whichever fact or shared run comes first. After the dspy child,
    which runs while this process is small; a session running no world pays neither."""
    asked = [getattr(item, "fixturenames", ()) for item in request.session.items]
    if any("dspy_child" in names for names in asked):
        request.getfixturevalue("dspy_child")
    if any("world" in names for names in asked):
        shared_run(tmp_path_factory, lambda world: world.minted())


@pytest.fixture
def world(built_stores: Stores, offline: Router, tmp_path: Path) -> World:
    """The edge tier's world (`world.py`): campaigns run through the real entry, offline."""
    return World(built_stores, offline, tmp_path / "world")
