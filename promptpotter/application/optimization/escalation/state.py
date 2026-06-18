"""Escalation FSM — L1/L2/L3 stall counters, observation methods, fold-over-ledger reducer.

Counters are private; only the observation methods + post-fire bookkeepers mutate. Read access is
property-only — there's no field to assign a `round >= N` literal to, so the "signals from
measurement, not calendar" rule is structural.

Out of scope: post-round routing (`.decide`), LLM calls (`.firing`), OSP mutations.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.domain.phases import StopReason
from promptpotter.domain.run_records import CycleRecord, PhaseRecord

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog


class NextAction(enum.StrEnum):
    """Round-loop's next action. STOP variants carry a `StopReason` via `EscalationEvent.stop_reason`."""

    CONTINUE = "continue"
    FIRE_L2 = "fire_l2"
    FIRE_L3 = "fire_l3"
    STOP_PERFECT = "stop_perfect"
    STOP_L3_PATIENCE = "stop_l3_patience"


_NEXT_ACTION_TO_STOP: dict[NextAction, StopReason] = {
    NextAction.STOP_PERFECT: StopReason.PERFECT,
    NextAction.STOP_L3_PATIENCE: StopReason.L3_PATIENCE,
}


@dataclass(frozen=True)
class EscalationEvent:
    """Escalation-observation outcome."""

    next_action: NextAction
    reason: str

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


class EscalationFSM:
    """Cause-driven L1/L2/L3 counters; mutation surface = observations + post-fire bookkeepers."""

    __slots__ = (
        "_l1_stall_count",
        "_l2_best_accuracy_at_entry",
        "_l2_best_composite_fitness_at_entry",
        "_l2_round",
        "_l2_stall_count",
        "_l3_best_accuracy_at_entry",
        "_l3_best_composite_fitness_at_entry",
        "_l3_round",
        "_l3_stall_count",
    )

    def __init__(self) -> None:
        self._l1_stall_count = 0
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_accuracy_at_entry = 0.0
        self._l2_best_composite_fitness_at_entry = 0.0
        self._l3_round = 0
        self._l3_stall_count = 0
        self._l3_best_accuracy_at_entry = 0.0
        self._l3_best_composite_fitness_at_entry = 0.0

    # ---- Read-only access (telemetry, decision payloads, prompt vars) ----

    @property
    def l1_stall_count(self) -> int:
        return self._l1_stall_count

    @property
    def l2_round(self) -> int:
        return self._l2_round

    @property
    def l2_stall_count(self) -> int:
        return self._l2_stall_count

    @property
    def l2_best_accuracy_at_entry(self) -> float:
        return self._l2_best_accuracy_at_entry

    @property
    def l2_best_composite_fitness_at_entry(self) -> float:
        return self._l2_best_composite_fitness_at_entry

    @property
    def l3_round(self) -> int:
        return self._l3_round

    @property
    def l3_stall_count(self) -> int:
        return self._l3_stall_count

    @property
    def l3_best_accuracy_at_entry(self) -> float:
        return self._l3_best_accuracy_at_entry

    @property
    def l3_best_composite_fitness_at_entry(self) -> float:
        return self._l3_best_composite_fitness_at_entry

    # ---- Observations: the only mutation surface ----

    def observe_round(
        self,
        *,
        improved: bool,
        current_accuracy: float,
        l1_patience: int,
        axes_with_positive_yield: int | None = None,
        l1_mandatory_breach: bool = False,
        evidence_starved: bool = False,
    ) -> EscalationEvent:
        """L1 round outcome — bumps stall, delegates the routing to `decide_escalation`."""
        from promptpotter.application.optimization.escalation.decide import (
            EscalationInputs,
            decide_escalation,
        )

        self._l1_stall_count = 0 if improved else self._l1_stall_count + 1

        inputs = EscalationInputs(
            current_accuracy=current_accuracy,
            l1_stall_count=self._l1_stall_count,
            l1_patience=l1_patience,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            evidence_starved=evidence_starved,
        )
        return decide_escalation(inputs)

    def observe_l2_escalation(
        self,
        *,
        current_composite_fitness: float,
        l2_patience: int | None,
        l3_patience: int | None,
    ) -> EscalationEvent:
        """L2 escalation requested. First-invocation grace: stall only advances after a layer has
        fired at least once (entry composite is the comparator).
        """
        if self._l2_round > 0:
            l2_improved = current_composite_fitness > self._l2_best_composite_fitness_at_entry
            self._l2_stall_count = 0 if l2_improved else self._l2_stall_count + 1

        if l2_patience is None or self._l2_stall_count < l2_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L2,
                reason=f"L2 stall {self._l2_stall_count}/{l2_patience}",
            )

        if self._l3_round > 0:
            l3_improved = current_composite_fitness > self._l3_best_composite_fitness_at_entry
            self._l3_stall_count = 0 if l3_improved else self._l3_stall_count + 1

        if l3_patience is None or self._l3_stall_count < l3_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L3,
                reason=f"L2 patience -> L3 stall {self._l3_stall_count}/{l3_patience}",
            )

        return EscalationEvent(
            next_action=NextAction.STOP_L3_PATIENCE,
            reason="L3 patience exhausted",
        )

    # ---- Post-fire bookkeepers ----

    def record_l2_fired(self, *, best_accuracy: float, best_composite_fitness: float) -> None:
        """L2 LLM completed. Bumps L2 round, captures entry origin; resets L1 stall."""
        self._l1_stall_count = 0
        self._l2_round += 1
        self._l2_best_accuracy_at_entry = best_accuracy
        self._l2_best_composite_fitness_at_entry = best_composite_fitness

    def record_l3_fired(self, *, best_accuracy: float, best_composite_fitness: float) -> None:
        """L3 fired. Bump L3, reset L1 stall + the L2 counter (new plan invalidates L2's progress)."""
        self._l1_stall_count = 0
        self._l3_round += 1
        self._l3_best_accuracy_at_entry = best_accuracy
        self._l3_best_composite_fitness_at_entry = best_composite_fitness
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_accuracy_at_entry = best_accuracy
        self._l2_best_composite_fitness_at_entry = best_composite_fitness

    # Reducer: round-complete → L1 stall; l2_context.exit → l2 state; l3_plan.exit → l3 state + l2 reset.
    # Live mutators above are the in-memory cache; from_ledger rebuilds on resume.

    def fold(self, record: CycleRecord) -> None:
        """Advance state from one ledger record. No-op for unrelated records."""
        if not isinstance(record, PhaseRecord):
            return
        if record.phase == "round" and record.event == "complete":
            # Audit emit only; display fires under "display" and is never folded. Probe rounds
            # emit "complete" so display + audit see them, but they aren't L1 progress evidence.
            if record.payload.get("is_probe"):
                return
            self._l1_stall_count = (
                0 if bool(record.payload["improved"]) else self._l1_stall_count + 1
            )
        elif record.phase == "l2_context" and record.event == "exit":
            escalation_state = record.payload["data"]
            self._l1_stall_count = 0
            self._l2_round = int(escalation_state["l2_round"])
            self._l2_stall_count = int(escalation_state["l2_stall_count"])
            self._l2_best_accuracy_at_entry = float(escalation_state["l2_best_accuracy_at_entry"])
            self._l2_best_composite_fitness_at_entry = float(
                escalation_state["l2_best_composite_fitness_at_entry"]
            )
        elif record.phase == "l3_plan" and record.event == "exit":
            escalation_state = record.payload["data"]
            best_acc = float(escalation_state["l3_best_accuracy_at_entry"])
            best_comp = float(escalation_state["l3_best_composite_fitness_at_entry"])
            self._l1_stall_count = 0
            self._l3_round = int(escalation_state["l3_round"])
            self._l3_stall_count = int(escalation_state["l3_stall_count"])
            self._l3_best_accuracy_at_entry = best_acc
            self._l3_best_composite_fitness_at_entry = best_comp
            # New plan invalidates L2's progress — wipe.
            self._l2_round = 0
            self._l2_stall_count = 0
            self._l2_best_accuracy_at_entry = best_acc
            self._l2_best_composite_fitness_at_entry = best_comp

    @classmethod
    def from_ledger(cls, ledger: CycleEventLog | None) -> EscalationFSM:
        """Rebuild state by folding every record in ``ledger``. ``None`` ⇒ fresh state."""
        s = cls()
        if ledger is None:
            return s
        for rec in ledger.iter():
            s.fold(rec)
        return s


__all__ = ["EscalationEvent", "EscalationFSM", "NextAction"]
