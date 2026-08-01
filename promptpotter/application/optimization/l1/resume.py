"""L1 candidate loading — fresh-generate or replay-from-disk."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    optimizer_model,
    optimizer_node_config,
)
from promptpotter.application.optimization.l1.generate import (
    candidate_summaries,
    l1_generate,
)
from promptpotter.application.optimization.validators.l1_strict import (
    L1YieldStats,
    detect_invariants,
)
from promptpotter.domain.phases import CampaignPhase, PhaseEvent, emit_phase
from promptpotter.domain.results import CandidateProposal, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, LLMCallRecord

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure.tracing.bridge import observed_node

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge

logger = logging.getLogger(__name__)


async def generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    n_scoring_samples: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> tuple[list[CandidateProposal], L1YieldStats]:
    """Load persisted candidates or generate fresh ones via LLM; detect no-op + duplicate variants."""
    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up the round budget.
    opt = config.optimization
    opt_params = cycle.opt_sp.memory.l1_overrides
    _n_variants = min(opt_params.get("n_variants", opt.n_variants), opt.n_variants * 3)
    _creativity = opt_params.get(
        "creativity", float(optimizer_node_config("l1_generate")["temperature"])
    )
    prompt_preview = cycle.opt_sp.render()[:120]

    assert cycle.tracking.current_sp is not None
    # The parent's RESOLVED, folded config — the baseline every candidate's param override is
    # a delta against (`detect_invariants`). Bound once: the narrowing does not survive the await.
    parent_pipeline_params = cycle.tracking.current_sp.pipeline_params
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=cycle.tracking.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=optimizer_model(),
        has_l1_critique=bool(cycle.rounds[-1].critique) if cycle.rounds else False,
        pipeline_params=parent_pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
        parent_task_context={
            k: v for k, v in cycle.opt_sp.memory.task_context.to_dict().items() if v
        },
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
            yield_stats = detect_invariants(
                persisted, cycle.opt_sp, parent_pipeline_params, cycle.rounds
            )
            # llm_call never fires on this branch — synthesize an
            # ``LLMCallRecord(payload_kind="synthesized")`` so the audit
            # trail + dashboard see the node, without lying about a real
            # LLM call having happened.
            if (_ledger := session.state.ledger) is not None:
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
                n_scoring_samples=n_scoring_samples,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted, round_num),
                l1_yield=yield_stats.l1_yield,
                l1_n_no_op=yield_stats.l1_n_no_op,
                l1_n_duplicate=yield_stats.l1_n_duplicate,
                l1_n_repeat=yield_stats.l1_n_repeat,
            )
            return persisted, yield_stats

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/optimizer",
        obs=obs,
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        candidates, parse_failure = await l1_generate(
            cycle,
            n_variants=_n_variants,
            creativity=_creativity,
            obs=obs,
            round_num=round_num,
        )

    # `detect_invariants` only sees proposals that exist, so a parse failure (zero candidates)
    # is invisible to it. Carry the reason onto the round's L1-quality record instead.
    yield_stats = replace(
        detect_invariants(candidates, cycle.opt_sp, parent_pipeline_params, cycle.rounds),
        l1_parse_failure=parse_failure,
    )

    # Persist a NON-EMPTY population only. The replay branch above tests `is not None`, and
    # `read_json_optional` hands back the parsed `[]` — which is not None — so an empty file
    # reads as a legitimate replay payload: the resumed round adopts zero candidates and
    # never calls the LLM again. A parse failure would therefore become permanent, replaying
    # identically on every resume, and the round's own healing (FIRE_L2 via
    # `l1_generate_unusable`) would re-steer a generation that no longer happens. Writing no
    # file is what makes the resume regenerate — which is the whole point of retrying a round
    # that produced nothing.
    if session.state.cycle_id and candidates:
        session.store.campaigns.save_round_candidates(
            session.campaign_id,
            session.state.cycle_id,
            round_num,
            [cp.model_dump() for cp in candidates],
        )
    # Identity, the moment it exists — a round that never closes still names its
    # candidates. The replay branch above skips this: its record is already on the ledger.
    if (ledger := session.state.ledger) is not None:
        for idx, cp in enumerate(candidates):
            ledger.append(
                CandidateMintedRecord(
                    round=round_num,
                    idx=idx,
                    candidate_id=cp.opt_sp.lineage.id,
                    parent_id=cp.opt_sp.lineage.parent_id,
                    label=candidate_label(round_num, idx),
                    changes_description=cp.opt_sp.lineage.changes_description,
                    source=cp.opt_sp.lineage.source,
                )
            )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_scoring_samples=n_scoring_samples,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates, round_num),
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
        l1_n_repeat=yield_stats.l1_n_repeat,
    )

    return candidates, yield_stats


__all__ = ["generate_or_load_candidates"]
