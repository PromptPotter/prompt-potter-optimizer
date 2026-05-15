"""Resume entry point — rescore prior rounds, replay decisions, halt-or-fork.

:func:`resume_with_divergence_check` is the single entry point the
runner reaches for. It rescores prior rounds under the active scorer,
walks ``replay_decisions`` looking for the first divergence, and
either:

* halts with :class:`ResumeDivergenceError` when ``fork_on_divergence``
  is False, or
* mints a sibling via :func:`_mint_fork` with
  ``ForkTrigger.SCORING_DIVERGENCE``, retargets the active pointer,
  and returns the new cycle's id and resume offset.

Behaviour-equivalent to the prior cycle.py implementation. The
escalation FSM is rebuilt via ``EscalationState.from_ledger`` because
the ledger is the SoT for layer-stall counters across resume.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.escalation.state import EscalationState
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
)
from promptpotter.application.optimization.resume_and_fork.replayers import replay_decisions
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.run_records import ForkPayload, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["resume_with_divergence_check"]


def resume_with_divergence_check(
    campaign_store: CampaignStore,
    backend_id: str,
    cycle_id: str,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    """Rescore prior rounds under the active scorer; halt or fork on divergence."""
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(backend_id, cycle_id, 0, resumed_from_round - 1)

    def _rescore(items: Any) -> list:
        out = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.get("results"))
        for items in (t.get("all_candidate_results") or {}).values():
            _rescore(items)

    origin_results_rescored = _rescore(cycle.tracking.current_results)

    if not skip_divergence_check:
        for i, t in enumerate(prior):
            div = replay_decisions(
                t,
                prior_rounds=prior[:i],
                origin_results=origin_results_rescored,
            )
            if div is None:
                continue
            if fork_on_divergence:
                survivors = list(prior[:i])
                new_cycle_id = _mint_fork(
                    campaign_store,
                    session.store.tenant_id,
                    session.session_id,
                    cycle_id,
                    div.round_num,
                    ForkPayload(
                        trigger=ForkTrigger.SCORING_DIVERGENCE,
                        reason=f"scorer_mismatch:{div.kind}",
                        issued_by="system",
                    ),
                    surviving_rounds=survivors,
                )
                cycle.replay_priors(survivors)
                cycle.escalation = EscalationState.from_ledger(session.state.ledger)
                logger.warning(
                    "Resume diverged at round %d (%s); forked → %s",
                    div.round_num,
                    div.kind,
                    new_cycle_id,
                )
                return ForkResult(
                    new_cycle_id=new_cycle_id,
                    new_resumed_from_round=div.round_num,
                )
            raise ResumeDivergenceError(
                round_num=div.round_num,
                kind=div.kind,
                recorded_outcome=div.recorded_outcome,
                current_outcome=div.current_outcome,
                diagnostics={
                    "scorer_id": sc.scorer_id,
                    "fork_hint": (
                        "rerun `optimize --fork-on-divergence` to branch a new "
                        "cycle here under the current scorer"
                    ),
                },
            )

    cycle.replay_priors(prior)
    cycle.escalation = EscalationState.from_ledger(session.state.ledger)
    return None
