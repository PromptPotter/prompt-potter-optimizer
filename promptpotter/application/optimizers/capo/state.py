"""The population CAPO's selector keeps is the bench's (``OptimizerState.population``), banked beside this."""

from __future__ import annotations

from typing import Literal

from promptpotter.domain.optimizer_state import RoundPayload

__all__ = ["CAPO_MANIFEST", "CapoRoundState"]

CapoManifest = Literal["capo"]
CAPO_MANIFEST: CapoManifest = "capo"


class CapoRoundState(RoundPayload, manifest=CAPO_MANIFEST):
    # The longest initial prompt's scored length, the length term's divisor (§4); ``None`` until round 1.
    length_norm: int | None
