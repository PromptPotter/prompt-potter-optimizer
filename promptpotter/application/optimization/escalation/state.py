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
    from promptpotter.application.config import LivesConfig
    from promptpotter.infrastructure.ledger import CycleEventLog


class NextAction(enum.StrEnum):
    """Round-loop's next action. STOP variants carry a `StopReason` via `EscalationEvent.stop_reason`."""

    CONTINUE = "continue"
    FIRE_L2 = "fire_l2"
    FIRE_L3 = "fire_l3"
    STOP_PERFECT = "stop_perfect"
    STOP_L3_PATIENCE = "stop_l3_patience"
    STOP_LIVES = "stop_lives"


_NEXT_ACTION_TO_STOP: dict[NextAction, StopReason] = {
    NextAction.STOP_PERFECT: StopReason.PERFECT,
    NextAction.STOP_L3_PATIENCE: StopReason.L3_PATIENCE,
    NextAction.STOP_LIVES: StopReason.LIVES_EXHAUSTED,
}


@dataclass(frozen=True)
class EscalationEvent:
    """Escalation-observation outcome."""

    next_action: NextAction

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


class EscalationFSM:
    """Cause-driven L1/L2/L3 counters; mutation surface = observations + post-fire bookkeepers."""

    __slots__ = (
        "_l1_stall_count",
        "_l2_best_composite_fitness_at_entry",
        "_l2_best_theta_at_entry",
        "_l2_round",
        "_l2_stall_count",
        "_l3_best_composite_fitness_at_entry",
        "_l3_best_theta_at_entry",
        "_l3_round",
        "_l3_stall_count",
        "_lives",
    )

    def __init__(self) -> None:
        self._l1_stall_count = 0
        # Improvement-banked round budget ("hearts"). ``None`` until the first
        # lives-enabled round seeds it from ``LivesConfig.start`` — a banking sibling
        # of ``_l1_stall_count`` over the SAME per-round ``improved`` verdict, folded
        # identically on resume. Stays ``None`` for the whole run when lives mode is off.
        self._lives: int | None = None
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_composite_fitness_at_entry = 0.0
        self._l2_best_theta_at_entry: float | None = None
        self._l3_round = 0
        self._l3_stall_count = 0
        self._l3_best_composite_fitness_at_entry = 0.0
        self._l3_best_theta_at_entry: float | None = None

    # ---- Read-only access (telemetry, decision payloads, prompt vars) ----

    @property
    def l1_stall_count(self) -> int:
        return self._l1_stall_count

    @property
    def lives(self) -> int | None:
        """Current banked lives ('hearts'), or ``None`` when lives mode is off."""
        return self._lives

    @staticmethod
    def _bank_life(current: int | None, improved: bool, lives: LivesConfig) -> int:
        """Bank the round's ``improved`` verdict: +1 if improved, -1 if not, seeded
        from ``start`` on the first round, clamped to ``[0, cap]``. Used identically by
        the live ``observe_round`` and the resume ``fold`` so the two never diverge."""
        base = lives.start if current is None else current
        return max(0, min(lives.cap, base + (1 if improved else -1)))

    def would_exhaust_lives(self, improved: bool, lives: LivesConfig | None) -> bool:
        """Would banking this round's verdict empty the bank (i.e. stop the loop)?

        Pure lookahead — banks nothing. Lets a caller know THIS round is the last one
        before it spends an LLM call on output only the NEXT round could read. Reads
        through ``_bank_life`` rather than re-deriving the arithmetic, so the answer can
        never disagree with what ``observe_round`` is about to do."""
        if lives is None:
            return False
        return self._bank_life(self._lives, improved, lives) == 0

    @property
    def l2_round(self) -> int:
        return self._l2_round

    @property
    def l2_stall_count(self) -> int:
        return self._l2_stall_count

    @property
    def l2_best_composite_fitness_at_entry(self) -> float:
        return self._l2_best_composite_fitness_at_entry

    @property
    def l2_best_theta_at_entry(self) -> float | None:
        return self._l2_best_theta_at_entry

    @property
    def l3_round(self) -> int:
        return self._l3_round

    @property
    def l3_stall_count(self) -> int:
        return self._l3_stall_count

    @property
    def l3_best_composite_fitness_at_entry(self) -> float:
        return self._l3_best_composite_fitness_at_entry

    @property
    def l3_best_theta_at_entry(self) -> float | None:
        return self._l3_best_theta_at_entry

    # ---- Improvement comparator: difficulty-adjusted θ when the ruler is live ----

    @staticmethod
    def _improved(
        current_comp: float,
        entry_comp: float,
        current_theta: float | None,
        entry_theta: float | None,
    ) -> bool:
        """Did the cycle's best advance since a layer fired? Prefers difficulty-adjusted
        ability θ when the per-cycle ruler is live (both θ present), else composite. θ
        stays cross-round comparable once per-round subsets drift, where composite (a raw
        rate over whichever samples ran) does not. The ruler is fixed per cycle, so the
        choice never flips mid-cycle — θ is present every round or none (R: slice 2)."""
        if current_theta is not None and entry_theta is not None:
            return current_theta > entry_theta
        return current_comp > entry_comp

    # ---- Observations: the only mutation surface ----

    def observe_round(
        self,
        *,
        improved: bool,
        current_accuracy: float,
        l1_patience: int,
        lives: LivesConfig | None = None,
        axes_with_positive_yield: int | None = None,
        l1_mandatory_breach: bool = False,
        evidence_starved: bool = False,
    ) -> EscalationEvent:
        """L1 round outcome — bumps stall (+ banks lives when enabled), delegates routing
        to `decide_escalation`. When the lives bank hits zero it stops the loop — but only
        if `decide_escalation` did not already stop on its own (a natural PERFECT / L3
        convergence keeps its more-specific reason); an exhausted bank overrides a would-be
        CONTINUE or escalation, skipping the doomed final layer fire."""
        from promptpotter.application.optimization.escalation.decide import (
            EscalationInputs,
            decide_escalation,
        )

        self._l1_stall_count = 0 if improved else self._l1_stall_count + 1
        if lives is not None:
            self._lives = self._bank_life(self._lives, improved, lives)

        inputs = EscalationInputs(
            current_accuracy=current_accuracy,
            l1_stall_count=self._l1_stall_count,
            l1_patience=l1_patience,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            evidence_starved=evidence_starved,
        )
        event = decide_escalation(inputs)
        if event.stop_reason is None and lives is not None and self._lives == 0:
            return EscalationEvent(next_action=NextAction.STOP_LIVES)
        return event

    def observe_l2_escalation(
        self,
        *,
        current_composite_fitness: float,
        current_theta: float | None = None,
        l2_patience: int | None,
        l3_patience: int | None,
    ) -> EscalationEvent:
        """L2 escalation requested. First-invocation grace: stall only advances after a layer has
        fired at least once (the entry θ/composite is the comparator).
        """
        if self._l2_round > 0:
            l2_improved = self._improved(
                current_composite_fitness,
                self._l2_best_composite_fitness_at_entry,
                current_theta,
                self._l2_best_theta_at_entry,
            )
            self._l2_stall_count = 0 if l2_improved else self._l2_stall_count + 1

        if l2_patience is None or self._l2_stall_count < l2_patience:
            return EscalationEvent(next_action=NextAction.FIRE_L2)

        if self._l3_round > 0:
            l3_improved = self._improved(
                current_composite_fitness,
                self._l3_best_composite_fitness_at_entry,
                current_theta,
                self._l3_best_theta_at_entry,
            )
            self._l3_stall_count = 0 if l3_improved else self._l3_stall_count + 1

        if l3_patience is None or self._l3_stall_count < l3_patience:
            return EscalationEvent(next_action=NextAction.FIRE_L3)

        return EscalationEvent(next_action=NextAction.STOP_L3_PATIENCE)

    # ---- Post-fire bookkeepers ----

    def record_l2_fired(
        self,
        *,
        best_composite_fitness: float,
        best_theta: float | None = None,
    ) -> None:
        """L2 LLM completed. Bumps L2 round, captures entry origin; resets L1 stall."""
        self._l1_stall_count = 0
        self._l2_round += 1
        self._l2_best_composite_fitness_at_entry = best_composite_fitness
        self._l2_best_theta_at_entry = best_theta

    def record_l3_fired(
        self,
        *,
        best_composite_fitness: float,
        best_theta: float | None = None,
    ) -> None:
        """L3 fired. Bump L3, reset L1 stall + the L2 counter (new plan invalidates L2's progress)."""
        self._l1_stall_count = 0
        self._l3_round += 1
        self._l3_best_composite_fitness_at_entry = best_composite_fitness
        self._l3_best_theta_at_entry = best_theta
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_composite_fitness_at_entry = best_composite_fitness
        self._l2_best_theta_at_entry = best_theta

    # Reducer: round-complete → L1 stall; l2_context.exit → l2 state; l3_plan.exit → l3 state + l2 reset.
    # Live mutators above are the in-memory cache; from_ledger rebuilds on resume.

    def fold(self, record: CycleRecord, *, lives: LivesConfig | None = None) -> None:
        """Advance state from one ledger record. No-op for unrelated records. ``lives``
        (when set) reconstructs the banked-lives accumulator from the same ``improved``
        sequence that drives the stall counter — so resume rebuilds it exactly, no new
        persisted field."""
        if not isinstance(record, PhaseRecord):
            return
        if record.phase == "round" and record.event == "complete":
            # Audit emit only; display fires under "display" and is never folded. Probe rounds
            # emit "complete" so display + audit see them, but they aren't L1 progress evidence.
            if record.payload.get("is_probe"):
                return
            improved = bool(record.payload["improved"])
            self._l1_stall_count = 0 if improved else self._l1_stall_count + 1
            if lives is not None:
                self._lives = self._bank_life(self._lives, improved, lives)
        elif record.phase == "l2_context" and record.event == "exit":
            escalation_state = record.payload["data"]
            self._l1_stall_count = 0
            self._l2_round = int(escalation_state["l2_round"])
            self._l2_stall_count = int(escalation_state["l2_stall_count"])
            self._l2_best_composite_fitness_at_entry = float(
                escalation_state["l2_best_composite_fitness_at_entry"]
            )
            l2_theta = escalation_state.get("l2_best_theta_at_entry")
            self._l2_best_theta_at_entry = None if l2_theta is None else float(l2_theta)
        elif record.phase == "l3_plan" and record.event == "exit":
            escalation_state = record.payload["data"]
            best_comp = float(escalation_state["l3_best_composite_fitness_at_entry"])
            l3_theta = escalation_state.get("l3_best_theta_at_entry")
            best_theta = None if l3_theta is None else float(l3_theta)
            self._l1_stall_count = 0
            self._l3_round = int(escalation_state["l3_round"])
            self._l3_stall_count = int(escalation_state["l3_stall_count"])
            self._l3_best_composite_fitness_at_entry = best_comp
            self._l3_best_theta_at_entry = best_theta
            # New plan invalidates L2's progress — wipe.
            self._l2_round = 0
            self._l2_stall_count = 0
            self._l2_best_composite_fitness_at_entry = best_comp
            self._l2_best_theta_at_entry = best_theta

    @classmethod
    def from_ledger(
        cls, ledger: CycleEventLog | None, *, lives: LivesConfig | None = None
    ) -> EscalationFSM:
        """Rebuild state by folding every record in ``ledger``. ``None`` ⇒ fresh state.
        Pass ``lives`` to reconstruct the banked-lives accumulator on resume."""
        s = cls()
        if ledger is None:
            return s
        for rec in ledger.iter():
            s.fold(rec, lives=lives)
        return s


__all__ = ["EscalationEvent", "EscalationFSM", "NextAction"]
