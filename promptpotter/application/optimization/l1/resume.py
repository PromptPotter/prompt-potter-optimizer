"""L1 candidate loading — fresh-generate or replay-from-disk."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.l1.generate import (
    candidate_summaries,
    l1_generate,
)
from promptpotter.application.optimization.validators.l1_strict import (
    L1YieldStats,
    detect_invariants,
)
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import CandidateProposal

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import observed_node

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)


async def generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> tuple[list[CandidateProposal], L1YieldStats]:
    """Load persisted candidates or generate fresh ones via LLM; detect no-op + duplicate variants."""
    from promptpotter.application.optimization.l1.generate import L1_CREATIVITY

    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up the round budget.
    opt = config.optimization
    model = config.optimizer_llm.model
    opt_params = cycle.opt_sp.l1_overrides
    _n_variants = min(opt_params.get("n_variants", opt.n_variants), opt.n_variants * 3)
    _creativity = opt_params.get("creativity", L1_CREATIVITY)
    prompt_preview = cycle.opt_sp.render()[:120]

    assert cycle.tracking.current_sp is not None
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=cycle.tracking.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=model or "(default)",
        has_l1_critique=bool(cycle.rounds[-1].critique) if cycle.rounds else False,
        pipeline_params=cycle.tracking.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
        parent_task_context={k: v for k, v in cycle.opt_sp.task_context.to_dict().items() if v},
    )

    if session.state.cycle_id:
        persisted_raw = session.store.campaigns.load_round_candidates(
            session.campaign_id,
            session.state.cycle_id,
            round_num,
        )
        if persisted_raw is not None:
            persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
            logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
            yield_stats = detect_invariants(persisted, cycle.opt_sp)
            # llm_call never fires on this branch — synthesize an
            # ``LLMCallRecord(payload_kind="synthesized")`` so the audit
            # trail + dashboard see the node, without lying about a real
            # LLM call having happened.
            if (_ledger := session.state.ledger) is not None:
                from promptpotter.domain.run_records import LLMCallRecord

                _ledger.append(
                    LLMCallRecord(
                        node="l1_generate",
                        round=round_num,
                        payload_kind="synthesized",
                        payload={
                            "type": "l1_generate",
                            "input": {"source": "loaded_from_disk", "round": round_num},
                            "response": {"candidates": candidate_summaries(persisted, round_num)},
                        },
                    )
                )
            emit_phase(
                on_phase,
                CampaignPhase.L1_GENERATE,
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted, round_num),
                l1_yield=yield_stats.l1_yield,
                l1_n_no_op=yield_stats.l1_n_no_op,
                l1_n_duplicate=yield_stats.l1_n_duplicate,
            )
            return persisted, yield_stats

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        candidates = await l1_generate(
            cycle,
            n_variants=_n_variants,
            creativity=_creativity,
            llm_client=client,
            model=model,
            obs=obs,
            round_num=round_num,
        )

    yield_stats = detect_invariants(candidates, cycle.opt_sp)

    if session.state.cycle_id:
        session.store.campaigns.save_round_candidates(
            session.campaign_id,
            session.state.cycle_id,
            round_num,
            [cp.model_dump() for cp in candidates],
        )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates, round_num),
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )

    return candidates, yield_stats


__all__ = ["generate_or_load_candidates"]
