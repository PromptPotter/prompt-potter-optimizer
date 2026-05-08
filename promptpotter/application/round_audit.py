"""Per-round audit-JSON readers — one shape, two consumer surfaces.

The audit projection writes ``{cycle_dir}/.runtime/cache/rounds/round_NNNN.json``
during a run. Two read sites live in this layer:

* ``leaderboard.py`` and ``presentation_writers.py`` load **all** per-round
  audits for one cycle to build review.md / leaderboard rows.
* ``l1_behavior_checks.py`` and ``review.py`` walk the
  ``nodes.l1_generate.output.response.variants`` chain on **one** round/audit
  dict to enumerate the L1 candidates that round.

This module owns both shapes so the path layout, the load/error policy, and
the variants chain live in exactly one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "extract_l1_variants",
    "load_round_audits",
]

_ROUNDS_SUBPATH = (".runtime", "cache", "rounds")


def audit_rounds_dir(cycle_dir: Path) -> Path:
    """``{cycle_dir}/.runtime/cache/rounds`` — per-round audit folder."""
    return cycle_dir.joinpath(*_ROUNDS_SUBPATH)


def load_round_audits(cycle_dir: Path, rounds: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Load ``round_NNNN.json`` for each round; ``None`` on missing/corrupt.

    Output is parallel to *rounds* — slot ``N`` is the audit dict (or ``None``)
    for ``rounds[N]``. Corrupt JSON or absent file is non-fatal: the matching
    slot is ``None`` and the operator-facing render degrades gracefully.
    """
    rd = audit_rounds_dir(cycle_dir)
    out: list[dict[str, Any] | None] = []
    for round_data in rounds:
        round_num = int(round_data.get("round") or 0)
        path = rd / f"round_{round_num:04d}.json"
        if path.is_file():
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("audit load failed: %s — %s", path.name, exc)
        out.append(None)
    return out


def extract_l1_variants(container: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Walk ``nodes.l1_generate.output.response.variants`` on a round/audit dict.

    The same shape rides on both the round-summary dict (as written into
    ``index.json::rounds[N]``) and the per-round audit dict
    (``round_NNNN.json``); both are read by this codepath. Empty list when L1
    didn't fire or the response is malformed.
    """
    if not container:
        return []
    nodes = container.get("nodes") or {}
    node = nodes.get("l1_generate") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    if isinstance(response, dict):
        return [v for v in (response.get("variants") or []) if isinstance(v, dict)]
    return []
