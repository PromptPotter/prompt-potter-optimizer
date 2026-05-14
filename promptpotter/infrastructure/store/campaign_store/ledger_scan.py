"""Pure ledger scan — read ``events.jsonl`` and find the highest closed round.

The scan does NOT instantiate :class:`CycleEventLog`, so no subscribers
fire during admissibility checks (e.g. ``--from N`` validation).
"""

from __future__ import annotations

import json
from pathlib import Path


def scan_ledger_max_round_complete(ledger_path: Path) -> int:
    """Return the highest round number that has a closing PhaseRecord in the
    ledger at ``ledger_path``. Returns ``-1`` if no round has closed.

    Closing events follow ``AuditTrailView._handle_phase``'s symmetry:
    ``(phase="origin", event="exit", round=0)`` for origin and
    ``(phase="round", event="complete", round=N)`` for normal rounds.
    Both close their round and flush the audit cache.

    Pure file read — does not instantiate :class:`CycleEventLog` so no
    subscribers fire. The ledger is JSONL; each line is one record dict.
    Corrupt lines are skipped (graceful — the ledger has at-least-once
    semantics from the writer's perspective; readers tolerate gaps).
    """
    max_complete = -1
    try:
        with ledger_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("record_type") != "phase":
                    continue
                phase = rec.get("phase")
                event = rec.get("event")
                if (phase == "round" and event == "complete") or (
                    phase == "origin" and event == "exit"
                ):
                    rnd = rec.get("round")
                    if isinstance(rnd, int) and rnd > max_complete:
                        max_complete = rnd
    except OSError:
        return -1
    return max_complete
