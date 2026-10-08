"""Escalation FSM — L1/L2/L3 stall counters, observations, and the fold-over-ledger reducer. Read
access is property-only, so "signals from measurement, not calendar" is structural, not a rule."""

from __future__ import annotations

import copy
import enum
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.domain.phases import StopReason
from promptpotter.domain.run_records import CycleRecord, PhaseRecord, view_fields
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder, LivesConfig
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog


class PotterPhase(enum.StrEnum):
    """Potter's controller phases: L2 refines the strategy, L3 modifies the plan."""

    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"


@shapes_optimizer_prompt
class ExplorationBudget(enum.StrEnum):
    """How freely ``l1_generate`` may explore. The single source for the ``escalation_panel.exploration_budget`` signal AND
    for the value the review writer feeds ``ValidatorContext``, so prompt and validator cannot disagree."""

    TIGHT = "tight"  # improving — exploit the parent; speculative gambles rejected
    NORMAL = "normal"  # stalling — stall_exploration citations permitted
    WIDE = "wide"  # patience exhausted — explore freely; a PEAKED axis is mutable with a wide rebut


@shapes_optimizer_prompt
def exploration_budget(stall_count: int, l1_patience: int) -> ExplorationBudget:
    """Widen the budget with MEASURED L1 stall depth, never a round-count schedule. Pure; the round banks the result and the
    validators read the banked value."""
    if stall_count <= 0:
        return ExplorationBudget.TIGHT
    if stall_count >= l1_patience:
        return ExplorationBudget.WIDE
    return ExplorationBudget.NORMAL


@shapes_optimizer_prompt
def round_advanced(improved: bool, separable: bool | None) -> bool:
    """``separable is None`` is a round whose arms carried no interval: unreadable, so it banks
    as ``improved`` alone decides."""
    return improved and separable is not False


@shapes_optimizer_prompt
def l1_stall_depth(rounds: Sequence[RoundResult]) -> int:
    """Closed rounds since the last advance, read off the round documents. No fire resets it,
    which is what sets it apart from ``EscalationFSM.l1_stall_count``, the pacing counter."""
    depth = 0
    for rr in reversed(rounds):
        if rr.round == 0 or round_advanced(rr.improved, rr.separable):
            break
        depth += 1
    return depth


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
    NextAction.STOP_L3_PATIENCE: StopReason.CONVERGED,
    NextAction.STOP_LIVES: StopReason.LIVES_EXHAUSTED,
}


@dataclass(frozen=True)
class EscalationEvent:
    next_action: NextAction
    # The `DEFAULT_ESCALATION_RULES` member that matched; ``None`` where the FSM decided alone.
    rule: str | None = None

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


@dataclass(frozen=True)
class LayerReading:
    """One layer's stall verdict at an ask: what the layer's counters become if its fire lands."""

    stall_count: int
    best_composite_fitness_at_entry: float | None
    best_theta_at_entry: float | None
    # Which scale `_improved` read; ``None`` where the layer held no entry reading to compare.
    comparator: str | None


@dataclass(frozen=True, kw_only=True)
class LadderAsk:
    """An L2 ask's verdict. L3's gate is read on every ask and binds only past L2's patience."""

    next_action: NextAction
    l2: LayerReading
    l3: LayerReading

    @property
    def stop_reason(self) -> StopReason | None:
        return _NEXT_ACTION_TO_STOP.get(self.next_action)


class EscalationFSM:
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
        "_matched_rule",
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
        # The ratchet `_improved` differences against: seeded at the layer's first patience fire
        # and moved only by an advance that cleared it. ``None`` until that fire.
        self._l2_best_composite_fitness_at_entry: float | None = None
        self._l2_best_theta_at_entry: float | None = None
        self._l3_round = 0
        self._l3_stall_count = 0
        self._l3_best_composite_fitness_at_entry: float | None = None
        self._l3_best_theta_at_entry: float | None = None
        # Which rule decided the last observed round. Not persisted: a resume observes a round
        # before anything reads it.
        self._matched_rule: str | None = None

    # ---- Read-only access (telemetry, decision payloads, prompt vars) ----

    @property
    def l1_stall_count(self) -> int:
        return self._l1_stall_count

    @property
    def matched_rule(self) -> str:
        if self._matched_rule is None:
            raise RuntimeError("no round has been observed, so no rule has matched one")
        return self._matched_rule

    @property
    def lives(self) -> int | None:
        return self._lives

    @staticmethod
    def _bank_life(
        current: int | None, improved: bool, lives: LivesConfig, *, compared: bool = True
    ) -> int:
        """Bank the round's ``improved`` verdict, clamped to ``[0, cap]``. ``compared=False`` banks NOTHING:
        no candidate reached the election, so it is evidence about l1_generate, not about the search."""
        base = lives.start if current is None else current
        if not compared:
            return max(0, min(lives.cap, base))
        return max(0, min(lives.cap, base + (1 if improved else -1)))

    def _bank_round(
        self, improved: bool, lives: LivesConfig | None, *, compared: bool, separable: bool | None
    ) -> None:
        """Advance the L1 accumulators — live ``observe_round`` and resume ``fold`` both land here. They
        diverge on ``compared``: an uncompared round still advances the STALL counter, at no life cost.

        Only an ADVANCE clears the stall. A round crowns a winner whenever one arm out-ranks the
        parent on θ, which is a point estimate — so a pick that has not separated from C0 on the
        origin panel sets ``improved`` and advanced nothing, and resetting patience on it spends
        the budget on coin flips."""
        advanced = round_advanced(improved, separable)
        self._l1_stall_count = 0 if advanced else self._l1_stall_count + 1
        # The life bank still reads `improved` alone: patience asks "does L1 need help", which a
        # round that resolved nothing answers yes to, while the bank asks "was this round worth
        # another", and moving both on one edit would leave neither reading attributable.
        if lives is not None:
            self._lives = self._bank_life(self._lives, improved, lives, compared=compared)

    def would_exhaust_lives(
        self, improved: bool, lives: LivesConfig | None, *, compared: bool = True
    ) -> bool:
        """Would banking this round empty the bank? Pure lives-bank lookahead — reads THROUGH ``_bank_life`` so it can
        never disagree with what ``observe_round`` is about to do."""
        if lives is None:
            return False
        return self._bank_life(self._lives, improved, lives, compared=compared) == 0

    @property
    def l2_round(self) -> int:
        return self._l2_round

    @property
    def l2_stall_count(self) -> int:
        return self._l2_stall_count

    @property
    def l2_best_composite_fitness_at_entry(self) -> float | None:
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
    def l3_best_composite_fitness_at_entry(self) -> float | None:
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
        current_theta_se: float | None = None,
    ) -> tuple[bool, str]:
        """Did the cycle's best advance since a layer fired, and ON WHICH SCALE — θ when both readings
        carry one, composite otherwise.

        A θ advance must CLEAR ITS OWN ERROR, or a rise of a few hundredths of one standard error
        resets the stall counter and pins the ladder where it stands. ``current_theta_se`` is the
        bar because it is the reading the caller has — the entry SE is not persisted, which makes
        this the lenient side of the honest comparison rather than the strict one.

        The scale is NOT fixed per cycle, which the previous wording claimed: ``entry_theta`` is
        captured when the layer FIRES, so a layer entering before the ruler warms compares composites
        for the rest of the run while a later layer compares θ. That ``theta_appeared`` case is the
        one worth seeing — θ is available and the verdict is not using it — and composite is only
        comparable to its own past while the formula holds (`persistence-and-state.md` § Changing the
        composite formula), which is why the caller records the answer rather than inferring it."""
        if current_theta is not None and entry_theta is not None:
            return current_theta - entry_theta > (current_theta_se or 0.0), "theta"
        scale = "theta_appeared" if current_theta is not None else "composite"
        return current_comp > entry_comp, scale

    def _read_layer(
        self,
        stall_count: int,
        entry_comp: float | None,
        entry_theta: float | None,
        current_comp: float | None,
        current_theta: float | None,
        current_theta_se: float | None,
    ) -> LayerReading:
        """A layer that has not fired, or a cycle with no peak, stalls on nothing. A cleared advance
        moves the ratchet, and each scale's is seeded by its first reading: a ruler may warm late."""
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
        """Where an L2 ask routes. Mutates nothing: the layer that lands commits its reading, so a
        fire that never parsed leaves the counters where the ledger has them."""
        l2 = self._read_layer(
            self._l2_stall_count,
            self._l2_best_composite_fitness_at_entry,
            self._l2_best_theta_at_entry,
            current_composite_fitness,
            current_theta,
            current_theta_se,
        )
        l3 = self._read_layer(
            self._l3_stall_count,
            self._l3_best_composite_fitness_at_entry,
            self._l3_best_theta_at_entry,
            current_composite_fitness,
            current_theta,
            current_theta_se,
        )
        if not escalation_ladder.fires_l3 or l2.stall_count < l2_patience:
            action = NextAction.FIRE_L2
        elif l3_patience is None or l3.stall_count < l3_patience:
            action = NextAction.FIRE_L3
        else:
            action = NextAction.STOP_L3_PATIENCE
        return LadderAsk(next_action=action, l2=l2, l3=l3)

    def as_read_by(self, ask: LadderAsk) -> EscalationFSM:
        """A copy holding the verdict ``ask`` read, which the prompt its fire composes reports.
        L3's half shows only where the ask reached L3's gate."""
        seen = copy.copy(self)
        seen._l2_stall_count = ask.l2.stall_count
        seen._l2_best_composite_fitness_at_entry = ask.l2.best_composite_fitness_at_entry
        seen._l2_best_theta_at_entry = ask.l2.best_theta_at_entry
        if ask.next_action is not NextAction.FIRE_L2:
            seen._l3_stall_count = ask.l3.stall_count
            seen._l3_best_composite_fitness_at_entry = ask.l3.best_composite_fitness_at_entry
            seen._l3_best_theta_at_entry = ask.l3.best_theta_at_entry
        return seen

    # ---- Mutation: a closed round, then a landed fire — each a record `fold` reads back ----

    def observe_round(
        self,
        *,
        improved: bool,
        compared: bool,
        separable: bool | None,
        current_objective: float | None,
        l1_patience: int,
        escalation_ladder: EscalationLadder,
        lives: LivesConfig | None = None,
        axes_with_positive_yield: int | None = None,
        l1_mandatory_breach: bool = False,
        l1_zero_candidates: bool = False,
        evidence_starved: bool = False,
    ) -> EscalationEvent:
        """L1 round outcome; routing delegates to `decide_escalation`. An emptied life bank overrides a
        CONTINUE or an escalation, but never a more-specific stop (a natural PERFECT / L3 convergence)."""
        # Deferred: `rules` imports this module's `NextAction` / `EscalationEvent` at module level,
        # so the policy layer sits above the state it reports on and this is the one edge back down.
        from promptpotter.application.optimizers.potter.escalation.rules import (
            EscalationInputs,
            decide_escalation,
        )

        self._bank_round(improved, lives, compared=compared, separable=separable)

        inputs = EscalationInputs(
            current_objective=current_objective,
            l1_stall_count=self._l1_stall_count,
            l1_patience=l1_patience,
            escalation_ladder=escalation_ladder,
            # Already banked above; the objective-ceiling stop reads it too, because a headline is
            # not a result until you know whether the round it came from resolved anything.
            separable=separable,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            l1_zero_candidates=l1_zero_candidates,
            evidence_starved=evidence_starved,
        )
        event = decide_escalation(inputs)
        self._matched_rule = event.rule
        if event.stop_reason is None and lives is not None and self._lives == 0:
            return EscalationEvent(next_action=NextAction.STOP_LIVES)
        return event

    def record_l2_fired(self, reading: LayerReading) -> None:
        self._l1_stall_count = 0
        self._l2_round += 1
        self._l2_stall_count = reading.stall_count
        self._l2_best_composite_fitness_at_entry = reading.best_composite_fitness_at_entry
        self._l2_best_theta_at_entry = reading.best_theta_at_entry

    def record_l3_fired(self, reading: LayerReading | None) -> None:
        """A new plan invalidates L2's progress, so L2's counters clear. ``None`` is a heal: it
        answers a refused L2 edit, not a stall, and leaves L3's ratchet alone."""
        self._l1_stall_count = 0
        self._l3_round += 1
        if reading is not None:
            self._l3_stall_count = reading.stall_count
            self._l3_best_composite_fitness_at_entry = reading.best_composite_fitness_at_entry
            self._l3_best_theta_at_entry = reading.best_theta_at_entry
        self._l2_round = 0
        self._l2_stall_count = 0
        self._l2_best_composite_fitness_at_entry = None
        self._l2_best_theta_at_entry = None

    # Reducer: round-complete → L1 stall; refine_strategy.exit → l2 state; modify_plan.exit → l3
    # state + l2 reset. The mutators above are the in-memory cache; from_ledger rebuilds on resume.
    # Match the CampaignPhase members, never their spellings — `phase` is a bare `str`, so only the
    # enum reference makes a wrong name an import-time AttributeError instead of an arm that
    # silently never matches. The L2/L3 node names are NOT their phase names.
    #
    # Read the counters off `payload["view"]["state"]` — the PERSISTED half; `PhaseRecord.data`
    # never reaches disk. A step that adopted nothing banks no state, and advanced none.

    def fold(self, record: CycleRecord, *, lives: LivesConfig | None = None) -> None:
        """Advance state from one ledger record. ``lives`` reconstructs from the same ``improved`` sequence
        that drives the stall counter, so resume rebuilds the bank exactly with no persisted field."""
        if not isinstance(record, PhaseRecord):
            return
        if record.phase == "round" and record.event == "complete":
            # Audit emit only; display fires under "display" and is never folded.
            #
            # ROUND 0 BANKS NOTHING — the origin is the baseline, with no prior round to have
            # improved over. Live that rule is structural and unwritten (`emit_origin_round`
            # reaches `close_round` without `post_round`, the sole caller of `observe_round`),
            # so replay had to state it and did not. It is also the one round that closes
            # TWICE, so folding it stepped the stall counter by two on every resume.
            if record.round == 0:
                return
            # `None` is the field's own third state: a round whose arms carried no interval banks
            # on `improved` alone, which is what it was decided on.
            sep = record.payload["separable"]
            self._bank_round(
                bool(record.payload["improved"]),
                lives,
                compared=int(record.payload["electable_count"]) > 0,
                separable=None if sep is None else bool(sep),
            )
        elif (escalation_state := adopted_fire_state(record)) is None:
            return
        elif record.phase == PotterPhase.REFINE_STRATEGY:
            self._l1_stall_count = 0
            self._l2_round = int(escalation_state["l2_round"])
            self._l2_stall_count = int(escalation_state["l2_stall_count"])
            self._l2_best_composite_fitness_at_entry = float(
                escalation_state["l2_best_composite_fitness_at_entry"]
            )
            l2_theta = escalation_state["l2_best_theta_at_entry"]
            self._l2_best_theta_at_entry = None if l2_theta is None else float(l2_theta)
        elif record.phase == PotterPhase.MODIFY_PLAN:
            # Both ``None`` where every L3 fire so far was a heal.
            l3_comp = escalation_state["l3_best_composite_fitness_at_entry"]
            l3_theta = escalation_state["l3_best_theta_at_entry"]
            self._l1_stall_count = 0
            self._l3_round = int(escalation_state["l3_round"])
            self._l3_stall_count = int(escalation_state["l3_stall_count"])
            self._l3_best_composite_fitness_at_entry = None if l3_comp is None else float(l3_comp)
            self._l3_best_theta_at_entry = None if l3_theta is None else float(l3_theta)
            # New plan invalidates L2's progress — wipe.
            self._l2_round = 0
            self._l2_stall_count = 0
            self._l2_best_composite_fitness_at_entry = None
            self._l2_best_theta_at_entry = None

    @classmethod
    def from_ledger(
        cls, ledger: CycleEventLog | None, *, lives: LivesConfig | None, before_round: int
    ) -> EscalationFSM:
        """Rebuild by folding the rounds that SURVIVE; ``None`` ⇒ fresh state. ``lives`` is REQUIRED —
        defaulting it rebuilt the accumulator empty, handing a cycle one stall from ``LIVES_EXHAUSTED``
        its bank back."""
        s = cls()
        for rec in surviving_phase_records(ledger, before_round=before_round):
            s.fold(rec, lives=lives)
        return s


def adopted_fire_state(record: PhaseRecord) -> dict[str, Any] | None:
    """What an L2 or L3 fire's exit banked; ``None`` for any other record, and for a fire that
    adopted nothing."""
    if record.event != "exit" or record.phase not in set(PotterPhase):
        return None
    state: dict[str, Any] | None = view_fields(record)["state"]
    return state


def surviving_phase_records(
    ledger: CycleEventLog | None, *, before_round: int
) -> list[PhaseRecord]:
    """The phase records a resume keeps, in ledger order. Two cuts: rounds at or past
    ``before_round``, and everything from a round's first close on once that round closes again."""
    live: list[PhaseRecord] = []
    if ledger is None:
        return live
    for _offset, rec in ledger.iter():
        if not isinstance(rec, PhaseRecord) or (rec.round or 0) >= before_round:
            continue
        if rec.phase == "round" and rec.event == "complete" and rec.round:
            reclosed = next(
                (
                    i
                    for i, kept in enumerate(live)
                    if kept.phase == "round"
                    and kept.event == "complete"
                    and (kept.round or 0) >= rec.round
                ),
                None,
            )
            if reclosed is not None:
                del live[reclosed:]
        live.append(rec)
    return live


__all__ = [
    "EscalationEvent",
    "EscalationFSM",
    "ExplorationBudget",
    "LadderAsk",
    "LayerReading",
    "NextAction",
    "PotterPhase",
    "adopted_fire_state",
    "exploration_budget",
    "l1_stall_depth",
    "round_advanced",
    "surviving_phase_records",
]
