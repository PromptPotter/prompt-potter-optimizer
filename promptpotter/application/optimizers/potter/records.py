from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import Field

from promptpotter.domain.optimizer_state import PARSE_FAILURE_TOOLING, RoundOutput, StateSlice
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared.hashing import shapes_optimizer_prompt

PotterManifest = Literal["potter"]
POTTER_MANIFEST: PotterManifest = "potter"


class PotterCheckpointKind(CheckpointKind):
    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"


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


class Recombination(FrozenModel):
    """One recombination spec as a host wrote it: the main's and the donor's pool id heads, and the
    loci the donor gives."""

    main: str
    donor: str
    from_donor: list[str]
    changes_description: str = ""


class L2Memory(StateSlice, kind="l2_memory"):
    """What `l2_context` alone writes; a fire landing after its round's close restates it there."""

    recombinations: list[Recombination] = Field(
        default_factory=list,
        description="The children L2's last fire named for ``l1_generate`` to add in ``for_round``.",
    )
    for_round: int | None = Field(
        default=None,
        description="The round those children join; any other round's L1 adds none of them.",
    )

    steer: dict[str, ManifestNodeOverlay] = Field(
        default_factory=dict,
        description=(
            "What L2 wrote onto another node's config, keyed by that node — the "
            "shape the campaign's own overlay takes. L2 steers ``l1_generate``: its "
            "``layout`` (the panels ``DispatchHub.fill`` walks, whole) and the call "
            "settings it widens or narrows."
        ),
    )
    guard_breaches: list[ValidatorOutcome] = Field(
        default_factory=list,
        description="What the post-parse guards caught on L2's last output; rendered to L2 next fire.",
    )

    def steered(self, node: str) -> dict[str, Any]:
        held = self.steer.get(node)
        return {} if held is None else {k: v for k, v in held.config.items() if k != "layout"}

    def steered_layout(self, node: str) -> L1Layout | None:
        held = self.steer.get(node)
        if held is None or (layout := held.config.get("layout")) is None:
            return None
        return L1Layout.model_validate(layout)

    def steered_onto(self, node: str, config: Mapping[str, Any]) -> L2Memory:
        held = self.steer.get(node)
        laid = ManifestNodeOverlay(config={**(held.config if held is not None else {}), **config})
        return self.model_copy(update={"steer": {**self.steer, node: laid}})


class L3Memory(StateSlice, kind="l3_memory"):
    """What `l3_plan` alone writes; a fire landing after its round's close restates it there."""

    plan: str = Field(
        default="",
        description=(
            "Strategic frame written by ``l3_plan`` and read by every layer "
            "next round; persistent until the next L3 fire. Empty until L3 "
            "fires for the first time."
        ),
    )
    note: str = Field(default="", description="L3's sticky note to the layers below it.")
    guard_breaches: list[ValidatorOutcome] = Field(
        default_factory=list,
        description="What the post-parse guards caught on L3's last output.",
    )


class LayerCounters(FrozenModel):
    """The peak a stall is read against is seeded at the layer's first patience fire and moved only by an advance clearing it."""

    fires: int = 0
    stall_count: int = 0
    best_composite_fitness_at_entry: float | None = None
    theta_peak_round_at_entry: int | None = None


class Ladder(StateSlice, kind="ladder"):
    """``l1_stall_count`` is the PACING count a fire resets — never `rounds_without_advance`, which none does."""

    l1_stall_count: int = 0
    l2: LayerCounters = LayerCounters()
    l3: LayerCounters = LayerCounters()


class L1Readout(RoundOutput, kind="l1_readout"):
    """What this round's `l1_generate` call produced."""

    # STORED, not derived: the share of `l1_proposed` no reject posture killed, fixed before any score.
    # ``None`` where nothing was proposed: a share of zero proposals is no yield.
    l1_yield: float | None = None
    l1_proposed: int = 0
    l1_rejected: dict[str, int] = Field(default_factory=dict)
    # ``None`` where no call was composed: round 0, a replayed generation.
    l1_citable: list[str] | None = None
    l1_exploration_budget: str | None = None
    # The round owns it: a parse failure yields no candidate to charge.
    l1_parse_failure: str | None = None
    # The AxisIndex's peaked axes as L1 proposed, which no later reading of the index rebuilds.
    axis_memory_peaked: list[str] = Field(default_factory=list)

    def lost_to_empty_response(self) -> bool:
        return self.l1_parse_failure == PARSE_FAILURE_TOOLING
