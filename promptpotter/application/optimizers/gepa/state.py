"""GEPA's working state between rounds: the pool scored on the Pareto set and the next parent,
banked whole on every round document so a resume or a fork re-seats the front."""

from __future__ import annotations

from typing import Literal

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.strict_model import StrictModel

__all__ = ["GEPA_MANIFEST", "GepaCandidate", "GepaRoundState"]

GepaManifest = Literal["gepa"]
GEPA_MANIFEST: GepaManifest = "gepa"


class GepaCandidate(StrictModel):
    """One member of GEPA's candidate pool and its row of the score matrix: its campaign objective
    on each Pareto-set cell, by sample key."""

    individual: OptSearchPoint
    scores: dict[str, float]


class GepaRoundState(RoundPayload, manifest=GEPA_MANIFEST):
    """GEPA's payload: its candidate pool scored on the Pareto set, and the parent the next round
    mutates."""

    # Sample keys, in the order every round walks them; empty until round 1 draws the split.
    pareto_set: list[str]
    # Empty on the origin's document: round 1 seats the origin with its Pareto-set scores.
    pool: list[GepaCandidate]
    # Drawn at each round's close; ``None`` until round 1 closes, the origin being the only parent.
    parent_id: str | None
