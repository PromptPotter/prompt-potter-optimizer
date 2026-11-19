"""Probe: writers whose dir the store-2 branch never creates. Read-only against the repo; all
writes land in a temp home."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="rvs2-"))
os.environ["PROMPTPOTTER_HOME"] = str(home)

import promptpotter  # noqa: E402

print("package from", Path(promptpotter.__file__).parent)

from promptpotter.config import paths  # noqa: E402
from promptpotter.infrastructure.store.data_root import mint_install  # noqa: E402

print("data root", paths.user_data_root())
mint_install(paths.user_data_root())


def probe(name, fn):
    try:
        out = fn()
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        print(f"OK   {name}: {out!r}"[:200])
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {type(exc).__name__}: {exc}"[:300])


# 1. model capabilities cache under a fresh workspace (every run's wiring calls ensure_*).
from promptpotter.infrastructure.llm import capabilities as caps  # noqa: E402
import httpx  # noqa: E402

ws = home / "projects" / "default"
ws.mkdir(parents=True, exist_ok=True)


class _Resp:
    content = b'{"data":[{"id":"openai/gpt-x"}]}'

    def raise_for_status(self):
        return None


async def _get(self, url, *a, **k):
    return _Resp()


httpx.AsyncClient.get = _get  # type: ignore[method-assign]
probe("capabilities refresh on fresh workspace", lambda: caps.refresh_model_capabilities(ws))

# 2. machine slots for a cells_hold_the_machine connector.
from promptpotter.infrastructure.backend import MachineSlots  # noqa: E402

slots = MachineSlots(paths.jobs_dir(paths.default_projects_root()), "harbor", 2, compose_overlay=None)
probe("machine slot lock (turn)", lambda: slots._lock("turn"))

# 3. harbor panel materialize into a fresh tenant tier.
from promptpotter.application.datasets.harbor_panel import write_task_panel  # noqa: E402

out = ws / "materialized" / "x"
probe(
    "write_task_panel into fresh materialized dir",
    lambda: write_task_panel(
        [{"q": "Q?", "a": "A", "d": [{"title": "t", "text": "body"}]}],
        out,
        layers=["longseal/base", "longseal/one-step"],
        columns={"question": "q", "answer": "a", "documents": "d"},
        task_prefix="t",
        provenance="probe",
    ),
)

# 4. API key put on an account dir not yet made.
from promptpotter.infrastructure.store.wallet_store import TenantApiKeyStore  # noqa: E402
from pydantic import SecretStr  # noqa: E402

probe(
    "api key put, account dir absent",
    lambda: TenantApiKeyStore(home / "projects" / "fresh-tenant").put("openrouter", SecretStr("k")),
)

# 5. readout mirror swallow: NotFoundError is no OSError.
from promptpotter.shared.errors import NotFoundError  # noqa: E402

print("NotFoundError is OSError:", issubclass(NotFoundError, OSError))
