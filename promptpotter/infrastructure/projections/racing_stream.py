"""Appends each eliminator's per-sample race standings to its cycle's OWN ``.runtime/streams/``, one
file per round named by the manifest member racing it; a runtime check rejects misrouted
construction, so a fork writes under its own audit tree."""

from __future__ import annotations

import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import SnapshotRecord
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.io import append_jsonl
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["RacingStreamProjection"]

_STREAMS_SUBPATH = (".runtime", "streams")


class RacingStreamProjection(Projection):
    """Per-sample standings, one JSONL per round and member. **One line describes ONE candidate** —
    fanned out by cid, a prior's trajectory would be built out of numbers about somebody else."""

    def __init__(self, streams_dir: Path) -> None:
        if streams_dir.parts[-len(_STREAMS_SUBPATH) :] != _STREAMS_SUBPATH:
            raise ValueError(
                f"RacingStreamProjection streams_dir must end in /{'/'.join(_STREAMS_SUBPATH)}; "
                f"got {streams_dir}"
            )
        self.streams_dir = streams_dir
        # cid → last-query p_best, for delta computation across the same round.
        self._last_p_best: dict[str, float] = {}
        self._last_round: int | None = None

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> RacingStreamProjection:
        return cls(CycleLayout(Path(cycle_dir)).streams)

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        if record.event != "race_standing":
            return
        payload = record.payload
        current_id: str = payload["current_id"]
        p_best: float = payload["p_best"]

        if self._last_round != record.round:
            # New round → reset the delta origin.
            self._last_p_best = {}
            self._last_round = record.round

        line = {
            "round": record.round,
            "sample_idx": record.sample_idx,
            "current_id": current_id,
            "n_samples": payload["n_samples"],
            "p_best": p_best,
            "p_best_delta": p_best - self._last_p_best.get(current_id, p_best),
            # Per-prior θ comparison (p_better = P(θ_cand > θ_prior), n_paired): when one prior
            # gates the abort, its p_better on this line tells the story.
            "paired_breakdown": payload["paired_breakdown"],
        }

        path = self.streams_dir / f"round_{record.round:04d}_{payload['member']}.jsonl"
        try:
            append_jsonl(path, line)
        except OSError as exc:
            logger.warning("RacingStreamProjection: append to %s failed: %s", path.name, exc)
        self._last_p_best[current_id] = p_best
