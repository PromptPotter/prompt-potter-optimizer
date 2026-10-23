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
    from promptpotter.domain.ruler import AbilityReading, LevelSeries


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
class HighWater:
    """Banks a θ peak's ROUND, never its θ: the ruler clock a θ was read at moves."""

    composite_fitness: float | None
    series: LevelSeries
    theta_round: int | None

    def level(self, rnd: int | None) -> AbilityReading | None:
        return None if rnd is None else self.series.level(rnd)

    def theta(self, rnd: int | None) -> float | None:
        level = self.level(rnd)
        return None if level is None else level.theta


@dataclass(frozen=True)
class LayerReading:
    stall_count: int
    best_composite_fitness_at_entry: float | None
    theta_peak_round_at_entry: int | None
    # ``None`` where the layer held no entry reading to compare.
    comparator: str | None

    def onto(self, held: LayerCounters, *, fired: bool = False) -> LayerCounters:
        return LayerCounters(
            fires=held.fires + fired,
            stall_count=self.stall_count,
            best_composite_fitness_at_entry=self.best_composite_fitness_at_entry,
            theta_peak_round_at_entry=self.theta_peak_round_at_entry,
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


def bank_round(ladder: Ladder, advance: RoundAdvance) -> Ladder:
    """Only an ADVANCE clears the stall: a crown is a point estimate, not separated from C0."""
    match ROUND_ADVANCE_INFO[advance].stall:
        case StallEffect.RESETS:
            return ladder.model_copy(update={"l1_stall_count": 0})
        case StallEffect.COUNTS:
            return ladder.model_copy(update={"l1_stall_count": ladder.l1_stall_count + 1})
        case StallEffect.STARTS | StallEffect.SKIPS:
            return ladder


def _improved(
    current_comp: float,
    entry_comp: float,
    current: AbilityReading | None,
    entry: AbilityReading | None,
) -> tuple[bool, str]:
    """The scale is NOT fixed per cycle: a layer entered before the ruler warmed compares composites."""
    if current is not None and entry is not None:
        return current.theta - entry.theta > (current.se or 0.0), "theta"
    scale = "theta_appeared" if current is not None else "composite"
    return current_comp > entry_comp, scale


def _read_layer(layer: LayerCounters, best: HighWater) -> LayerReading:
    """Each scale's ratchet is seeded by its first reading: a ruler may warm late."""
    stall_count = layer.stall_count
    entry_comp = layer.best_composite_fitness_at_entry
    entry = best.level(layer.theta_peak_round_at_entry)
    comp = best.composite_fitness if entry_comp is None else entry_comp
    peak = best.theta_round if entry is None else layer.theta_peak_round_at_entry
    if entry_comp is None or best.composite_fitness is None:
        return LayerReading(stall_count, comp, peak, None)
    improved, comparator = _improved(
        best.composite_fitness, entry_comp, best.level(best.theta_round), entry
    )
    if improved:
        return LayerReading(0, best.composite_fitness, best.theta_round, comparator)
    return LayerReading(stall_count + 1, entry_comp, peak, comparator)


def ask_l2_escalation(
    ladder: Ladder,
    *,
    best: HighWater,
    escalation_ladder: EscalationLadder,
    l2_patience: int,
    l3_patience: int | None,
) -> LadderAsk:
    """A fire that never parsed leaves the counters where the ledger has them."""
    l2 = _read_layer(ladder.l2, best)
    l3 = _read_layer(ladder.l3, best)
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


def as_read_by(ladder: Ladder, ask: LadderAsk) -> Ladder:
    read = {"l2": ask.l2.onto(ladder.l2)}
    if ask.next_action is not NextAction.FIRE_L2:
        read["l3"] = ask.l3.onto(ladder.l3)
    return ladder.model_copy(update=read)


def observe(
    ladder: Ladder,
    *,
    advance: RoundAdvance,
    l1_patience: int,
    escalation_ladder: EscalationLadder,
    axes_with_positive_yield: int | None = None,
    l1_mandatory_breach: bool = False,
    l1_zero_candidates: bool = False,
    evidence_starved: bool = False,
) -> tuple[Ladder, EscalationEvent]:
    banked = bank_round(ladder, advance)
    inputs = EscalationInputs(
        l1_stall_count=banked.l1_stall_count,
        l1_patience=l1_patience,
        escalation_ladder=escalation_ladder,
        axes_with_positive_yield=axes_with_positive_yield,
        l1_mandatory_breach=l1_mandatory_breach,
        l1_zero_candidates=l1_zero_candidates,
        evidence_starved=evidence_starved,
    )
    return banked, decide_escalation(inputs)


def l2_fired(ladder: Ladder, reading: LayerReading) -> Ladder:
    return ladder.model_copy(
        update={"l1_stall_count": 0, "l2": reading.onto(ladder.l2, fired=True)}
    )


def l3_fired(ladder: Ladder, reading: LayerReading | None) -> Ladder:
    """``None`` is a heal: it answers a refused L2 edit, not a stall, and leaves L3's ratchet alone."""
    l3 = (
        ladder.l3.model_copy(update={"fires": ladder.l3.fires + 1})
        if reading is None
        else reading.onto(ladder.l3, fired=True)
    )
    return Ladder(l3=l3)
