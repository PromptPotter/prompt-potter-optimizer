from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.config.settings import DATASET_NAME
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint
from promptpotter.domain.pipeline_overlay import overlay_is_locked_axis_only
from promptpotter.domain.results import RoundParent, ScoredCandidate, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, CycleSeed
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignOrigin",
    "establish_campaign_origin",
    "prepare_scoring_context",
    "rescore_parent",
    "resolve_origin_opt_search_point",
    "try_inherit_fork_origin",
]


async def rescore_parent(
    cycle: Cycle,
    scoring_set: list[Sample],
    round_num: int,
    *,
    callbacks: RunCallbacks,
    force_fresh: bool = False,
) -> RoundParent:
    """Score the round's parent on THIS round's ``scoring_set``, so election compares on the SAME
    samples. Without it ``matched_parent_stats`` intersects disjoint sets and returns a fake floor."""
    from promptpotter.application.optimization.l1.population import build_score_report
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    session = cycle.session
    tr = cycle.tracking
    assert tr.current_sp is not None
    results, scores, _signal = await score_search_point(
        tr.current_sp,
        scoring_set,
        session,
        label="round_parent",
        # One half of a PAIRED diff: both sides must sit on the same vacuous fallback or the
        # delta reads a prompt-length difference as a behaviour difference. `l1_diversity` is
        # withheld for the same reason — it keeps its 1.0 vacuous default.
        opt_sp=None,
        degradation_checks=None,
        n_total_candidates=0,
        axes=cycle.axes,
        on_sample_scored=None,
        on_sample_starting=None,
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=cycle.opt_sp.lineage.id,
            label=cycle.rounds[-1].label,
            role=MeasurementRole.PARENT,
        ),
        force_fresh=force_fresh,
    )
    return RoundParent(
        opt_sp=cycle.opt_sp,
        results=results,
        # The gateway's OWN answer — never re-run `compute_composite_fitness` over the same
        # rows, which drops the evaluator namespace on the way.
        report=build_score_report(
            cycle.opt_sp,
            None,
            scores,
            results,
            scoring_set,
            # The parent INDIVIDUAL's label (``C0``, ``C3.1``, …), off the round it won — it
            # reaches disk, so a synthesized round name here would name no candidate.
            label=cycle.rounds[-1].label,
            sp_hash=tr.current_sp.sp_hash(session.pipeline_schema),
        ),
    )


class CampaignOrigin(NamedTuple):
    """The scored origin. ``report`` is C0's measurement in the one shape every individual's takes —
    the same object deposited on the ledger, so round 0's row cannot be a second computation."""

    resolved_origin: OptSearchPoint | None
    report: ScoredCandidate
    origin_results: list[Any] | None


def try_inherit_fork_origin(
    session: Session,
    seed: CycleSeed | None,
    *,
    resolved_origin: OptSearchPoint,
) -> CampaignOrigin | None:
    """Inherit an operator fork's C0 from its branch-point candidate — a no-edit fork or a
    model/provider-only steer. Re-scoring would re-roll a different number and the lineage would jump."""
    if seed is None:
        return None
    # Overlay gate — `render()` below compares the PROMPT and says nothing about the pipeline
    # the fork RUNS under. A non-locked param edit (temperature, effort, …) changes what the
    # origin measures → re-score. A model/provider-only steer is the sanctioned babysit path:
    # C0 is inherited exact and grade C carries the "measured off the origin's model" caveat.
    if seed.pipeline_overlay and not overlay_is_locked_axis_only(seed.pipeline_overlay):
        return None

    store = session.store.campaigns
    index = store.load(session.hop)
    if not isinstance(index, dict):
        return None
    fork = index.get("fork")
    parent = index.get("parent_cycle_id")
    if not isinstance(fork, dict) or not isinstance(parent, str) or not parent:
        return None
    from_round = fork.get("from_round")
    from_candidate_id = fork.get("from_candidate_id")
    if not isinstance(from_round, int) or not isinstance(from_candidate_id, str):
        return None

    parent_round = store.load_round_file(
        CycleHop(campaign_id=session.campaign_id, cycle_id=parent), from_round
    )
    if parent_round is None:
        return None
    cand = next(
        (c for c in parent_round.candidate_scores if c.candidate_id == from_candidate_id),
        None,
    )
    if cand is None:
        return None

    # Identity gate: an operator edit changes the render → re-score.
    if resolved_origin.render() != OptSearchPoint.from_prompt_fields(cand.prompt_fields).render():
        return None

    origin_acc = cand.accuracy
    # The branch-point candidate's recorded per-sample rows, which is what makes the inherited
    # C0 a faithful copy rather than an empty shell: the strict origin gate assesses round 0 on
    # real samples, and round-1 hard-sample seeding inherits the origin's per-sample δ evidence.
    inherited_results = list(parent_round.all_candidate_results.get(from_candidate_id) or [])
    if not inherited_results:
        inherited_results = list(parent_round.results)
    logger.info(
        "Fork %s: inheriting C0 from branch-point candidate %s (parent %s round %d) "
        "acc=%.4f, %d per-sample rows — skipping origin re-score, straight to L1",
        session.state.cycle_id,
        from_candidate_id,
        parent,
        from_round,
        origin_acc,
        len(inherited_results),
    )
    # The OSP object, not a prompt-field dict, so the inherited C0 keeps its
    # lineage(source=seed.origin_source) — same shape the re-score path produces.
    return CampaignOrigin(
        resolved_origin=resolved_origin,
        # The branch-point candidate's whole report, re-identified onto this fork's C0:
        # composite, evaluators, counts and whisker are the parent's measurement or they
        # are a new one.
        report=cand.model_copy(
            update={"candidate_id": resolved_origin.lineage.id, "label": candidate_label(0, 0)}
        ),
        origin_results=inherited_results,
    )


# The seed declares its own provenance; the resolver stamps it.
_SEED_ORIGIN_LINEAGE = {
    "fork_seed": "Operator-steered fork — edited searchpoint as origin",
    "campaign_origin": "Fresh campaign minted from a chosen prior origin",
}


def resolve_origin_opt_search_point(
    prompt_node_names: list[str] | None = None,
    dataset_dir: Path | None = None,
    *,
    task_context: TaskDecomposition,
    seed: CycleSeed | None = None,
) -> OptSearchPoint:
    """Resolve the origin OptSearchPoint by precedence: a seed with non-empty prompt fields wins
    outright and short-circuits the dataset lookup, then ``{dataset_dir}/prompts``, then empty."""
    names = prompt_node_names or []
    origin: OptSearchPoint | None = None

    if seed is not None and seed.origin_prompt_fields:
        origin = OptSearchPoint.from_prompt_fields(
            seed.origin_prompt_fields,
            lineage=IndividualLineage(
                changes_description=_SEED_ORIGIN_LINEAGE[seed.origin_source],
                source=seed.origin_source,
            ),
        )
    elif dataset_dir is not None and names:
        from promptpotter.application.datasets.prompts import has_dataset_prompts, load_node_prompt

        if has_dataset_prompts(dataset_dir):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_dir, node_name, "default")
                except FileNotFoundError:
                    continue
                origin = OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    lineage=IndividualLineage(
                        changes_description=(f"Origin from {dataset_dir}/prompts/ ({node_name})"),
                        source="origin",
                    ),
                )
                break

    if origin is None:
        origin = OptSearchPoint(
            lineage=IndividualLineage(
                changes_description="Origin (no prompt node active — param-only optimization)",
                source="origin",
            ),
        )

    # The framing RENDERS — `_field_value` splices up/downstream around `problem_description` —
    # and `build_origin_cycle_id` hashes exactly that render, so an origin resolved without it
    # is a different prompt from the one the run scores. Stamped here on every branch, never by
    # the callers: each attached its own and identity read a prompt the run never measured.
    origin.memory.task_context = task_context
    return origin


async def establish_campaign_origin(
    session: Session,
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    seed: CycleSeed | None,
    listener: RunCallbacks | None,
) -> CampaignOrigin:
    """The single origin-establishment seam — the OSP is resolved exactly once and shared by both
    branches, which return the same :class:`CampaignOrigin` shape."""
    from promptpotter.application.optimization.task_context import committed_task_context

    resolved_origin = resolve_origin_opt_search_point(
        prompt_node_names=session.pipeline_schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
        # The SAME read identity uses. Handed the framing instead, this seam could be given a
        # value the cycle id never saw.
        task_context=committed_task_context(session.store, session.dataset_name),
        seed=seed,
    )
    inherited = try_inherit_fork_origin(session, seed, resolved_origin=resolved_origin)
    if inherited is not None:
        return inherited

    origin, _ = await prepare_scoring_context(
        dataset,
        campaign_config,
        pipeline_params=session.pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=listener,
        seed=seed,
        resolved_origin=resolved_origin,
    )
    return origin


async def prepare_scoring_context(
    train_data: list[Sample] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    pipeline_params: dict[str, Any] | None = None,
    pipeline_schema: PipelineSchema,
    svc: Session | None = None,
    listener: RunCallbacks | None = None,
    obs: ObservabilityBridge | None = None,
    seed: CycleSeed | None = None,
    resolved_origin: OptSearchPoint | None = None,
) -> tuple[CampaignOrigin, list[Sample]]:
    """*resolved_origin* lets the caller pass an already-resolved origin OSP (so it isn't
    resolved twice on the runner path); when ``None`` it's resolved here (the notebook path)."""
    from promptpotter.application.datasets.loaders import sample_dataset

    if resolved_origin is None:
        from promptpotter.application.optimization.task_context import committed_task_context

        prompt_nodes = pipeline_schema.prompt_node_names()
        # Resolving one HERE means reading the committed framing off the store, so this branch
        # requires the session — a requirement it previously stated only by ``getattr``-ing the
        # object defensively on one line and dereferencing it bare on the next.
        if svc is None:
            raise ValueError(
                "prepare_scoring_context needs `svc` (the Session) to resolve an origin; "
                "pass an already-resolved `resolved_origin=` when there is no session."
            )
        resolved_origin = resolve_origin_opt_search_point(
            prompt_node_names=prompt_nodes,
            dataset_dir=svc.dataset_config_dir,
            task_context=committed_task_context(svc.store, svc.dataset_name),
            seed=seed,
        )
    dataset = train_data or []

    # Score the origin whenever there is a live run to score it in (session + config +
    # dataset). Never sniff the searchpoint shape to guess "is there a program here?" — the
    # L4 outer origin's prose and node configs are both empty because its program IS the inner
    # recursion, and the skip that guess produced is indistinguishable downstream from a crash.
    # An unscoreable origin is caught LOUD by the round-0 origin gate, never hidden here; the
    # remaining guard is the no-session notebook/test path, which has nothing to score.
    from promptpotter.application.optimization.l1.population import (
        INVALID_SCORES,
        build_score_report,
    )

    if not (campaign_config is not None and svc is not None and dataset):
        # The resolved origin still travels — dropping it hands back a blank
        # OptSearchPoint(instruction="").
        return (
            CampaignOrigin(
                resolved_origin=resolved_origin,
                # Unmeasured reports as unmeasured in the shape a measured one uses: `total=0`
                # is the no-evidence marker, where a bare 0.0 reads as a real floor of zero.
                report=build_score_report(
                    resolved_origin,
                    None,
                    INVALID_SCORES,
                    [],
                    [],
                    label=candidate_label(0, 0),
                    # No session ⇒ no schema to hash under, and no rows for an id to address.
                    sp_hash="",
                ),
                origin_results=None,
            ),
            dataset,
        )

    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.phases import CampaignPhase, emit_phase
    from promptpotter.shared.errors import graceful

    session: Session = svc
    scoring_set = sample_dataset(dataset, campaign_config.origin_budget())
    spec = split_scoring_block(campaign_config.scoring)

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    elif session.backend_client.execution == "remote_http":
        # Only a wired backend can be broken by an empty term index; an `in_process` one has no
        # `/matches` to fail.
        logger.warning("No session terms available — /matches calls will fail.")

    if obs:
        with graceful("Dataset registration in origin scoring failed"):
            obs.register_dataset(DATASET_NAME, scoring_set)

    sp = resolved_origin.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )
    # populate_session_scoring overwrites scoring/source; loop repopulates before round 1.
    prior_schema = session.pipeline_schema
    session.pipeline_schema = pipeline_schema
    populate_session_scoring(
        session,
        obs=obs,
        scoring_formula=spec.per_sample,
        scoring_cell_formula=spec.per_cell,
        scorer_id=spec.scorer_id,
        headline_metric=campaign_config.headline_metric,
        judge_specs=campaign_config.judges,
        source="origin",
    )

    # ci=0/ct=1 ⇒ dashboard ticks per-sample during origin like L1.
    if listener is not None:
        emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "enter", round=0)

    # C0 is a minted candidate like any other — named on the ledger before it is measured.
    if (ledger := session.state.ledger) is not None:
        ledger.append(
            CandidateMintedRecord(
                round=0,
                idx=0,
                candidate_id=resolved_origin.lineage.id,
                parent_id=resolved_origin.lineage.parent_id,
                label=candidate_label(0, 0),
                changes_description=resolved_origin.lineage.changes_description,
                source=resolved_origin.lineage.source or "origin",
            )
        )

    from promptpotter.application.optimization.l1.score.signal_effect import (
        is_transient_scoring_abort,
    )

    try:
        # The origin is the campaign's whole reference, so a transient-transport abort must not
        # bank a corrupted floor — re-score once fresh. A config-deterministic abort is NOT
        # retried; it is a real fault the operator must fix.
        for attempt in range(2):
            origin_results, scores, signal = await score_search_point(
                sp,
                scoring_set,
                session,
                label="Origin",
                # The reference every later delta is taken against, so it sits on the same
                # vacuous fallback as the matched floor above — an opt_sp-aware composite here
                # is one no candidate's matched floor shares.
                opt_sp=None,
                measured=None,
                force_fresh=attempt > 0,
                on_sample_starting=(
                    partial(listener.on_sample_started, 0, 1) if listener is not None else None
                ),
                on_sample_scored=(
                    partial(listener.on_sample_scored, 0, 1) if listener is not None else None
                ),
            )
            if not is_transient_scoring_abort(signal):
                break
            logger.warning(
                "Origin scoring hit a transient transport abort — re-scoring once fresh."
            )
        # Candidate 0 of round 0, measured ONCE. This object is both what the ledger receives
        # and what round 0's row is built from — never re-derive either from these rows.
        report = build_score_report(
            resolved_origin,
            None,
            scores,
            origin_results,
            scoring_set,
            label=candidate_label(0, 0),
            sp_hash=sp.sp_hash(pipeline_schema),
            resolved_pipeline_params=sp.config_params,
        )
        if listener is not None:
            listener.on_candidate_scored(0, 1, report.model_dump())
    finally:
        if listener is not None:
            emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "exit", round=0)
        session.pipeline_schema = prior_schema

    return (
        CampaignOrigin(
            resolved_origin=resolved_origin,
            report=report,
            origin_results=origin_results,
        ),
        dataset,
    )
