from __future__ import annotations

from typing import Literal

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
    # Sample keys, in the order every later round walks them.
    proxy: list[str]
    centroids: list[list[float]]
    stats: DescriptorStats


class LeviElite(StrictModel):
    """Holds an id, never the individual, which is one of the population the run carries."""

    cell: int
    score: float
    # The round whose rows MEASURED it, where its feedback is read back from.
    round: int
    individual_id: str


class LeviRoundState(RoundPayload, manifest=LEVI_MANIFEST):
    # ``None`` on the origin's document: round 1 is the calibration round that sets it.
    calibration: LeviCalibration | None
    elites: list[LeviElite]
