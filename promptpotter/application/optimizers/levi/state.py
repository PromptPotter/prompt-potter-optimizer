"""LEVI's working state between rounds: its archive and what calibration fixed for it. Every round
document banks it whole, so a resume or a fork re-seats the archive off the round it continues from."""

from __future__ import annotations

from typing import Literal

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "LEVI_MANIFEST",
    "DescriptorStats",
    "LeviCalibration",
    "LeviElite",
    "LeviRoundState",
]

LeviManifest = Literal["levi"]
LEVI_MANIFEST: LeviManifest = "levi"


class DescriptorStats(StrictModel):
    """Welford's running count, mean and squared-deviation sum per descriptor dimension."""

    count: int
    mean: list[float]
    m2: list[float]


class LeviCalibration(StrictModel):
    """What LEVI's calibration round fixes for the run: the proxy and the archive's Voronoi cells."""

    # Sample keys, in the order every later round walks them.
    proxy: list[str]
    centroids: list[list[float]]
    stats: DescriptorStats


class LeviElite(StrictModel):
    """One occupied cell of LEVI's archive: the best individual mapped to it."""

    cell: int
    # The mean per-cell objective over the proxy: LEVI's f, which the cell keeps the best of.
    score: float
    # The round whose rows measured it, which is where its feedback is read back from.
    round: int
    individual: OptSearchPoint


class LeviRoundState(RoundPayload, manifest=LEVI_MANIFEST):
    """LEVI's payload: its CVT-MAP-Elites archive and what calibration fixed for it."""

    # ``None`` on the origin's document: round 1 is the calibration round that sets it.
    calibration: LeviCalibration | None
    elites: list[LeviElite]
