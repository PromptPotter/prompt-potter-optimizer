"""Post-round escalation router — priority-sort, first-match, pure. State mutation lives in ``observe_round``; this is
post-fold."""

from __future__ import annotations

from dataclasses import dataclass

from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    NextAction,
)

__all__ = ["EscalationEvent", "EscalationInputs", "NextAction", "decide_escalation"]


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


def decide_escalation(inputs: EscalationInputs) -> EscalationEvent:
    from promptpotter.application.optimization.escalation.rules import (
        DEFAULT_ESCALATION_RULES,
    )

    for rule in sorted(DEFAULT_ESCALATION_RULES, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(next_action=rule.fire)
    raise RuntimeError(
        "No escalation rule matched observe_round inputs "
        f"(rules={[r.name for r in DEFAULT_ESCALATION_RULES]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )
