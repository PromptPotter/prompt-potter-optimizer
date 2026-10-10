from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import ConfigDict, Field

from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_TOOLING,
    CritiqueReadout,
    RoundPayload,
)
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import RuntimeFailure
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "POTTER_MANIFEST",
    "L1Layout",
    "L2L3Memory",
    "Ladder",
    "LayerCounters",
    "PotterCheckpointKind",
    "PotterRoundState",
    "WoundChannels",
]

PotterManifest = Literal["potter"]
POTTER_MANIFEST: PotterManifest = "potter"


class PotterCheckpointKind(CheckpointKind):
    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"


class WoundChannels(StrictModel):
    """The wound streams no round holds; an arm's parse-time reject is the arm's (`ScoredCandidate.validation_failures`)."""

    model_config = ConfigDict(frozen=True)

    l3_note: str = ""
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class L1Layout(StrictModel):
    # Declared in render order: `model_dump()` puts it on disk and `dispatch/layout.py` asserts it.
    persona: list[str] = Field(default_factory=list)
    task_intent: list[str] = Field(default_factory=list)
    thinking_style: list[str] = Field(default_factory=list)
    problem_description: list[str] = Field(default_factory=list)

    @shapes_optimizer_prompt
    def all_placeholders(self) -> list[str]:
        return [name for slot in type(self).model_fields for name in self.slot(slot)]

    def slot(self, name: str) -> list[str]:
        if name not in type(self).model_fields:
            raise KeyError(f"Unknown L1 layout slot: {name}")
        return cast("list[str]", getattr(self, name))


class L2L3Memory(StrictModel):
    """Frozen: a write is the next memory (:meth:`wounded`, :meth:`steered_onto`), which ``PotterState.memory`` then holds."""

    model_config = ConfigDict(frozen=True)

    wounds: WoundChannels = Field(
        default_factory=WoundChannels,
        description=(
            "Three wound streams (runtime/l2-guard/l3-guard) + "
            "sticky L3 note. Rendered by dispatch-hub injections; absorbed "
            "by L2 next round."
        ),
    )
    steer: dict[str, ManifestNodeOverlay] = Field(
        default_factory=dict,
        description=(
            "What a layer wrote onto another node's config, keyed by that node — the "
            "shape the campaign's own overlay takes. L2 steers ``l1_generate``: its "
            "``layout`` (the panels ``DispatchHub.fill`` walks, whole) and the call "
            "settings it widens or narrows."
        ),
    )
    plan: str = Field(
        default="",
        description=(
            "Strategic frame written by ``l3_plan`` and read by every layer "
            "next round; persistent until the next L3 fire. Empty until L3 "
            "fires for the first time."
        ),
    )

    def steered(self, node: str) -> dict[str, Any]:
        held = self.steer.get(node)
        return {} if held is None else {k: v for k, v in held.config.items() if k != "layout"}

    def steered_layout(self, node: str) -> L1Layout | None:
        held = self.steer.get(node)
        if held is None or (layout := held.config.get("layout")) is None:
            return None
        return L1Layout.model_validate(layout)

    def steered_onto(self, node: str, config: Mapping[str, Any]) -> L2L3Memory:
        held = self.steer.get(node)
        laid = ManifestNodeOverlay(config={**(held.config if held is not None else {}), **config})
        return self.model_copy(update={"steer": {**self.steer, node: laid}})

    def wounded(self, **channels: Any) -> L2L3Memory:
        return self.model_copy(update={"wounds": self.wounds.model_copy(update=channels)})


class LayerCounters(StrictModel):
    """The peak a stall is read against is seeded at the layer's first patience fire and moved only by an advance clearing it."""

    model_config = ConfigDict(frozen=True)

    fires: int = 0
    stall_count: int = 0
    best_composite_fitness_at_entry: float | None = None
    best_theta_at_entry: float | None = None


class Ladder(StrictModel):
    """``l1_stall_count`` is the PACING count a fire resets — never `rounds_without_advance`, which none does."""

    model_config = ConfigDict(frozen=True)

    l1_stall_count: int = 0
    l2: LayerCounters = LayerCounters()
    l3: LayerCounters = LayerCounters()


class PotterRoundState(RoundPayload, manifest=POTTER_MANIFEST):
    """``memory`` and ``ladder`` are banked whole at the round's close and restated whole by a fire that lands after it."""

    memory: L2L3Memory
    ladder: Ladder
    # Feedback FOR the next round's `l1_generate`, distilled after this round's scoring.
    critique: CritiqueReadout | None = None
    # STORED, not derived: the share of `l1_proposed` no reject posture killed, fixed before any score.
    l1_yield: float = 1.0
    l1_proposed: int = 0
    l1_rejected: dict[str, int] = Field(default_factory=dict)
    # ``None`` where no call was composed: round 0, a replayed generation.
    l1_citable: list[str] | None = None
    l1_exploration_budget: str | None = None
    # The round owns it: a parse failure yields no candidate to charge.
    l1_parse_failure: str | None = None
    # The AxisIndex's peaked axes as L1 proposed, which no later reading of the index rebuilds.
    axis_memory_peaked: list[str] = Field(default_factory=list)

    def feedback(self) -> CritiqueReadout | None:
        return self.critique

    def lost_to_empty_response(self) -> bool:
        return self.l1_parse_failure == PARSE_FAILURE_TOOLING
