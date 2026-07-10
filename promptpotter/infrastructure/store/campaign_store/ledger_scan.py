"""Pure ledger scan — read the per-cycle ledger for the highest closed round or the
cycle seed; no subscribers fire."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.run_records import CycleSeed


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


def scan_ledger_cycle_seed(ledger_path: Path) -> CycleSeed | None:
    """The cycle's own seed — the last ``CycleSeedRecord``'s ``seed`` in ``ledger_path``,
    or ``None`` when the cycle carries none (sweep / diag). Pure scan, no subscribers.

    A fork's own ledger holds only its own appends (the parent's prefix is inherited
    *virtually* via ``iter``), so scanning the physical file returns THIS cycle's seed.
    The seed is written once at mint, but we take the last match so a re-seed wins.
    Corrupt lines skipped.
    """
    found: CycleSeed | None = None
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
                if not isinstance(rec, dict) or rec.get("record_type") != "cycle_seed":
                    continue
                seed_data = rec.get("seed")
                if isinstance(seed_data, dict):
                    try:
                        found = CycleSeed.model_validate(seed_data)
                    except ValidationError:
                        continue
    except OSError:
        return None
    return found


__all__ = ["scan_ledger_cycle_seed", "scan_ledger_max_round_complete"]
