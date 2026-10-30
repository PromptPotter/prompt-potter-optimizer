"""A predicate is False when its signal is unavailable, so an early cycle falls through, never fires blind."""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.domain.phases import StopReason

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder


class NextAction(enum.StrEnum):
    CONTINUE = "continue"
    FIRE_L2 = "fire_l2"
    FIRE_L3 = "fire_l3"
    STOP_L3_PATIENCE = "stop_l3_patience"


NEXT_ACTION_STOP: dict[NextAction, StopReason] = {
    NextAction.STOP_L3_PATIENCE: StopReason.OPTIMIZER_EXHAUSTED,
}


@dataclass(frozen=True)
class EscalationEvent:
    next_action: NextAction
    # ``None`` where the FSM decided alone.
    rule: str | None = None

    @property
    def stop_reason(self) -> StopReason | None:
        return NEXT_ACTION_STOP.get(self.next_action)


@dataclass(frozen=True)
class EscalationInputs:
    l1_stall_count: int
    l1_patience: int
    escalation_ladder: EscalationLadder
    l1_mandatory_breach: bool = False
    l1_zero_candidates: bool = False
    # NEVER stops the loop here: the stop authority stays with the LLM tier.
    evidence_starved: bool = False


@dataclass(frozen=True)
class LadderInputs:
    escalation_ladder: EscalationLadder
    l2_stall_count: int
    l2_patience: int
    l3_stall_count: int
    l3_patience: int | None
    l1_layout_refused: bool = False


@dataclass(frozen=True)
class EscalationRule[S]:
    name: str
    when: Callable[[S], bool]
    fire: NextAction
    priority: int = 0


L1_ONLY_LADDER = "l1_only_ladder"


def _l1_only[S: (EscalationInputs, LadderInputs)]() -> EscalationRule[S]:
    return EscalationRule(
        name=L1_ONLY_LADDER,
        when=lambda s: not s.escalation_ladder.fires_l2,
        fire=NextAction.CONTINUE,
        priority=90,
    )


# Every rule above `l1_continue` preempts patience; `l1_only_ladder` preempts every FIRE_L2 below it.
DEFAULT_ESCALATION_RULES: list[EscalationRule[EscalationInputs]] = [
    _l1_only(),
    EscalationRule(
        name="l1_generate_unusable",
        when=lambda s: s.l1_mandatory_breach or s.l1_zero_candidates,
        fire=NextAction.FIRE_L2,
        priority=70,
    ),
    EscalationRule(
        name="l1_evidence_starved",
        when=lambda s: s.evidence_starved,
        fire=NextAction.FIRE_L2,
        priority=65,
    ),
    EscalationRule(
        name="l1_continue",
        when=lambda s: s.l1_stall_count < s.l1_patience,
        fire=NextAction.CONTINUE,
        priority=50,
    ),
    EscalationRule(
        name="l1_to_l2",
        when=lambda _s: True,
        fire=NextAction.FIRE_L2,
        priority=10,
    ),
]


L2_PATIENCE_SPENT = "l2_patience_spent"
L1_LAYOUT_REFUSED = "l1_layout_refused"

# Its first rule holds a climb no cause reaches past `observe` (`--diag`) to the ladder's depth.
CLIMB_RULES: list[EscalationRule[LadderInputs]] = [
    _l1_only(),
    EscalationRule(
        name="l2_within_patience",
        when=lambda s: not s.escalation_ladder.fires_l3 or s.l2_stall_count < s.l2_patience,
        fire=NextAction.FIRE_L2,
        priority=30,
    ),
    EscalationRule(
        name=L2_PATIENCE_SPENT,
        when=lambda s: s.l3_patience is None or s.l3_stall_count < s.l3_patience,
        fire=NextAction.FIRE_L3,
        priority=20,
    ),
    EscalationRule(
        name="l3_patience_spent",
        when=lambda _s: True,
        fire=NextAction.STOP_L3_PATIENCE,
        priority=10,
    ),
]

HEAL_RULES: list[EscalationRule[LadderInputs]] = [
    EscalationRule(
        name=L1_LAYOUT_REFUSED,
        when=lambda s: s.escalation_ladder.fires_l3 and s.l1_layout_refused,
        fire=NextAction.FIRE_L3,
        priority=20,
    ),
    EscalationRule(
        name="l2_landed",
        when=lambda _s: True,
        fire=NextAction.CONTINUE,
        priority=10,
    ),
]


def _first_match[S](rules: list[EscalationRule[S]], inputs: S) -> EscalationEvent:
    for rule in sorted(rules, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(next_action=rule.fire, rule=rule.name)
    raise RuntimeError(
        f"No escalation rule matched (rules={[r.name for r in rules]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )


def decide_escalation(inputs: EscalationInputs) -> EscalationEvent:
    return _first_match(DEFAULT_ESCALATION_RULES, inputs)


def decide_climb(inputs: LadderInputs) -> EscalationEvent:
    return _first_match(CLIMB_RULES, inputs)


def decide_heal(inputs: LadderInputs) -> EscalationEvent:
    return _first_match(HEAL_RULES, inputs)
