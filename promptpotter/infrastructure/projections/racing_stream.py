from __future__ import annotations

import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import RaceStandingRecord
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.io import append_jsonl
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["RacingStreamProjection"]

_STREAMS_SUBPATH = (".runtime", "streams")


class RacingStreamProjection(Projection):
    """One line describes one candidate: fanned out by cid, a prior's trajectory is built from somebody else's numbers."""

    def __init__(self, streams_dir: Path) -> None:
        if streams_dir.parts[-len(_STREAMS_SUBPATH) :] != _STREAMS_SUBPATH:
            raise ValueError(
                f"RacingStreamProjection streams_dir must end in /{'/'.join(_STREAMS_SUBPATH)}; "
                f"got {streams_dir}"
            )
        self.streams_dir = streams_dir
        self._last_p_best: dict[str, float] = {}
        self._last_round: int | None = None

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> RacingStreamProjection:
        return cls(CycleLayout(Path(cycle_dir)).streams)

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        current_id, p_best = record.current_id, record.p_best

        if self._last_round != record.round:
            self._last_p_best = {}
            self._last_round = record.round

        line = {
            "round": record.round,
            "sample_idx": record.n_samples - 1,
            "current_id": current_id,
            "n_samples": record.n_samples,
            "p_best": p_best,
            "p_best_delta": p_best - self._last_p_best.get(current_id, p_best),
            "paired_breakdown": record.paired_breakdown,
        }

        path = self.streams_dir / f"round_{record.round:04d}_{record.member}.jsonl"
        try:
            append_jsonl(path, line)
        except OSError as exc:
            logger.warning("RacingStreamProjection: append to %s failed: %s", path.name, exc)
        self._last_p_best[current_id] = p_best
