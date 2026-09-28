"""Potter's working state between rounds: the memory its escalation layers author, the stall
ladder's counters, and the block library mined at run init. The bench reaches it only through
``nodes.WorkingState``; ``members.py::PotterRuntime`` mints it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.earned_blocks import (
    answer_space_signature,
    earned_library_for,
)
from promptpotter.application.intelligence.sibling_wounds import gather_sibling_runtime_failures
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    compute_optimizer_prompt_hashes,
)
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.domain.optimizer_state import (
    POTTER_MANIFEST,
    L2L3Memory,
    OptimizerState,
    PotterRoundState,
    potter_round_state,
)
from promptpotter.domain.wounds import rf_dedup_key
from promptpotter.infrastructure.store.layout import root_cycle_id

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["PotterState", "potter_state"]


@dataclass
class PotterState:
    """``memory`` carries across every adoption and is snapshotted onto each round; the FSM's
    counters are rebuilt from the ledger on resume, since no round document banks them."""

    memory: L2L3Memory = field(default_factory=L2L3Memory)
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    # Reusable field values that earned credible lift on a run with the SAME answer-space
    # signature, mined once at run init (the walk is cross-campaign). Never the static seed set.
    earned_blocks: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def start(
        cls, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> PotterState:
        memory = L2L3Memory()
        _inherit_sibling_runtime_failures(memory, session)
        # Silent when no block earned credible lift on a matching shape — the dispatch-first
        # "signal or silence" rule.
        earned_blocks = earned_library_for(
            session.store,
            answer_space_signature(
                (r.get("ground_truth") for r in origin_results), dataset=config.dataset_name
            ),
        )
        return cls(memory=memory, earned_blocks=earned_blocks)

    def snapshot(
        self,
        *,
        l1_yield: float,
        l1_parse_failure: str | None,
        prompt_hashes: dict[str, str],
    ) -> OptimizerState:
        """What a round document banks — a copy, so a later fire cannot rewrite a closed round."""
        return OptimizerState(
            manifest=POTTER_MANIFEST,
            prompt_hashes=prompt_hashes,
            payload=PotterRoundState(
                memory=self.memory.model_copy(deep=True),
                l1_yield=l1_yield,
                l1_parse_failure=l1_parse_failure,
            ),
        )

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        return self.snapshot(
            l1_yield=1.0,
            l1_parse_failure=None,
            prompt_hashes=compute_optimizer_prompt_hashes(selected),
        )

    def replay(self, last: RoundResult) -> None:
        self.memory = potter_round_state(last.optimizer_state).memory.model_copy(deep=True)

    def resume(self, ledger: CycleEventLog | None, selected: SelectedOptimizer) -> None:
        self.escalation = EscalationFSM.from_ledger(
            ledger, lives=potter_knobs(selected).escalation.lives
        )

    def absorb(self, round_result: RoundResult) -> None:
        failures = self.memory.wounds.runtime_failures
        seen = {rf_dedup_key(rf.model_dump()) for rf in failures}
        for cs in round_result.candidate_scores:
            for rf in cs.runtime_failures:
                key = rf_dedup_key(rf.model_dump())
                if key not in seen:
                    seen.add(key)
                    failures.append(rf)
        potter_round_state(round_result.optimizer_state).memory = self.memory.model_copy(deep=True)

    def standing(self) -> tuple[int, int | None]:
        return self.escalation.l1_stall_count, self.escalation.lives


def potter_state(state: WorkingState) -> PotterState:
    if not isinstance(state, PotterState):
        raise TypeError(f"a potter member was handed {type(state).__name__}, not potter's state")
    return state


def _inherit_sibling_runtime_failures(memory: L2L3Memory, session: Session) -> None:
    """Pull RuntimeFailures from sibling forks of this cycle's root so L1 sees configs
    prior siblings already proved to fail (``wounds.py::_runtime_block`` filters by pipeline
    match, under the ``l1_wounds`` signal)."""

    if not session.state.cycle_id:
        return
    try:
        failures = gather_sibling_runtime_failures(
            session.store,
            session.campaign_id,
            root_cycle_id(session.state.cycle_id),
            session.backend_id,
            exclude_cycle_id=session.state.cycle_id,
        )
    except Exception:
        # Surface, don't swallow: on failure L1 never sees configs sibling forks already proved
        # to fail, which is an optimization-quality regression rather than noise.
        logger.warning("sibling runtime_failures inheritance skipped", exc_info=True)
        return
    if failures:
        memory.wounds.runtime_failures.extend(failures)
        logger.info(
            "inherited %d runtime_failures from sibling forks of %s",
            len(failures),
            session.state.cycle_id,
        )
