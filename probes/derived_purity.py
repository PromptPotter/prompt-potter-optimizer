"""Are the derived files pure functions of the ledger? Re-derive each from the ledger on disk, in a fresh
process, and compare bytes with what the run wrote (bb-folds, read-only).

    PROMPTPOTTER_HOME=<scratch> .venv/bin/python probes/derived_purity.py <offline home>

- index.json: `CampaignStore.load(hop)` dumped as `CycleIndexProjection.feed` dumps it (store.py:148-151).
- dashboard.json: `fold_at` dumped as `DashboardCheckout._write` dumps it (live_dashboard.py:856-865),
  at the ledger's head and at the file's own `at_offset` (a write lands at a boundary, not at the tail).
- round files and readout.log join the archive and the wall clock; they are hashed by hash_derived.py.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from promptpotter.application.maintenance.landing import complete_registries
from promptpotter.domain.cycle_paths import CycleAt, CycleHop, WorkspaceDir
from promptpotter.infrastructure.projections.live_dashboard import fold_at
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def dumped(data: object, *, default: object = None) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False, default=default).encode("utf-8")  # type: ignore[arg-type]


def first_diff(a: bytes, b: bytes) -> str:
    x, y = a.decode().splitlines(), b.decode().splitlines()
    i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    return f"line {i}: disk {x[i].strip()[:90] if i < len(x) else '<eof>'!r} vs fold {y[i].strip()[:90] if i < len(y) else '<eof>'!r}"


def main() -> None:
    complete_registries()
    home = Path(sys.argv[1])
    tally: Counter[str] = Counter()
    shown: dict[str, list[str]] = {}
    for ledger in sorted(home.rglob(".runtime/ledger.jsonl")):
        cycle_dir = ledger.parent.parent
        campaign_dir = cycle_dir.parent.parent
        workspace = campaign_dir.parent.parent
        data = next(p for p in cycle_dir.parents if (p / "jobs").is_dir() or p.name == "data")
        hop = CycleHop(campaign_id=campaign_dir.name, cycle_id=cycle_dir.name)
        rel = re.sub(r"__[0-9a-f]{6,12}\b", "__X", str(cycle_dir.relative_to(home)))
        store = CampaignStore(WorkspaceDir(workspace), data / "jobs")
        index_file = cycle_dir / "index.json"
        if index_file.is_file():
            index = store.load(hop, moment=None)
            got = b"" if index is None else dumped(index.model_dump(mode="json"))
            verdict = "index equal" if got == index_file.read_bytes() else "index DIFFERS"
            tally[verdict] += 1
            if verdict.endswith("DIFFERS"):
                shown.setdefault(verdict, []).append(f"{rel}: {first_diff(index_file.read_bytes(), got)}")
        dash_file = cycle_dir / "dashboard.json"
        if dash_file.is_file():
            disk = dash_file.read_bytes()
            at = json.loads(disk).get("at_offset")
            for label, cut in (("head", None), ("own at_offset", at)):
                state = fold_at(CycleAt(cycle_dir, hop, cut))
                got = b"" if state is None else dumped(state.model_dump(), default=str)
                verdict = f"dashboard@{label} {'equal' if got == disk else 'DIFFERS'}"
                tally[verdict] += 1
                if got != disk:
                    shown.setdefault(verdict, []).append(f"{rel}: {first_diff(disk, got)}")
                    # Is the lag its offset alone, or does the head move what the file says?
                    lagged = json.loads(disk) | {"at_offset": None} == json.loads(got) | {"at_offset": None}
                    tail = [json.loads(line)["record_type"] for line in ledger.read_text().splitlines()[at + 1 :]]
                    tally[f"  ..lags by {'at_offset only' if lagged else 'MORE than at_offset'}"] += 1
                    shown[verdict][-1] += f"  tail after the write: {tail}"
    for k, n in sorted(tally.items()):
        print(f"{k:32s} {n}")
    for k, rows in shown.items():
        print(f"--- {k} (first 4 of {len(rows)})")
        for r in rows[:4]:
            print("   ", r[:300])


if __name__ == "__main__":
    main()
