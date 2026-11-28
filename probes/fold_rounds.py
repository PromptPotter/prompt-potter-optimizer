"""N7 premise probe (bb-folds, read-only): drive every round-rebuilding fold over each cycle the offline
world left, extract per round what each holds, and diff each against the reference fold.

    PROMPTPOTTER_HOME=<scratch> .venv/bin/python probes/fold_rounds.py <offline home> [--json OUT]

Reference: `STANDING_ROUNDS` (`_StandingFold` composed over the fork chain, `read_line`) -- what
`closed_round`, the round file, resume and fork all read. Each fold is driven through the door its
production reader uses: the chain-seeded `fold_at` for the dashboard, the own ledger for the index-held
folds (`RoundAudits`, `ActivityFeed`, `_RayTail`). `ReadoutProjection` needs a live run's stores,
resolution and sink, so its round state is re-derived from its own rule (readout.py:269-272 + :394-397):
STANDING at its last `InitClosed`, then every close after it.

Facts per round: members (arm keys), winner (`Election.selected`), pair (`deciding_pair` members),
ending, status (closed / open), cells (per arm). `None` = the fold does not hold that fact.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from promptpotter.application.maintenance.landing import complete_registries
from promptpotter.application.views.readout import ReadoutProjection  # noqa: F401  (registers it)
from promptpotter.domain.activity import ActivityFeed
from promptpotter.domain.cycle_paths import CycleAt, CycleHop
from promptpotter.domain.records.rounds import RoundClosedRecord
from promptpotter.domain.results import RoundEnding
from promptpotter.infrastructure.projections.audit_trail import RoundAudits
from promptpotter.infrastructure.projections.live_dashboard import fold_at
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    STANDING_ROUNDS,
    CycleFactsFold,
    _StandingFold,
)
from promptpotter.infrastructure.store.family_ray_queries import _RayTail
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import Moment, folded, read_line

FACTS = ("index", "members", "winner", "pair", "ending", "status", "cells")
FOLDS = (
    "_StandingFold (own ledger)",
    "LiveDashboardProjection",
    "ReadoutProjection",
    "RoundFileProjection",
    "RoundAudits",
    "ActivityFeed",
    "_RayTail",
    "CycleFactsFold",
)


def _key(arm: Any) -> str:
    return arm if isinstance(arm, str) else getattr(arm, "key", None) or str(arm)


def _pair(election: Any) -> tuple[str, str] | None:
    pair = None if election is None else election.deciding_pair
    return None if pair is None else (str(pair.a.address), str(pair.b.address))


def _closed(close: Any) -> dict[str, Any]:
    el = close.election
    return {
        "members": sorted(close.arms),
        "proposals": sorted(k for k, a in close.arms.items() if a.role.is_proposal),
        "winner": None if el is None else el.selected,
        "pair": _pair(el),
        "ending": None if el is None else str(el.ending),
        "status": "closed",
        "cells": {k: len(v) for k, v in sorted(close.cells.items())},
    }


def standing_rounds(standing: Any) -> dict[int, dict[str, Any]]:
    out = {n: _closed(held.close) for n, held in standing.rounds.items()}
    open_arms: dict[int, dict[str, Any]] = defaultdict(dict)
    for (n, key), arm in standing.arms.items():
        if n not in out:
            open_arms[n][key] = arm
    for n, arms in open_arms.items():
        el = standing.elections.get(n)
        out[n] = {
            "members": sorted(arms),
            "proposals": sorted(k for k, a in arms.items() if a.state.role.is_proposal),
            "winner": None if el is None else el.selected,
            "pair": _pair(el),
            "ending": None if el is None else str(el.ending),
            "status": "open",
            "cells": {k: len(a.cells) for k, a in sorted(arms.items())},
        }
    return out


def dashboard_rounds(state: Any) -> dict[int, dict[str, Any]]:
    if state is None:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for r in state.rounds:
        keys = sorted(_key(c.reading.arm) for c in r.candidates)
        won = r.leading is not None and r.ending in (RoundEnding.ADOPTED, RoundEnding.CROWNED)
        out[r.round] = {
            "members": keys,
            "winner": _key(r.leading) if won else None,
            "pair": None,
            "ending": None if r.ending is None else str(r.ending),
            "status": "closed",
            "cells": None,
        }
    cur = state.current_round
    if cur is not None and cur.round not in out and cur.candidates:
        out[cur.round] = {
            "members": sorted(_key(c.reading.arm) for c in cur.candidates),
            "winner": None,
            "pair": None,
            "ending": None,
            "status": "open",
            "cells": None,
        }
    return out


def narration_rounds(narration: Any) -> dict[int, dict[str, Any]]:
    held: dict[int, list[str]] = defaultdict(list)
    for n, key in narration:
        held[n].append(key)
    rounds = set(held) | set(narration._closed)
    return {
        n: {
            "members": sorted(held.get(n, [])),
            "winner": None,
            "pair": None,
            "ending": None,
            "status": "closed" if n in narration._closed else "open",
            "cells": None,
        }
        for n in rounds
    }


def audit_rounds(audits: dict[int, Any]) -> dict[int, dict[str, Any]]:
    return {
        n: {
            "members": None,
            "winner": None,
            "pair": None,
            "ending": None,
            "status": "closed" if a.finished_at else "open",
            "cells": None,
        }
        for n, a in audits.items()
    }


def readout_rounds(cycle_dir: Path, ledger: Path) -> dict[int, dict[str, Any]] | None:
    lines = ledger.read_text(encoding="utf-8").splitlines()
    init = [i for i, line in enumerate(lines) if '"act": "init_closed"' in line]
    if not init:
        return None
    seed = read_line(cycle_dir, STANDING_ROUNDS, moment=Moment(ledger, init[-1]))
    closed = {n: held.close for n, held in seed.rounds.items()}
    for line in lines[init[-1] + 1 :]:
        if '"round_closed"' in line:
            rec = RoundClosedRecord.model_validate_json(line)
            closed[rec.round] = rec
    return {n: _closed(c) for n, c in closed.items()}


def round_file_rounds(cycle_dir: Path) -> dict[int, dict[str, Any]]:
    out = {}
    for p in sorted((cycle_dir / "rounds").glob("round_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        el = d.get("election")
        pair = None if not el or not el.get("deciding_pair") else el["deciding_pair"]
        sel = None
        if el and el.get("ending") in ("adopted", "crowned"):
            sel = el.get("leading")
        out[d["round"]] = {
            "members": sorted(d.get("arms", {})),
            "winner": sel,
            "pair": None
            if pair is None
            else (
                _addr(pair["a"]["address"]),
                _addr(pair["b"]["address"]),
            ),
            "ending": None if not el else _ending(el.get("ending")),
            "status": "closed",
            "cells": {k: len(v.get("cells", v) if isinstance(v, dict) else v) for k, v in sorted(d.get("cells", {}).items())},
        }
    return out


def _ending(raw: str | None) -> str | None:
    return None if raw is None else str(RoundEnding(raw))


def _addr(raw: Any) -> str:
    from promptpotter.domain.paired_reading import MemberAddress
    from pydantic import TypeAdapter

    return str(TypeAdapter(MemberAddress).validate_python(raw))


def compare(ref: dict[int, dict[str, Any]], got: dict[int, dict[str, Any]] | None) -> dict[str, str | None]:
    """Per fact: None = same as the reference, "not held", or the first round that differs and how."""
    if got is None:
        return {f: "no round notion" for f in FACTS}
    not_held = _NOT_HELD.get(id(got), set())
    out: dict[str, str | None] = {
        "index": None if set(ref) == set(got) else f"ref {sorted(ref)} vs {sorted(got)}"
    }
    for fact in FACTS[1:]:
        if fact in not_held:
            out[fact] = "not held"
            continue
        out[fact] = next(
            (
                f"round {n}: ref {ref[n][_AS.get(id(got), {}).get(fact, fact)]!r} vs {got[n][fact]!r}"
                for n in sorted(set(ref) & set(got))
                if ref[n].get(_AS.get(id(got), {}).get(fact, fact)) != got[n][fact]
            ),
            None,
        )
    return out


_NOT_HELD: dict[int, set[str]] = {}
# A fold whose fact is a projection of the reference's: the dashboard banks proposals alone (rounds.py:97).
_AS: dict[int, dict[str, str]] = {}


def _as(d, facts):
    if d is not None:
        _AS[id(d)] = facts
    return d


def held_as(d: dict[int, dict[str, Any]] | None, not_held: set[str]) -> dict[int, dict[str, Any]] | None:
    if d is not None:
        _NOT_HELD[id(d)] = not_held
    return d


def main() -> None:
    # The registries a state kind reads, completed as a process's entry completes them (no landing).
    complete_registries()
    home = Path(sys.argv[1])
    report: dict[str, Any] = {}
    for ledger in sorted(home.rglob(".runtime/ledger.jsonl")):
        cycle_dir = ledger.parent.parent
        campaign = cycle_dir.parent.parent.name
        hop = CycleHop(campaign_id=campaign, cycle_id=cycle_dir.name)
        rel = re.sub(r"__[0-9a-f]{6,12}\b", "__X", str(cycle_dir.relative_to(home)))
        row: dict[str, Any] = {}
        try:
            ref = standing_rounds(read_line(cycle_dir, STANDING_ROUNDS))
        except Exception as exc:  # noqa: BLE001
            report[rel] = {"error": f"reference: {type(exc).__name__}: {exc}"}
            continue
        own = standing_rounds(folded(ledger, _StandingFold, start=None, until=None).value())
        feed = folded(ledger, ActivityFeed, start=None, until=None)
        ray = folded(ledger, _RayTail, start=None, until=None)
        drivers = {
            "_StandingFold (own ledger)": lambda: held_as(own, set()),
            "LiveDashboardProjection": lambda: _as(
                held_as(dashboard_rounds(fold_at(CycleAt(cycle_dir, hop))), {"pair", "cells"}),
                {"members": "proposals"},
            ),
            "ReadoutProjection": lambda: held_as(readout_rounds(cycle_dir, ledger), set()),
            "RoundFileProjection": lambda: held_as(round_file_rounds(cycle_dir), set()),
            "RoundAudits": lambda: held_as(
                audit_rounds(folded(ledger, RoundAudits, start=None, until=None).value()),
                {"members", "winner", "pair", "ending", "cells"},
            ),
            "ActivityFeed": lambda: held_as(
                narration_rounds(feed.narration), {"winner", "pair", "ending", "cells"}
            ),
            "_RayTail": lambda: held_as(
                narration_rounds(ray._narration), {"winner", "pair", "ending", "cells"}
            ),
            "CycleFactsFold": lambda: (folded(ledger, CycleFactsFold, start=None, until=None), None)[1],
        }
        for name, drive in drivers.items():
            try:
                row[name] = compare(ref, drive())
            except Exception as exc:  # noqa: BLE001
                row[name] = {f: f"raised {type(exc).__name__}: {str(exc)[:160]}" for f in FACTS}
        row["_ref"] = {str(n): {"status": r["status"], "members": len(r["members"])} for n, r in sorted(ref.items())}
        report[rel] = row
    out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    if out:
        Path(out).write_text(json.dumps(report, indent=1, default=str))
    # The table: per fold x fact, "same" or the first differing cycle and round.
    print(f"cycles: {len(report)}  errors: {sum('error' in r for r in report.values())}")
    for name in FOLDS:
        cells = []
        for fact in FACTS:
            differs = lambda v: bool(v) and v not in ("not held", "no round notion")  # noqa: E731
            kinds = {r[name][fact] for r in report.values() if "error" not in r} - {None}
            if kinds and all(not differs(k) for k in kinds):
                cells.append(next(iter(kinds)))
                continue
            first = next(
                ((c, r[name][fact]) for c, r in report.items() if "error" not in r and differs(r[name][fact])),
                None,
            )
            n_diff = sum(1 for r in report.values() if "error" not in r and differs(r[name][fact]))
            cells.append("same" if first is None else f"{n_diff}x first {first[0].split('/data/')[0]}:{first[0].rsplit('/', 1)[-1]} {first[1][:140]}")
        print(f"\n== {name}")
        for fact, cell in zip(FACTS, cells, strict=True):
            print(f"   {fact:8s} {cell}")


if __name__ == "__main__":
    main()
