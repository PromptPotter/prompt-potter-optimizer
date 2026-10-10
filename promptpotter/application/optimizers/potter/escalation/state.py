from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimizers.potter.escalation.rules import (
    NEXT_ACTION_STOP,
    EscalationEvent,
    EscalationInputs,
    LadderInputs,
    NextAction,
    decide_climb,
    decide_escalation,
)
from promptpotter.application.optimizers.potter.records import Ladder, LayerCounters
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import ROUND_ADVANCE_INFO, RoundAdvance, StallEffect
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder


class PotterPhase(enum.StrEnum):
    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"


@shapes_optimizer_prompt
class ExplorationBudget(enum.StrEnum):
    """ONE source for the ``escalation_panel`` signal and the review writer's ``ValidatorContext``: prompt and validator agree."""

    TIGHT = "tight"  # improving — exploit the parent; speculative gambles rejected
    NORMAL = "normal"  # stalling — stall_exploration citations permitted
    WIDE = "wide"  # patience exhausted — explore freely; a PEAKED axis is mutable with a wide rebut


@shapes_optimizer_prompt
def exploration_budget(stall_count: int, l1_patience: int) -> ExplorationBudget:
    if stall_count <= 0:
        return ExplorationBudget.TIGHT
    if stall_count >= l1_patience:
        return ExplorationBudget.WIDE
    return ExplorationBudget.NORMAL


@dataclass(frozen=True)
class LayerReading:
    stall_count: int
    best_composite_fitness_at_entry: float | None
    best_theta_at_entry: float | None
    # ``None`` where the layer held no entry reading to compare.
    comparator: str | None

    def onto(self, held: LayerCounters, *, fired: bool = False) -> LayerCounters:
        return LayerCounters(
            fires=held.fires + fired,
            stall_count=self.stall_count,
            best_composite_fitness_at_entry=self.best_composite_fitness_at_entry,
            best_theta_at_entry=self.best_theta_at_entry,
        )


@dataclass(frozen=True, kw_only=True)
class LadderAsk:
    """L3's gate is read on every ask and binds only past L2's patience."""

    next_action: NextAction
    l2: LayerReading
    l3: LayerReading

    @property
    def stop_reason(self) -> StopReason | None:
        return NEXT_ACTION_STOP.get(self.next_action)


class EscalationFSM:
    """The ladder is replaced whole and never edited, so one banked on a round cannot move."""

    __slots__ = ("_ladder", "_matched_rule")

    def __init__(self, ladder: Ladder | None = None) -> None:
        self._ladder = ladder or Ladder()
        # Not banked: a resume observes a round before anything reads it.
        self._matched_rule: str | None = None

    @property
    def ladder(self) -> Ladder:
        return self._ladder

    @property
    def matched_rule(self) -> str:
        if self._matched_rule is None:
            raise RuntimeError("no round has been observed, so no rule has matched one")
        return self._matched_rule

    def _bank_round(self, advance: RoundAdvance) -> None:
        """Only an ADVANCE clears the stall: a crown is a point estimate, not separated from C0."""
        match ROUND_ADVANCE_INFO[advance].stall:
            case StallEffect.RESETS:
                self._ladder = self._ladder.model_copy(update={"l1_stall_count": 0})
            case StallEffect.COUNTS:
                self._ladder = self._ladder.model_copy(
                    update={"l1_stall_count": self._ladder.l1_stall_count + 1}
                )
            case StallEffect.STARTS | StallEffect.SKIPS:
                pass

    @staticmethod
    def _improved(
        current_comp: float,
        entry_comp: float,
        current_theta: float | None,
        entry_theta: float | None,
        current_theta_se: float | None = None,
    ) -> tuple[bool, str]:
        """The scale is NOT fixed per cycle: a layer entered before the ruler warmed compares composites."""
        if current_theta is not None and entry_theta is not None:
            return current_theta - entry_theta > (current_theta_se or 0.0), "theta"
        scale = "theta_appeared" if current_theta is not None else "composite"
        return current_comp > entry_comp, scale

    def _read_layer(
        self,
        layer: LayerCounters,
        current_comp: float | None,
        current_theta: float | None,
        current_theta_se: float | None,
    ) -> LayerReading:
        """Each scale's ratchet is seeded by its first reading: a ruler may warm late."""
        stall_count = layer.stall_count
        entry_comp = layer.best_composite_fitness_at_entry
        entry_theta = layer.best_theta_at_entry
        comp = current_comp if entry_comp is None else entry_comp
        theta = current_theta if entry_theta is None else entry_theta
        if entry_comp is None or current_comp is None:
            return LayerReading(stall_count, comp, theta, None)
        improved, comparator = self._improved(
            current_comp, entry_comp, current_theta, entry_theta, current_theta_se
        )
        if improved:
            return LayerReading(0, current_comp, current_theta, comparator)
        return LayerReading(stall_count + 1, entry_comp, theta, comparator)

    def ask_l2_escalation(
        self,
        *,
        current_composite_fitness: float | None,
        current_theta: float | None = None,
        current_theta_se: float | None = None,
        escalation_ladder: EscalationLadder,
        l2_patience: int,
        l3_patience: int | None,
    ) -> LadderAsk:
        """Mutates nothing: a fire that never parsed leaves the counters where the ledger has them."""
        now = (current_composite_fitness, current_theta, current_theta_se)
        l2 = self._read_layer(self._ladder.l2, *now)
        l3 = self._read_layer(self._ladder.l3, *now)
        climb = decide_climb(
            LadderInputs(
                escalation_ladder=escalation_ladder,
                l2_stall_count=l2.stall_count,
                l2_patience=l2_patience,
                l3_stall_count=l3.stall_count,
                l3_patience=l3_patience,
            )
        )
        return LadderAsk(next_action=climb.next_action, l2=l2, l3=l3)

    def as_read_by(self, ask: LadderAsk) -> EscalationFSM:
        held = self._ladder
        read = {"l2": ask.l2.onto(held.l2)}
        if ask.next_action is not NextAction.FIRE_L2:
            read["l3"] = ask.l3.onto(held.l3)
        return EscalationFSM(held.model_copy(update=read))

    def observe_round(
        self,
        *,
        advance: RoundAdvance,
        l1_patience: int,
        escalation_ladder: EscalationLadder,
        axes_with_positive_yield: int | None = None,
        l1_mandatory_breach: bool = False,
        l1_zero_candidates: bool = False,
        evidence_starved: bool = False,
    ) -> EscalationEvent:
        self._bank_round(advance)

        inputs = EscalationInputs(
            l1_stall_count=self._ladder.l1_stall_count,
            l1_patience=l1_patience,
            escalation_ladder=escalation_ladder,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            l1_zero_candidates=l1_zero_candidates,
            evidence_starved=evidence_starved,
        )
        event = decide_escalation(inputs)
        self._matched_rule = event.rule
        return event

    def record_l2_fired(self, reading: LayerReading) -> None:
        held = self._ladder
        self._ladder = held.model_copy(
            update={"l1_stall_count": 0, "l2": reading.onto(held.l2, fired=True)}
        )

    def record_l3_fired(self, reading: LayerReading | None) -> None:
        """``None`` is a heal: it answers a refused L2 edit, not a stall, and leaves L3's ratchet alone."""
        held = self._ladder
        l3 = (
            held.l3.model_copy(update={"fires": held.l3.fires + 1})
            if reading is None
            else reading.onto(held.l3, fired=True)
        )
        self._ladder = Ladder(l3=l3)


__all__ = [
    "EscalationFSM",
    "ExplorationBudget",
    "LadderAsk",
    "LayerReading",
    "PotterPhase",
    "exploration_budget",
]
