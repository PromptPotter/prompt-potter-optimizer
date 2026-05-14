"""Escalation FSM — cause-driven L1/L2/L3 stall counters + observation methods.

This file participates in **escalation** as the decision-state
authority. Counters are private; the only mutation surface is the
observation methods (:meth:`EscalationState.observe_round`,
:meth:`EscalationState.observe_l2_escalation`) and the post-fire
bookkeepers (:meth:`EscalationState.record_l2_fired`,
:meth:`EscalationState.record_l3_fired`). Read access is via flat
``l{1,2,3}_*`` properties — there is no public setter for any counter,
so the "signals from measurement, not the calendar" rule (per
``promptpotter/CLAUDE.md``) is structural: there is no field to assign
a ``round_num >= N`` literal to.

State is a fold over ``CycleEventLog`` records; :meth:`EscalationState.fold`
advances one step, :meth:`EscalationState.from_ledger` rebuilds on resume.

Out-of-bounds: this file MUST NOT mutate ``OptSearchPoint`` fields
outside the documented escalation surface; MUST NOT decide
post-round routing (that's :func:`.decide.decide_escalation`); MUST
NOT fire LLM calls (that's :func:`.firing.escalate_l2`).
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
    """What the round loop does after an escalation observation.

    Computed inside :class:`EscalationState` from stall depth + layer history.
    Stop variants carry the matching :class:`StopReason` via
    :attr:`EscalationEvent.stop_reason`; ``CONTINUE`` / ``FIRE_*`` carry None.
    """

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
    """Outcome of one escalation observation — what the loop does next.

    ``stall_depth`` is the layer-relevant counter at decision time (L1 stall
    for ``observe_round``; L2 or L3 stall for ``observe_l2_escalation``).
    ``reason`` is human-readable for telemetry; consumers branch on
    ``next_action`` and read ``stop_reason`` for the StopLoop projection.

    ``rule_name`` / ``rule_priority`` are populated by
    :func:`escalation.decide.decide_escalation` (post-round path goes
    through the rule engine). The L2/L3 cascade in
    :meth:`EscalationState.observe_l2_escalation` is still imperative —
    those paths leave both fields ``None``.
    """

    next_action: NextAction
    stall_depth: int
    reason: str
    rule_name: str | None = None
    rule_priority: int | None = None

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


class EscalationState:
    """Cause-driven L1/L2/L3 escalation. Counters are private; the only
    mutation surface is the observation methods + post-fire bookkeepers.
    """

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
    ) -> EscalationEvent:
        """L1 round outcome. Bumps the stall counter; returns CONTINUE /
        FIRE_L2 / STOP_PERFECT.

        Counter mutation stays here (state ownership). The decision
        lifts into :func:`escalation.decide.decide_escalation` — this
        method delegates with a :class:`EscalationInputs` snapshot.
        ``axes_with_positive_yield`` populates the yield-driven rule
        when the cycle has AxisIndex evidence.

        L2/L3 stall observation lives in :meth:`observe_l2_escalation` so
        the mid-round signal path (DegradationCheck) shares the same cascade.
        """
        from promptpotter.application.optimization.escalation.decide import (
            EscalationInputs,
            decide_escalation,
        )

        self._l1_stall_count = 0 if improved else self._l1_stall_count + 1

        inputs = EscalationInputs(
            improved=improved,
            current_accuracy=current_accuracy,
            l1_stall_count=self._l1_stall_count,
            l1_patience=l1_patience,
            axes_with_positive_yield=axes_with_positive_yield,
        )
        return decide_escalation(inputs)

    def observe_l2_escalation(
        self,
        *,
        current_composite_fitness: float,
        l2_patience: int | None,
        l3_patience: int | None,
    ) -> EscalationEvent:
        """L2 escalation requested (L1 patience or mid-round signal). Updates
        the L2 stall counter (and L3's when cascading); returns FIRE_L2 /
        FIRE_L3 / STOP_L3_PATIENCE.

        First-invocation grace: ``stall_count`` only advances after a layer
        has fired at least once (``round > 0``) — the prior best composite_fitness
        captured at entry is the comparator.
        """
        if self._l2_round > 0:
            l2_improved = current_composite_fitness > self._l2_best_composite_fitness_at_entry
            self._l2_stall_count = 0 if l2_improved else self._l2_stall_count + 1

        if l2_patience is None or self._l2_stall_count < l2_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L2,
                stall_depth=self._l2_stall_count,
                reason=f"L2 stall {self._l2_stall_count}/{l2_patience}",
            )

        if self._l3_round > 0:
            l3_improved = current_composite_fitness > self._l3_best_composite_fitness_at_entry
            self._l3_stall_count = 0 if l3_improved else self._l3_stall_count + 1

        if l3_patience is None or self._l3_stall_count < l3_patience:
            return EscalationEvent(
                next_action=NextAction.FIRE_L3,
                stall_depth=self._l3_stall_count,
                reason=f"L2 patience -> L3 stall {self._l3_stall_count}/{l3_patience}",
            )

        return EscalationEvent(
            next_action=NextAction.STOP_L3_PATIENCE,
            stall_depth=self._l3_stall_count,
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
        """L3 LLM completed. Bumps L3 round, captures entry; resets L1 stall and
        the L2 counter — under a new plan, L2's prior progress is invalidated.
        """
        self._l1_stall_count = 0
        self._l3_round += 1
        self._l3_best_accuracy_at_entry = best_accuracy
        self._l3_best_composite_fitness_at_entry = best_composite_fitness
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_accuracy_at_entry = best_accuracy
        self._l2_best_composite_fitness_at_entry = best_composite_fitness

    # ---- Reducer over the ledger ----
    #
    # State is the fold of three PhaseRecord signals: round-complete (improved →
    # L1 stall), l2_context exit (L2 fired → l2 state), l3_plan exit (L3
    # fired → l3 state, l2 reset). The live mutators above are the in-memory
    # cache of this fold; ``from_ledger`` rebuilds the same value on resume.

    def fold(self, record: CycleRecord) -> None:
        """Advance state from one ledger record. No-op for unrelated records."""
        if not isinstance(record, PhaseRecord):
            return
        if record.phase == "round" and record.event == "complete":
            # Audit emit only: display fires under event="display" and is
            # never folded. The lean scalar payload is the SoT for resume.
            # ``is_probe`` rounds emit ``complete`` so display + audit see
            # them, but they are not L1 progress evidence — skip.
            if record.payload.get("is_probe"):
                return
            self._l1_stall_count = (
                0 if bool(record.payload["improved"]) else self._l1_stall_count + 1
            )
        elif record.phase == "l2_context" and record.event == "exit":
            escalation_state = record.payload.get("data") or {}
            self._l1_stall_count = 0
            self._l2_round = int(escalation_state["l2_round"])
            self._l2_stall_count = int(escalation_state["l2_stall_count"])
            self._l2_best_accuracy_at_entry = float(escalation_state["l2_best_accuracy_at_entry"])
            self._l2_best_composite_fitness_at_entry = float(
                escalation_state["l2_best_composite_fitness_at_entry"]
            )
        elif record.phase == "l3_plan" and record.event == "exit":
            escalation_state = record.payload.get("data") or {}
            best_acc = float(escalation_state["l3_best_accuracy_at_entry"])
            best_comp = float(escalation_state["l3_best_composite_fitness_at_entry"])
            self._l1_stall_count = 0
            self._l3_round = int(escalation_state["l3_round"])
            self._l3_stall_count = int(escalation_state["l3_stall_count"])
            self._l3_best_accuracy_at_entry = best_acc
            self._l3_best_composite_fitness_at_entry = best_comp
            # record_l3_fired wipes L2 — under a new plan, prior L2 stall is gone.
            self._l2_round = 0
            self._l2_stall_count = 0
            self._l2_best_accuracy_at_entry = best_acc
            self._l2_best_composite_fitness_at_entry = best_comp

    @classmethod
    def from_ledger(cls, ledger: CycleEventLog | None) -> EscalationState:
        """Rebuild state by folding every record in ``ledger``. ``None`` ⇒ fresh state."""
        s = cls()
        if ledger is None:
            return s
        for rec in ledger.iter():
            s.fold(rec)
        return s


__all__ = ["EscalationEvent", "EscalationState", "NextAction"]
