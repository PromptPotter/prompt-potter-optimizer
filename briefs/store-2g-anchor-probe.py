"""Review probe for arc/store-2f: anchor recognition, brief writers, long paths, races. Temp home only."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="rv2f-"))
os.environ["PROMPTPOTTER_HOME"] = str(home)

import promptpotter  # noqa: E402

print("package from", Path(promptpotter.__file__).parent)

from promptpotter.config import paths  # noqa: E402
from promptpotter.infrastructure.store import io  # noqa: E402


def probe(name, fn):
    try:
        out = fn()
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        print(f"OK   {name}: {out!r}"[:220])
    except BaseException as exc:  # noqa: BLE001
        print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:200]}")


# --- the brief's writers -------------------------------------------------------------------
from promptpotter.infrastructure.llm import capabilities as caps  # noqa: E402
import httpx  # noqa: E402

ws = home / "projects" / "default"


class _Resp:
    content = b'{"data":[{"id":"openai/gpt-x"}]}'

    def raise_for_status(self):
        return None


async def _get(self, url, *a, **k):
    return _Resp()


httpx.AsyncClient.get = _get  # type: ignore[method-assign]
probe("capabilities refresh, workspace absent", lambda: caps.refresh_model_capabilities(ws))
probe("capabilities file landed", lambda: (ws / ".cache").exists() and sorted(p.name for p in (ws / ".cache").iterdir()))

from promptpotter.infrastructure.backend import MachineSlots  # noqa: E402

slots = MachineSlots(paths.jobs_dir(paths.default_projects_root()), "harbor", 2, compose_overlay=None)
probe("machine slot lock (turn), jobs/machine absent", lambda: slots._lock("turn"))

from promptpotter.application.datasets.harbor_panel import write_task_panel  # noqa: E402


def panel(out):
    return write_task_panel(
        [{"q": "Q?", "a": "A", "d": [{"title": "t", "text": "body"}]}],
        out,
        layers=["longseal/base", "longseal/one-step"],
        columns={"question": "q", "answer": "a", "documents": "d"},
        task_prefix="t",
        provenance="probe",
    )


probe("harbor panel into materialized/x", lambda: panel(ws / "materialized" / "x"))

from promptpotter.infrastructure.store.wallet_store import TenantApiKeyStore  # noqa: E402
from pydantic import SecretStr  # noqa: E402

probe(
    "api key put, account dir absent",
    lambda: TenantApiKeyStore(home / "projects" / "fresh-tenant").put("openrouter", SecretStr("k")),
)
from promptpotter.shared.errors import NotFoundError  # noqa: E402

print("NotFoundError is OSError:", issubclass(NotFoundError, OSError))

# --- anchor misreads: non-campaign paths whose segment is named campaigns / cycles ------------
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore  # noqa: E402

tds = TenantDatasetStore(ws)
probe("harbor panel for a dataset named 'cycles'", lambda: panel(tds.materialized_panel_dir("cycles")))
probe("harbor panel for a dataset named 'campaigns'", lambda: panel(tds.materialized_panel_dir("campaigns")))
probe(
    "definition staged write for tenant dataset 'cycles'",
    lambda: io.write_text(tds.dataset_dir("cycles") / ".staged" / "framing.md", "x"),
)
probe(
    "definition staged write for tenant dataset 'bench/campaigns/v1'",
    lambda: io.write_text(tds.dataset_dir("bench/campaigns/v1") / ".staged" / "framing.md", "x"),
)
probe(
    "fresh data root under a dir named 'campaigns'",
    lambda: io.write_json(home / "campaigns" / "pp" / "projects" / "default" / ".cache" / "x.json", {}),
)

from promptpotter.infrastructure.store.campaign_store.store import CampaignStore  # noqa: E402
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir  # noqa: E402

jobs = paths.jobs_dir(paths.default_projects_root())
for tenant in ("cycles", "campaigns"):
    st = CampaignStore(WorkspaceDir(home / "projects" / tenant), jobs)
    probe(f"root mint in a fresh tenant named {tenant!r}", lambda st=st: st.mint_cycle(CycleHop(campaign_id="c1", cycle_id="k1")))
for cid in ("cycles", "campaigns"):
    st = CampaignStore(WorkspaceDir(home / "projects" / "t2"), jobs)
    probe(f"root mint of a campaign id {cid!r}", lambda st=st, cid=cid: st.mint_cycle(CycleHop(campaign_id=cid, cycle_id="k1")))

# --- anchored paths: refusal still made ----------------------------------------------------
st = CampaignStore(WorkspaceDir(home / "projects" / "t3"), jobs)
hop = CycleHop(campaign_id="camp", cycle_id="cyc")
probe("root mint, fresh tenant", lambda: st.mint_cycle(hop))
import shutil  # noqa: E402

shutil.rmtree(st.campaign_root_dir("camp"))
probe("write under deleted campaign (must refuse)", lambda: io.write_json(st.cycle_dir(hop) / "rounds" / "r.json", {}))
print("   campaign dir re-made?", st.campaign_root_dir("camp").exists())

# --- '..' in a path ------------------------------------------------------------------------
st.mint_cycle(hop)
shutil.rmtree(st.cycle_dir(hop))
dotted = st.cycle_dir(hop).parent / "other" / ".." / "cyc" / ".runtime" / "ledger.jsonl"
probe("append via a '..' path into a deleted cycle (must refuse)", lambda: io.append_line(dotted, b"{}"))
print("   cycle dir re-made?", st.cycle_dir(hop).exists())

# --- a file where a dir should be ---------------------------------------------------------
blocker = home / "blk"
blocker.write_text("x")
sys.setrecursionlimit(400)
probe("write under a path whose component is a file", lambda: io.write_json(blocker / "a" / "b.json", {}))
sys.setrecursionlimit(1000)

# --- long paths -----------------------------------------------------------------------------
st4 = CampaignStore(WorkspaceDir(home / "projects" / "t4"), jobs)
hop4 = CycleHop(campaign_id="c" * 60, cycle_id="k" * 60)
probe("root mint, long ids", lambda: st4.mint_cycle(hop4))
deep = st4.cycle_dir(hop4).joinpath(*(["d" * 40] * 5)) / "f.json"
print("   deep path length", len(str(deep)))
probe("first write, deep under a cycle", lambda: io.write_json(deep, {"a": 1}))
probe("append, deep under a cycle", lambda: io.append_line(deep.with_name("l.jsonl"), b"{}"))
probe("lock, deep under a cycle", lambda: io.take_lock(deep.with_name("x.lock"), wait_s=0))
probe("make_dirs, deep under a cycle", lambda: io.make_dirs(deep.parent / ("e" * 40) / ("f" * 40)))
for n in (247, 248, 259, 260, 261):
    base = home / "edge"
    pad = n - len(str(base)) - 1
    p = base / ("x" * min(pad, 200))
    while len(str(p)) < n:
        p = p / ("y" * min(n - len(str(p)) - 1, 200))
    probe(f"write_json at length {len(str(p))}", lambda p=p: io.write_json(p, {}))

# --- concurrent creators --------------------------------------------------------------------
race_root = home / "race"
errors: list[BaseException] = []


def worker(i):
    try:
        io.write_json(race_root / "a" / "b" / "c" / "d" / f"f{i}.json", {})
        io.append_line(race_root / "a" / "b" / "e" / "g" / "l.jsonl", b"{}")
        io.make_dirs(race_root / "a" / "h" / "i")
    except BaseException as exc:  # noqa: BLE001
        errors.append(exc)


ts = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
for t in ts:
    t.start()
for t in ts:
    t.join()
print("concurrent creators errors:", [f"{type(e).__name__}: {e}" for e in errors][:5])
print("home", home)
