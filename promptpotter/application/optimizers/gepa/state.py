from __future__ import annotations

from typing import Literal

from promptpotter.domain.optimizer_state import RoundPayload

__all__ = ["GEPA_MANIFEST", "GepaRoundState"]

GepaManifest = Literal["gepa"]
GEPA_MANIFEST: GepaManifest = "gepa"


class GepaRoundState(RoundPayload, manifest=GEPA_MANIFEST):
    # Sample keys, in the order every round walks them; empty until round 1 draws the split.
    pareto_set: list[str]
    # By id then sample key, in the population's order; empty on the origin's document: round 1 seats it.
    scores: dict[str, dict[str, float]]
    # Drawn at each round's close; ``None`` until round 1 closes, the origin being the only parent.
    parent_id: str | None
