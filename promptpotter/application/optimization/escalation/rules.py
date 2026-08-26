"""The escalation POLICY, whole: what a round is judged on, the rules that judge it, and the
first-match router over them — (predicate, action, priority); higher wins, ties by list order. A
predicate is False when its signal is unavailable, so early cycles fall through rather than firing
blind. State mutation lives in ``EscalationFSM.observe_l1_round``, which is this module's only
caller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.application.optimization.escalation.state import EscalationEvent, NextAction


@dataclass(frozen=True)
class EscalationInputs:
    """Frozen snapshot of all escalation-rule predicate inputs. Optional fields are populated only
    when the corresponding derived state exists (e.g. AxisIndex initialised); rules must handle None.
    """

    current_accuracy: float
    l1_stall_count: int
    l1_patience: int
    # None until AxisIndex is initialised; runner populates from `cycle.axes.with_positive_yield()`.
    axes_with_positive_yield: int | None = None
    # A candidate this round dropped a mandatory backend placeholder (e.g. {{combined_text}}).
    # Structural breakage → immediate L2 re-frame, bypassing l1_patience (the "patience 0" path).
    l1_mandatory_breach: bool = False
    # l1_generate produced ZERO parseable candidates this round (empty/truncated provider output
    # or a schema-validation failure — `RoundResult.l1_parse_failure` set). Same class of fault as
    # a mandatory-placeholder breach: l1_generate's output is structurally unusable, and re-running
    # the identical optimizer prompt next round reproduces it. Heal L2 now instead of grinding l1_patience
    # dead rounds. Shares the `l1_generate_unusable` rule; the loud `l1_zero_candidates` round
    # warning already carries the malformed-vs-tooling detail L2 reads.
    l1_zero_candidates: bool = False
    # A node failed across ~all of the round's samples (evidence-starvation — accumulated, not
    # one fluke). A weak preemptor brings L2 in to diagnose; L2 self-heals or requests human
    # action. It NEVER stops the loop here — the stop authority stays with the LLM tier (R-48).
    evidence_starved: bool = False


PredicateFn = Callable[[EscalationInputs], bool]


@dataclass(frozen=True)
class EscalationRule:
    """Declarative rule; evaluated highest-priority first, first match wins.
    Fall-through = low-priority + ``when=lambda s: True``."""

    name: str
    when: PredicateFn
    fire: NextAction
    priority: int = 0


# perfect_accuracy preempts so a perfect-fit round terminates instead of firing L2;
# l1_generate_unusable preempts patience — l1_generate's output is structurally unusable this
#   round (a dropped mandatory placeholder OR zero parseable candidates), a fault no amount of
#   patience fixes because the identical optimizer prompt reproduces it; heal L2 now;
# l1_evidence_starved preempts patience — a node starved across ~all samples is accumulated
#   evidence of a systemic fault no L1 param move can fix; bring L2 in to diagnose (it never stops);
# l2_axis_yield_drought preempts patience when AxisIndex shows no productive axes;
# l1_patience=0 collapses "fire L2 every round" via the l1_to_l2 fall-through.
DEFAULT_ESCALATION_RULES: list[EscalationRule] = [
    EscalationRule(
        name="perfect_accuracy",
        when=lambda s: s.current_accuracy >= 1.0,
        fire=NextAction.STOP_PERFECT,
        priority=100,
    ),
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
        name="l2_axis_yield_drought",
        when=lambda s: (
            s.l1_stall_count >= 1
            and s.axes_with_positive_yield is not None
            and s.axes_with_positive_yield == 0
        ),
        fire=NextAction.FIRE_L2,
        priority=60,
    ),
    EscalationRule(
        name="l1_continue",
        when=lambda s: s.l1_stall_count < s.l1_patience,
        fire=NextAction.CONTINUE,
        priority=50,
    ),
    EscalationRule(
        name="l1_to_l2",
        when=lambda s: True,
        fire=NextAction.FIRE_L2,
        priority=10,
    ),
]


def decide_escalation(inputs: EscalationInputs) -> EscalationEvent:
    """Post-round router: priority-sort, first match wins, pure."""
    for rule in sorted(DEFAULT_ESCALATION_RULES, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(next_action=rule.fire)
    raise RuntimeError(
        "No escalation rule matched observe_round inputs "
        f"(rules={[r.name for r in DEFAULT_ESCALATION_RULES]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )


__all__ = [
    "DEFAULT_ESCALATION_RULES",
    "EscalationInputs",
    "EscalationRule",
    "decide_escalation",
]
