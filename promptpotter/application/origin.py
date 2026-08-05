"""Campaign data loading and origin scoring."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.config import CampaignConfig
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.config.settings import DATASET_NAME
from promptpotter.domain.opt_search_point import (
    IndividualLineage,
    OptSearchPoint,
    overlay_is_locked_axis_only,
)
from promptpotter.domain.results import RoundParent, ScoredCandidate, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, CycleSeed
from promptpotter.domain.sample import Sample
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignOrigin",
    "build_campaign_emitter",
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
    """Score the round's parent — the origin at round 0, the prior winner after — on THIS
    round's ``scoring_set``, so winner election compares candidate-vs-parent on the SAME
    hard-first samples the candidates ran.

    Elimination truncates each candidate at a different depth of the shared round order, so
    each is scored on a different prefix, while the
    parent was only ever scored on its own earlier rounds; without this re-score,
    ``matched_origin_stats`` intersects disjoint sample sets and returns a fake ``0.0``
    floor. Re-scoring through the ``score_search_point`` gateway + content-hash cache
    yields a real same-subset floor, and samples the parent already measured replay from
    cache — so the cost is one measurement per *new* hard sample it hasn't seen.

    ``force_fresh`` bypasses the measurement cache (see ``score_search_point``). The
    winner-election path leaves it ``False``. The origin gate sets it ``True`` so a re-score
    after a backend-code fix reflects the fix instead of replaying the stale origin.

    This re-scores a PRIOR searchpoint, not a foreground candidate, so it fires **no**
    per-sample display callbacks — wiring them would mint a bogus ``C{round}.0`` row. Its
    spend rides the token ledger; ``degradation_checks=None`` blocks the floor from
    aborting itself.
    """
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
        # The parent is one half of a PAIRED diff; the candidates it is differenced against
        # are scored with their own. Both sides must sit on the same vacuous fallback or the
        # delta reads a prompt-length difference as a behaviour difference. `l1_diversity`
        # is not passed for that same reason — it keeps the 1.0 vacuous default.
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
        # The gateway's OWN answer, not a second computation of it. This used to discard
        # `scores` and re-run `compute_composite_fitness` over the same rows with the same
        # scorer — a second code path producing what the single scoring gateway had just
        # returned, and one that dropped the evaluator namespace on the way (leaving
        # `RoundParent.evaluators` a declared field with no writer).
        report=build_score_report(
            cycle.opt_sp,
            None,
            scores,
            results,
            scoring_set,
            # The parent INDIVIDUAL's label (``C0``, ``C3.1``, …), read off the round it won.
            # It reaches disk — a held round persists the parent's label as its own — so a
            # synthesized round name here ("origin", "round_2") names no candidate.
            label=cycle.rounds[-1].label,
        ),
    )


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    origin_accuracy: float,
    resumed_from_round: int | None = None,
    recorder: Any | None = None,
    seed_from_cycle_id: str | None = None,
    langfuse_trace_url: str | None = None,
) -> Any:
    """Live dashboard projection from session + config (shared by CLI + runner).

    ``seed_from_cycle_id`` (set when building a fork's dashboard) names the
    parent cycle to seed prior trajectory from; ``None`` seeds from the cycle's
    own dir. ``langfuse_trace_url`` is the set-once operator deep link.
    """
    from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView

    opt = campaign_config.optimization
    return LiveDashboardView.for_session(
        session.state.cycle_id,
        tenant_root=session.tenant_root,
        session_id=session.session_id,
        campaign_id=session.campaign_id,
        l1_patience=opt.l1_patience,
        n_variants=opt.n_variants,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        headline_metric=campaign_config.headline_metric,
        langfuse_trace_url=langfuse_trace_url,
        resumed_from_round=resumed_from_round,
        recorder=recorder,
        seed_from_cycle_id=seed_from_cycle_id,
    )


class CampaignOrigin(NamedTuple):
    """The scored origin. ``resolved_origin`` is the origin OptSearchPoint —
    carrying its full lineage/memory, not just prompt strings — so the C0
    individual keeps its ``source`` marker (e.g. ``fork_seed``) downstream.

    ``report`` is C0's measurement in the one shape every individual's measurement takes —
    the same object deposited on the ledger, so round 0's row and the ledger's cannot be two
    computations of one thing. A nothing-to-score origin still gets one, carrying the
    ``total=0`` no-evidence marker the round-0 gate is there to catch.
    """

    resolved_origin: OptSearchPoint | None
    report: ScoredCandidate
    origin_results: list[Any] | None


def try_inherit_fork_origin(
    session: Session,
    seed: CycleSeed | None,
    *,
    resolved_origin: OptSearchPoint,
) -> CampaignOrigin | None:
    """Inherit an operator fork's C0 from its branch-point candidate — for a no-edit
    fork OR a model/provider-only steer.

    When an operator forks from a searchpoint whose PROMPT it does not edit, that
    searchpoint *is* the fork's origin — its accuracy was already measured in the parent
    round. Re-scoring would re-roll a different number under a nondeterministic backend
    (the same prompt + samples does NOT reproduce the same accuracy), so C0 would no
    longer equal the branch point and the lineage would jump. Instead we inherit the
    recorded measurement and skip the origin scoring pass — straight to L1_generate.

    A model/provider-only steer inherits too (the sanctioned babysit path): the origin
    is unchanged in every respect but the steered axis, so its done C0 is inherited exact
    and only the candidate is measured under the steered model. This is what lets the
    param-only pp-self origin be steered without re-paying C0.

    *resolved_origin* is the already-resolved fork origin (resolved once by
    ``establish_campaign_origin``). Returns the inherited :class:`CampaignOrigin` only
    when this is an operator-steered fork whose origin renders identically to the
    ``from_candidate_id`` candidate in the parent's recorded round, under an overlay that
    is empty or locked-axis-only. Any miss (non-fork, missing coords, edited prompt →
    different render, non-locked param edit) returns ``None`` and the caller re-scores.
    """
    if seed is None:
        return None
    # Overlay gate. `render()` below compares the PROMPT; it says nothing about the
    # pipeline. A fork carries a `pipeline_overlay` (the node-config editor beside Steer
    # & fork) that `runner/entry.py` layers on top of the dataset config, so the fork RUNS
    # under different params. A NON-locked param edit (temperature, effort, …) genuinely
    # changes what the origin measures → re-score (return None). But a **model/provider-only**
    # steer is the sanctioned babysit path: the origin is unchanged in every other respect,
    # so the done C0 is inherited exact and only the candidate is measured under the steered
    # model — grade C carries the "measured off the origin's model" caveat. An empty overlay
    # (a no-edit fork) also inherits. This is why the param-only pp-self origin can be steered
    # without re-paying its C0.
    if seed.pipeline_overlay and not overlay_is_locked_axis_only(seed.pipeline_overlay):
        return None

    store = session.store.campaigns
    index = store.load(session.campaign_id, session.state.cycle_id)
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

    parent_round = store.load_round_file(session.campaign_id, parent, from_round)
    if parent_round is None:
        return None
    cand = next(
        (c for c in parent_round.candidate_scores if c.candidate_id == from_candidate_id),
        None,
    )
    if cand is None:
        return None

    # Identity gate: the fork origin's prompt must render identically to the
    # branch-point candidate's. An operator edit changes the render → re-score.
    if resolved_origin.render() != OptSearchPoint.from_prompt_fields(cand.prompt_fields).render():
        return None

    origin_acc = cand.accuracy
    # Carry the branch-point candidate's per-sample rows (they ARE in the round file —
    # `all_candidate_results[candidate_id]`, the origin's own being round 0's `results`).
    # This is what makes the inherited C0 a FAITHFUL copy of the validated origin, not an
    # empty shell: round 0's health is assessed on real samples (so the strict origin gate
    # doesn't misfire "zero samples → critical" on an origin the parent already measured),
    # and round-1 hard-sample seeding inherits the origin's per-sample δ evidence. No
    # re-measurement — these are the recorded parent rows, exactly the inherited origin measurement.
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
    # resolved_origin carries the OSP object (not a prompt-field dict) so the inherited C0 keeps
    # its lineage(source=seed.origin_source) — same shape the re-score path produces.
    return CampaignOrigin(
        resolved_origin=resolved_origin,
        # The inherited C0's measurement IS the branch-point candidate's report, re-identified
        # onto this fork's own C0. Carrying only the accuracy and re-deriving the rest is what
        # "faithful copy" cannot mean — composite, evaluators, counts and whisker are the
        # parent's measurement or they are a new one.
        report=cand.model_copy(
            update={"candidate_id": resolved_origin.lineage.id, "label": candidate_label(0, 0)}
        ),
        origin_results=inherited_results,
    )


# C0 lineage description per ``CycleSeed.origin_source`` — keyed lookup, no
# branch: the seed declares its own provenance, the resolver stamps it.
_SEED_ORIGIN_LINEAGE = {
    "fork_seed": "Operator-steered fork — edited searchpoint as origin",
    "campaign_origin": "Fresh campaign minted from a chosen prior origin",
}


def resolve_origin_opt_search_point(
    prompt_node_names: list[str] | None = None,
    dataset_dir: Path | None = None,
    *,
    seed: CycleSeed | None = None,
) -> OptSearchPoint:
    """Resolve the origin OptSearchPoint by precedence, highest wins:
    seed → {dataset_dir}/prompts → empty.

    A *seed* with non-empty ``origin_prompt_fields`` wins outright — an
    operator-steered fork's (or a campaign-from-origin's) origin *is* the chosen
    searchpoint, so we build the OSP straight from those fields and short-circuit
    (no dataset lookup), stamping the C0 lineage from ``seed.origin_source``.
    *dataset_dir* is the resolved config dir (``Session.dataset_config_dir``,
    tenant-first), so an ingested dataset's authored prompts are found the same way a
    repo benchmark's are."""
    if seed is not None and seed.origin_prompt_fields:
        return OptSearchPoint.from_prompt_fields(
            seed.origin_prompt_fields,
            lineage=IndividualLineage(
                changes_description=_SEED_ORIGIN_LINEAGE[seed.origin_source],
                source=seed.origin_source,
            ),
        )

    names = prompt_node_names or []
    if dataset_dir is not None and names:
        from promptpotter.application.datasets.prompts import has_dataset_prompts, load_node_prompt

        if has_dataset_prompts(dataset_dir):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_dir, node_name, "default")
                except FileNotFoundError:
                    continue
                return OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    lineage=IndividualLineage(
                        changes_description=(f"Origin from {dataset_dir}/prompts/ ({node_name})"),
                        source="origin",
                    ),
                )

    return OptSearchPoint(
        lineage=IndividualLineage(
            changes_description="Origin (no prompt node active — param-only optimization)",
            source="origin",
        ),
    )


async def establish_campaign_origin(
    session: Session,
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    seed: CycleSeed | None,
    listener: Any | None,
) -> CampaignOrigin:
    """The single origin-establishment seam. The origin OSP is resolved exactly once and
    shared by both branches (inherit a no-edit fork's recorded C0 vs score), which return
    the same ``CampaignOrigin`` shape."""
    resolved_origin = resolve_origin_opt_search_point(
        prompt_node_names=session.pipeline_schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
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
    svc: Any = None,
    listener: Any | None = None,
    obs: Any | None = None,
    seed: CycleSeed | None = None,
    resolved_origin: OptSearchPoint | None = None,
) -> tuple[CampaignOrigin, list[Sample]]:
    """*resolved_origin* lets the caller pass an already-resolved origin OSP (so it isn't
    resolved twice on the runner path); when ``None`` it's resolved here (the notebook path)."""
    from promptpotter.application.datasets.loaders import sample_dataset

    if resolved_origin is None:
        prompt_nodes = pipeline_schema.prompt_node_names()
        resolved_origin = resolve_origin_opt_search_point(
            prompt_node_names=prompt_nodes,
            dataset_dir=getattr(svc, "dataset_config_dir", None),
            seed=seed,
        )
    dataset = train_data or []

    # Score the origin whenever there is a live run to score it in (session + config +
    # dataset). We deliberately do NOT sniff the searchpoint shape to guess "is there a
    # program here?" — that guess (non-empty prose OR a dict-valued pipeline_param)
    # silently skipped the L4 outer origin, whose prose is empty and whose node configs
    # are empty because its program IS the inner recursion the connector runs. A silent
    # skip is indistinguishable downstream from a crash (round 0 lands total=0). An origin
    # that genuinely cannot be scored is caught LOUD by the round-0 origin gate (total=0 →
    # critical → halt-and-decide), never hidden here. The remaining guard is the
    # no-session notebook/test path, which has nothing to score.
    from promptpotter.application.optimization.l1.population import (
        INVALID_SCORES,
        build_score_report,
    )

    if not (campaign_config is not None and svc is not None and dataset):
        # Nothing to score. The resolved origin still travels — the old empty-list branch
        # dropped it and handed back a blank OptSearchPoint(instruction="").
        return (
            CampaignOrigin(
                resolved_origin=resolved_origin,
                # An unmeasured origin reports as unmeasured, in the same shape a measured
                # one uses: `total=0` is the no-evidence marker every reader already knows,
                # where a bare 0.0 accuracy is indistinguishable from a real floor of zero.
                report=build_score_report(
                    resolved_origin, None, INVALID_SCORES, [], [], label=candidate_label(0, 0)
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
    else:
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
        scoring_round_formula=spec.per_round,
        scorer_id=spec.scorer_id,
        source="origin",
    )

    # ci=0/ct=1 ⇒ dashboard ticks per-sample during origin like L1.
    if listener is not None:
        emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "enter", round=0)

    # C0 is a minted candidate like any other: named on the ledger before it is measured,
    # then measured through the same report every candidate deposits below.
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
        # The origin is the campaign's whole reference — a transient-transport scoring abort
        # (a provider blip) must not bank a corrupted floor. Re-score once fresh if the first
        # pass aborted transiently; the blip usually passes. A config-deterministic abort is
        # NOT retried — it's a real fault the operator must fix.
        for attempt in range(2):
            origin_results, scores, signal = await score_search_point(
                sp,
                scoring_set,
                session,
                label="Origin",
                # C0 is the reference every later delta is taken against, so it sits on the
                # same vacuous fallback as the matched floor above. Threading the origin's
                # own OSP here would give the reference an opt_sp-aware composite that no
                # candidate's matched floor shares.
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
        # Origin is candidate 0 of round 0, and it is measured ONCE, here, into the same
        # `ScoredCandidate` report every L1 candidate gets. This object is both what the
        # ledger receives and what round 0's row is built from, so the two cannot be two
        # computations of one measurement — `Cycle.start` used to re-derive the composite
        # and the evaluator namespace from these same rows, beside the gateway that had
        # just returned them.
        report = build_score_report(
            resolved_origin,
            None,
            scores,
            origin_results,
            scoring_set,
            label=candidate_label(0, 0),
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
