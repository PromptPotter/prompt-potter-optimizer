"""PoBBStreamProjection — appends per-query Posterior-of-Being-Best snapshots
to ``campaigns/{cycle_id}/streams/round_NNNN_p_best.jsonl``.

Subscribed to the same ``RunLedger`` as the other projections. Filters on
``Snapshot.event == "p_best_update"`` and writes one JSONL record per
PoBBCheck snapshot — operator-tailable in real time, replayable after the
fact for trajectory plots and post-hoc analysis.

Per-cycle scope: each fork's projection points at its own ``streams/``
directory under the fork's audit tree (parallel to ``.cache/rounds/``). A
runtime check rejects misrouted construction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import RunRecord, Snapshot

logger = logging.getLogger(__name__)

__all__ = ["PoBBStreamProjection"]

_STREAMS_SUBDIR = "streams"


class PoBBStreamProjection:
    """Appends per-query P(best) snapshots to one JSONL file per round.

    File path: ``streams/round_NNNN_p_best.jsonl`` under the cycle's audit
    dir. Each line is a JSON object with ``round``, ``query_idx``,
    ``current_id``, ``p_best`` (cid → prob), ``p_best_delta`` (cid → delta
    vs previous query), and ``stopped`` (cids whose p_best fell below ε
    on this query — currently encoded as the singleton ``[current_id]``
    when applicable; future: full set when leader-only stopping arrives).
    """

    def __init__(self, streams_dir: Path) -> None:
        if streams_dir.name != _STREAMS_SUBDIR:
            raise ValueError(
                f"PoBBStreamProjection streams_dir must end in /{_STREAMS_SUBDIR}; "
                f"got {streams_dir}"
            )
        self.streams_dir = streams_dir
        # cid → last-query p_best, for delta computation across the same round.
        self._last_p_best: dict[str, float] = {}
        self._last_round: int | None = None

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> PoBBStreamProjection:
        """Build a projection rooted at ``{cycle_dir}/streams``."""
        return cls(Path(cycle_dir) / _STREAMS_SUBDIR)

    def on_record(self, record: RunRecord, offset: int) -> None:
        del offset
        if not isinstance(record, Snapshot):
            return
        if record.event != "p_best_update":
            return
        if record.round is None:
            return

        payload: dict[str, Any] = dict(record.payload)
        p_best: dict[str, float] = {
            str(k): float(v) for k, v in (payload.get("p_best") or {}).items()
        }
        if not p_best:
            return

        if self._last_round != record.round:
            # New round → reset the delta baseline.
            self._last_p_best = {}
            self._last_round = record.round

        deltas: dict[str, float] = {
            cid: float(p_best[cid] - self._last_p_best.get(cid, p_best[cid])) for cid in p_best
        }

        line = {
            "round": int(record.round),
            "query_idx": int(record.sample_idx if record.sample_idx is not None else -1),
            "current_id": str(payload.get("current_id", "")),
            "n_queries": int(payload.get("n_queries", 0)),
            "p_best": p_best,
            "p_best_delta": deltas,
        }

        self._append(record.round, line)
        self._last_p_best = dict(p_best)

    def _append(self, round_num: int, line: dict[str, Any]) -> None:
        self.streams_dir.mkdir(parents=True, exist_ok=True)
        path = self.streams_dir / f"round_{round_num:04d}_p_best.jsonl"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, default=str) + "\n")
        except OSError as exc:
            logger.warning("PoBBStreamProjection: append failed for round %d: %s", round_num, exc)
