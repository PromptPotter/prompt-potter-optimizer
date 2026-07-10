"""Pure ledger scan — read the per-cycle ledger for the highest closed round; no subscribers fire."""

from __future__ import annotations

import json
from pathlib import Path


def scan_ledger_max_round_complete(ledger_path: Path) -> int:
    """Highest round with a closing PhaseRecord in ``ledger_path``; ``-1`` if none closed.

    One closing event: ``(phase="round", event="complete", round=N)``. Round 0 closes through
    it too — ``emit_origin_round`` sends the origin down the same ``close_round`` seam every L1
    round uses. Corrupt lines skipped.
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
                if rec.get("phase") == "round" and rec.get("event") == "complete":
                    rnd = rec.get("round")
                    if isinstance(rnd, int) and rnd > max_complete:
                        max_complete = rnd
    except OSError:
        return -1
    return max_complete


__all__ = ["scan_ledger_max_round_complete"]
