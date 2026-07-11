"""Physical-file ledger scans — the highest closed round, and the cycle seed.

Both read through ``iter_jsonl``, the declared read-model primitive: corruption-
tolerant (a torn trailing line degrades to "not there") but NOT failure-tolerant.
They each used to hand-parse the file behind ``except OSError``, which returned the
same value as "nothing on the ledger" — an unreadable file told the operator their
round had never completed, and turned a seeded cycle into an unseeded one, silently
changing what the campaign optimizes.

These scan the PHYSICAL file, deliberately. ``CycleEventLog.iter`` replays a fork's
inherited parent prefix first; a fork's own seed and its own closed rounds are the
appends in its own file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store.read_model import iter_jsonl


def scan_ledger_max_round_complete(ledger_path: Path) -> int:
    """Highest round with a closing PhaseRecord in ``ledger_path``; ``-1`` if none closed.

    One closing event: ``(phase="round", event="complete", round=N)``. Round 0 closes through
    it too — ``emit_origin_round`` sends the origin down the same ``close_round`` seam every L1
    round uses.
    """
    max_complete = -1
    for rec in iter_jsonl(ledger_path):
        if rec.get("record_type") != "phase":
            continue
        if rec.get("phase") == "round" and rec.get("event") == "complete":
            rnd = rec.get("round")
            if isinstance(rnd, int) and rnd > max_complete:
                max_complete = rnd
    return max_complete


def scan_ledger_cycle_seed(ledger_path: Path) -> CycleSeed | None:
    """The cycle's own seed — the last ``CycleSeedRecord``'s ``seed`` in ``ledger_path``,
    or ``None`` when the cycle carries none (sweep / diag).

    The seed is written once at mint, but we take the last match so a re-seed wins.
    """
    found: CycleSeed | None = None
    for rec in iter_jsonl(ledger_path):
        if rec.get("record_type") != "cycle_seed":
            continue
        seed_data = rec.get("seed")
        if isinstance(seed_data, dict):
            try:
                found = CycleSeed.model_validate(seed_data)
            except ValidationError:
                continue
    return found


__all__ = ["scan_ledger_cycle_seed", "scan_ledger_max_round_complete"]
