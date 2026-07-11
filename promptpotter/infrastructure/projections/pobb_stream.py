"""PoBBStreamView — appends per-sample Posterior-of-Being-Best snapshots
to each cycle's ``.runtime/streams/round_NNNN_p_best.jsonl``.

Subscribed to the same ``CycleEventLog`` as the other projections. Filters on
``SnapshotRecord.event == "p_best_update"`` and writes one JSONL record per
PoBBCheck snapshot — operator-tailable in real time, replayable after the
fact for trajectory plots and post-hoc analysis.

Per-cycle scope: each fork's projection points at its own
``.runtime/streams/`` directory under the fork's audit tree (parallel to
``.runtime/cache/rounds/``). A runtime check rejects misrouted construction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import SnapshotRecord
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.store.io import append_jsonl
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["PoBBStreamView"]

_STREAMS_SUBPATH = (".runtime", "streams")


class PoBBStreamView(DerivedView):
    """Appends per-sample P(best) snapshots to one JSONL file per round.

    File path: ``.runtime/streams/round_NNNN_p_best.jsonl`` under the cycle's
    audit dir. Each line is a JSON object with ``round``, ``sample_idx``,
    ``current_id``, ``n_samples``, ``p_best`` (cid → prob), ``p_best_delta``
    (cid → delta vs previous query), and ``paired_breakdown`` (prior cid →
    {mean_d, se_d, n_paired, p_better}).
    """

    def __init__(self, streams_dir: Path) -> None:
        if streams_dir.parts[-len(_STREAMS_SUBPATH) :] != _STREAMS_SUBPATH:
            raise ValueError(
                f"PoBBStreamView streams_dir must end in /{'/'.join(_STREAMS_SUBPATH)}; "
                f"got {streams_dir}"
            )
        self.streams_dir = streams_dir
        # cid → last-query p_best, for delta computation across the same round.
        self._last_p_best: dict[str, float] = {}
        self._last_round: int | None = None

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> PoBBStreamView:
        """Build a projection rooted at ``{cycle_dir}/.runtime/streams``."""
        return cls(CycleLayout(Path(cycle_dir)).streams)

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
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
            # New round → reset the delta origin.
            self._last_p_best = {}
            self._last_round = record.round

        deltas: dict[str, float] = {
            cid: float(p_best[cid] - self._last_p_best.get(cid, p_best[cid])) for cid in p_best
        }

        # Per-prior θ comparison (p_better = P(θ_cand > θ_prior), n_paired)
        # — the operator's main triangulation surface. When one prior gates
        # the abort, its p_better on this line tells the story.
        paired_breakdown: dict[str, dict[str, float]] = {
            str(pid): {str(k): float(v) for k, v in (entry or {}).items()}
            for pid, entry in (payload.get("paired_breakdown") or {}).items()
        }

        line = {
            "round": int(record.round),
            "sample_idx": int(record.sample_idx if record.sample_idx is not None else -1),
            "current_id": str(payload.get("current_id", "")),
            "n_samples": int(payload.get("n_samples", 0)),
            "p_best": p_best,
            "p_best_delta": deltas,
            "paired_breakdown": paired_breakdown,
            # Paired-margin gate trajectory (wins/losses/net/opportunities/p_clear)
            # — lets the operator watch a candidate converge on the kill.
            "margin": {str(k): float(v) for k, v in (payload.get("margin") or {}).items()},
        }

        self._append(record.round, line)
        self._last_p_best = dict(p_best)

    def _append(self, round_num: int, line: dict[str, Any]) -> None:
        try:
            append_jsonl(self.streams_dir / f"round_{round_num:04d}_p_best.jsonl", line)
        except OSError as exc:
            logger.warning("PoBBStreamView: append failed for round %d: %s", round_num, exc)
