"""What potter banks: its payload on every round document — the memory its escalation layers
author and the readouts only potter reads — and the decision kinds its members record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import Field

from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_TOOLING,
    CritiqueReadout,
    RoundPayload,
)
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "POTTER_MANIFEST",
    "L1Layout",
    "L2L3Memory",
    "PotterCheckpointKind",
    "PotterRoundState",
    "WoundChannels",
]

PotterManifest = Literal["potter"]
POTTER_MANIFEST: PotterManifest = "potter"


class PotterCheckpointKind(CheckpointKind):
    """Decisions potter's members take: its selector, its eliminator (PoBB), its controller."""

    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"


class WoundChannels(StrictModel):
    """Four wound streams + sticky L3 note; rendered by dispatch-hub injections."""

    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class L1Layout(StrictModel):
    """Per-slot list of placeholder names that the dispatch hub resolves
    when filling L1's PromptTemplate. Empty lists ⇒ the slot's static text only."""

    # The fields ARE the slots, declared in render order: `model_dump()` puts this order on disk,
    # and potter's `dispatch/layout.py` asserts it against `OptimizerPromptTemplate.RENDER_ORDER`.
    persona: list[str] = Field(default_factory=list)
    task_intent: list[str] = Field(default_factory=list)
    thinking_style: list[str] = Field(default_factory=list)
    problem_description: list[str] = Field(default_factory=list)

    @shapes_optimizer_prompt
    def all_placeholders(self) -> list[str]:
        """Every placed panel, in RENDER order."""
        return [name for slot in type(self).model_fields for name in self.slot(slot)]

    def slot(self, name: str) -> list[str]:
        if name not in type(self).model_fields:
            raise KeyError(f"Unknown L1 layout slot: {name}")
        return cast("list[str]", getattr(self, name))


class L2L3Memory(StrictModel):
    """Potter's persistent frame, carried across every adoption.

    The surfaces its escalation layers author: L2 writes most; L3 writes ``plan``,
    ``wounds.l3_note`` and ``wounds.l3_guard_breaches``; the dispatch-hub injections read all of
    it."""

    wounds: WoundChannels = Field(
        default_factory=WoundChannels,
        description=(
            "Four wound streams (validation/runtime/l2-guard/l3-guard) + "
            "sticky L3 note. Rendered by dispatch-hub injections; absorbed "
            "by L2 next round."
        ),
    )
    l1_layout: L1Layout = Field(
        description=(
            "L2-authored ordered list of injection slots that "
            "``DispatchHub.fill`` walks to compose the L1 optimizer prompt. "
            "L2's primary lever for changing what evidence L1 sees."
        ),
    )
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "L1 optimizer prompt overrides keyed by the surface field name "
            "(``persona``, ``instruction``, …). L2 writes here to nudge L1 "
            "without rewriting the shared optimizer prompt."
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

    def fire_writes(self) -> dict[str, Any]:
        """A fire's exit record banks this: the fire runs after its round's document is written,
        so no round holds it until the next one closes."""
        return self.model_dump(
            mode="json",
            include={
                "l1_layout": True,
                "l1_overrides": True,
                "plan": True,
                "wounds": {"l3_note", "l2_guard_breaches", "l3_guard_breaches"},
            },
        )

    def with_fire_writes(self, writes: Mapping[str, Any]) -> L2L3Memory:
        kept = self.model_dump(mode="json")
        wounds = {**kept["wounds"], **writes["wounds"]}
        return L2L3Memory.model_validate({**kept, **writes, "wounds": wounds})


class PotterRoundState(RoundPayload, manifest=POTTER_MANIFEST):
    """Potter's payload: the memory the round ended on and the readouts only potter reads."""

    memory: L2L3Memory
    # Feedback FOR the next round's `l1_generate`, distilled after this round's scoring.
    critique: CritiqueReadout | None = None
    # STORED, not derived: the share of `l1_proposed` no reject posture killed, fixed before any
    # score; `l1_rejected` counts each proposal under the first reason that cost it its measurement.
    l1_yield: float = 1.0
    l1_proposed: int = 0
    l1_rejected: dict[str, int] = Field(default_factory=dict)
    # The citation menu the wire schema offered `l1_generate` and the exploration budget its
    # prompt was composed under. ``None`` where no call was composed: round 0, a replayed generation.
    l1_citable: list[str] | None = None
    l1_exploration_budget: str | None = None
    # Why this round's L1 output was unparseable (zero candidates), or None. The round owns it:
    # a parse failure yields no candidate to charge. One of `domain/optimizer_state.py`'s three.
    l1_parse_failure: str | None = None
    # The AxisIndex's peaked axes as L1 proposed, which no later reading of the index rebuilds.
    axis_memory_peaked: list[str] = Field(default_factory=list)

    def feedback(self) -> CritiqueReadout | None:
        return self.critique

    def lost_to_empty_response(self) -> bool:
        return self.l1_parse_failure == PARSE_FAILURE_TOOLING
