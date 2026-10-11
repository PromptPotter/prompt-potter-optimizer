"""The bench reaches it only through ``nodes.WorkingState``; ``members.py::PotterRuntime`` mints it."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.earned_blocks import (
    answer_space_signature,
    earned_library_for,
)
from promptpotter.application.intelligence.indexes.axis import AxisIndex
from promptpotter.application.optimizers.potter.escalation.state import EscalationFSM
from promptpotter.application.optimizers.potter.records import (
    POTTER_MANIFEST,
    L2L3Memory,
    PotterRoundState,
)
from promptpotter.application.scoring.candidate_report import fatal_validation_failures
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.wounds import RuntimeFailure, rf_dedup_key
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.application.optimizers.potter.l1.generate import L1Generation
    from promptpotter.domain.optimizer_state import CritiqueReadout
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import CellSheet

logger = logging.getLogger(__name__)

__all__ = ["PotterState", "potter_state"]


@dataclass
class PotterState:
    """``memory`` and the ladder carry across every adoption; the rest is this process's."""

    session: Session
    memory: L2L3Memory = field(default_factory=L2L3Memory)
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    # Mined once at run init from runs with the SAME answer-space signature; never the static seed set.
    earned_blocks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    axis_index: AxisIndex | None = None
    # ``None`` until a round generates: the origin's round.
    in_flight: PotterRoundState | None = None

    def axes(self, sample_index: SampleIndex | None) -> AxisIndex | None:
        if sample_index is None:
            return None
        if self.axis_index is None:
            self.axis_index = AxisIndex(sample_index)
        self.axis_index.refresh()
        return self.axis_index

    @classmethod
    def start(
        cls, session: Session, config: CampaignConfig, origin_results: CellSheet
    ) -> PotterState:
        memory = _with_sibling_runtime_failures(L2L3Memory(), session)
        # A controlled arm reads none: the mine walks every campaign of the workspace.
        earned_blocks = (
            {}
            if session.controlled
            else earned_library_for(
                session.store,
                answer_space_signature(
                    (cell.facts.ground_truth for cell in origin_results),
                    dataset=config.dataset_name,
                ),
            )
        )
        return cls(session=session, memory=memory, earned_blocks=earned_blocks)

    def snapshot(
        self, sample_index: SampleIndex | None, generation: L1Generation
    ) -> PotterRoundState:
        """Called once every reject posture has read *generation*: the yield is counted here alone."""
        axes = self.axes(sample_index)
        rejected = Counter(
            fatal[0].reason
            for cp in generation.proposals
            if (fatal := fatal_validation_failures(cp.validation_failures))
        )
        proposed = len(generation.proposals)
        return PotterRoundState(
            memory=self.memory,
            ladder=self.escalation.ladder,
            l1_yield=(proposed - rejected.total()) / proposed if proposed else 1.0,
            l1_proposed=proposed,
            l1_rejected=dict(rejected),
            l1_parse_failure=generation.parse_failure,
            l1_citable=None if generation.citable is None else list(generation.citable),
            l1_exploration_budget=generation.exploration_budget,
            axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
        )

    def _standing(self, readouts: PotterRoundState | None) -> PotterRoundState:
        if readouts is None:
            return PotterRoundState(memory=self.memory, ladder=self.escalation.ladder)
        return readouts.model_copy(update={"memory": self.memory, "ladder": self.escalation.ladder})

    def round_payload(self) -> PotterRoundState:
        return self._standing(self.in_flight)

    def restated(self, closed: RoundResult) -> PotterRoundState:
        return self._standing(closed.optimizer_state.payload_as(PotterRoundState))

    def critiqued(self, critique: CritiqueReadout) -> None:
        self.in_flight = self.round_payload().model_copy(update={"critique": critique})

    def replay(self, last: RoundResult) -> None:
        """The ONE restore: the state *last* stands on, a fire after its close included."""
        self.in_flight = None
        banked = last.optimizer_state.payload_as(PotterRoundState)
        self.memory = banked.memory
        self.escalation = EscalationFSM(banked.ladder)

    def absorb(self, round_result: RoundResult) -> None:
        held = self.memory.wounds.runtime_failures
        seen = {rf_dedup_key(rf.model_dump()) for rf in held}
        fresh: list[RuntimeFailure] = []
        for cs in round_result.candidate_scores:
            for rf in cs.runtime_failures:
                key = rf_dedup_key(rf.model_dump())
                if key not in seen:
                    seen.add(key)
                    fresh.append(rf)
        if fresh:
            self.memory = self.memory.wounded(runtime_failures=[*held, *fresh])


def potter_state(state: WorkingState) -> PotterState:
    if not isinstance(state, PotterState):
        raise TypeError(f"a potter member was handed {type(state).__name__}, not potter's state")
    return state


def _sibling_runtime_failures(session: Session) -> list[RuntimeFailure]:
    """Only a sibling that reached a natural conclusion carries trustworthy failures."""
    campaigns = session.store.campaigns
    root = root_cycle_id(session.state.cycle_id)
    out: dict[tuple[str, str, str], RuntimeFailure] = {}
    cycles_dir = campaign_cycles_dir(campaigns.campaign_root_dir(session.campaign_id))
    for sibling in sorted(d.name for d in cycles_dir.iterdir() if d.is_dir()):
        if sibling == session.state.cycle_id or root_cycle_id(sibling) != root:
            continue
        hop = CycleHop(campaign_id=session.campaign_id, cycle_id=sibling)
        index = campaigns.load(hop)
        reason = None if index is None else index.stop_reason
        if reason is None or stop_reason_outcome(reason) is not StopOutcome.SUCCESS:
            continue
        rounds = campaigns.standing_rounds(hop).rounds
        state = rounds[max(rounds)].close.optimizer_state if rounds else None
        if state is None or state.manifest != POTTER_MANIFEST:
            continue
        for failure in state.payload_as(PotterRoundState).memory.wounds.runtime_failures:
            out.setdefault(rf_dedup_key(failure.model_dump(mode="json")), failure)
    return list(out.values())


def _with_sibling_runtime_failures(memory: L2L3Memory, session: Session) -> L2L3Memory:
    if not session.state.cycle_id:
        return memory
    try:
        failures = _sibling_runtime_failures(session)
    except Exception:
        # Logged, never swallowed: without these L1 re-proposes configs sibling forks proved to fail.
        logger.warning("sibling runtime_failures inheritance skipped", exc_info=True)
        return memory
    if not failures:
        return memory
    logger.info(
        "inherited %d runtime_failures from sibling forks of %s",
        len(failures),
        session.state.cycle_id,
    )
    return memory.wounded(runtime_failures=[*memory.wounds.runtime_failures, *failures])
