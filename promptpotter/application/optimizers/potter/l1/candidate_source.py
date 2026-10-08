from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizer_manifest import bound_inner_optimizer
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.application.optimizers.potter.l1.critique import ensure_prior_critique
from promptpotter.application.optimizers.potter.l1.generate import L1Generation, l1_generate
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.state import L1Population
from promptpotter.application.optimizers.potter.validators.l1_invariants import detect_invariants
from promptpotter.application.runner.round import proposal_summaries
from promptpotter.domain.results import CandidateProposal, round_document_digest
from promptpotter.domain.run_records import LLMCallRecord

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure.tracing.bridge import observed_node

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.potter.state import PotterState

logger = logging.getLogger(__name__)


def variants_this_round(cycle: Cycle, state: PotterState) -> int:
    """L2's `n_variants` override, capped at 3× the knob so L2 cannot blow up the round budget."""
    n_variants = potter_knobs(cycle.optimizer).l1_generate.n_variants
    return min(int(state.memory.l1_overrides.get("n_variants", n_variants)), n_variants * 3)


def replayed_candidates(cycle: Cycle, round_num: int) -> tuple[list[Any], str] | None:
    """The round's persisted generation, which a resume replays rather than calling L1 again."""
    session = cycle.session
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.load_round_candidates(session.hop, round_num)


async def propose_l1_population(round_num: int, cycle: Cycle, state: PotterState) -> L1Population:
    """The round's L1 population, every reject posture applied, and the state it generated under
    — whether the round goes on to measure it or, under ``--diag``, only shows it."""
    schema = cycle.session.pipeline_schema
    assert schema is not None and cycle.tracking.current_sp is not None
    generation = await _generate_or_load(round_num, cycle, state)
    knobs = potter_knobs(cycle.optimizer).l1_generate
    individuals, params = parse_population(
        generation.proposals,
        cycle.opt_sp,
        cycle.tracking.current_sp.pipeline_params,
        schema,
        runtime_failures=state.memory.wounds.runtime_failures,
        demo_ids=frozenset(s.id for s in cycle.session.scoring.require_partition().demo),
        shot_k_max=knobs.k_max,
        inner_optimizer=bound_inner_optimizer(),
        prompt_block_catalogue=knobs.prompt_block_catalogue,
    )
    return L1Population(
        proposals=generation.proposals,
        individuals=individuals,
        pipeline_params=params,
        payload=state.snapshot(cycle, generation),
    )


async def _generate_or_load(round_num: int, cycle: Cycle, state: PotterState) -> L1Generation:
    session = cycle.session
    opt_params = state.memory.l1_overrides
    _n_variants = variants_this_round(cycle, state)
    _creativity = opt_params.get(
        "creativity", float(cycle.optimizer.node_config("l1_generate")["temperature"])
    )

    assert cycle.tracking.current_sp is not None
    # The parent's RESOLVED, folded config — the baseline every candidate's param override is
    # a delta against (`detect_invariants`). Bound once: the narrowing does not survive the await.
    parent_pipeline_params = cycle.tracking.current_sp.pipeline_params

    # A round that will replay its candidates makes no LLM call, so it is the one round not worth
    # re-sending a missing critique for.
    cached = replayed_candidates(cycle, round_num)
    if cached is None:
        await ensure_prior_critique(cycle, state)

    if cached is not None:
        persisted_raw, _consumed = cached
        persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
        logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
        detect_invariants(persisted, cycle.opt_sp, parent_pipeline_params, cycle.rounds)
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
                        "response": {"candidates": proposal_summaries(persisted, round_num)},
                    },
                )
            )
        return L1Generation(persisted)

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm",
        obs=session.state.obs,
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        generation = await l1_generate(
            cycle,
            state,
            n_variants=_n_variants,
            creativity=_creativity,
            round_num=round_num,
        )
    candidates = generation.proposals
    detect_invariants(candidates, cycle.opt_sp, parent_pipeline_params, cycle.rounds)

    # Persist a NON-EMPTY population only. The replay branch above tests `is not None`, so a
    # file holding an empty population reads as a legitimate replay payload: the resumed round
    # adopts zero candidates and never calls the LLM again. A parse failure would therefore become permanent, replaying
    # identically on every resume, and the round's own healing (FIRE_L2 via
    # `l1_generate_unusable`) would re-steer a generation that no longer happens. Writing no
    # file is what makes the resume regenerate — which is the whole point of retrying a round
    # that produced nothing.
    if session.state.cycle_id and candidates:
        session.store.campaigns.save_round_candidates(
            session.hop,
            round_num,
            [cp.model_dump() for cp in candidates],
            # What this generation READ: the round it was composed from. Recorded beside the
            # candidates so a later resume can ask whether that round still says the same
            # thing before replaying them — a repaired round, or one whose critique was
            # re-distilled, voids the generation that read the old one.
            consumed=round_document_digest(cycle.rounds[-1]) if cycle.rounds else "",
        )
    return generation


__all__ = ["propose_l1_population", "replayed_candidates", "variants_this_round"]
