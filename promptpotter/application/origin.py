from __future__ import annotations

import json
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import (
    bank_samples,
    resolve_dataset_items,
    sample_dataset,
)
from promptpotter.application.datasets.prompts import has_dataset_prompts, load_node_prompt
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    experiment_outside_run,
    resolve_pipeline_config_params,
)
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id
from promptpotter.application.scoring.candidate_report import (
    INVALID_SCORES,
    build_score_report,
    is_transient_scoring_abort,
    walk_outcome,
)
from promptpotter.application.scoring.formula import (
    DIALS_KEY,
    origin_anchors,
    parse_dials,
    realize_dials,
    rescore_results,
    split_scoring_block,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import ORIGIN_SOURCE, IndividualLineage, OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.pipeline_overlay import overlay_is_locked_axis_only
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import (
    ArmOutcome,
    ReferenceReading,
    ScoredCandidate,
    candidate_label,
)
from promptpotter.domain.run_records import CandidateMintedRecord, CycleSeed
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.judges import judge_instrument
from promptpotter.shared.errors import (
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)
from promptpotter.shared.instrument import (
    NO_ROUND_SLOT,
    MeasuredCandidate,
    MeasurementRole,
)

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignOrigin",
    "establish_campaign_origin",
    "rescore_parent",
    "resolve_origin_opt_search_point",
    "try_inherit_fork_origin",
]


async def rescore_parent(
    cycle: Cycle,
    scoring_set: list[Sample],
    *,
    callbacks: RunCallbacks,
    force_fresh: bool = False,
) -> ReferenceReading:
    """Score the round's parent on THIS round's ``scoring_set``, so election compares on the SAME
    samples. Without it ``matched_parent_stats`` intersects disjoint sets and returns a fake floor."""

    session = cycle.session
    tr = cycle.tracking
    assert tr.current_sp is not None
    scored = await score_search_point(
        tr.current_sp,
        scoring_set,
        session,
        label=MeasurementRole.PARENT,
        sample_index=cycle.sample_index,
        # Ticked, not silenced. This is the LONGEST phase of a held round — the parent walks the
        # whole panel while the candidates stopped wherever PoBB cut them — so a silenced one
        # serves `between_samples` throughout, which on a `measured_unit="cell"` connector is tens
        # of minutes of a live run reading as hung.
        on_sample_scored=partial(callbacks.on_sample_scored, NO_ROUND_SLOT, 0),
        on_sample_starting=partial(callbacks.on_sample_started, NO_ROUND_SLOT, 0),
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=cycle.opt_sp.lineage.id,
            label=cycle.rounds[-1].label,
            role=MeasurementRole.PARENT,
        ),
        force_fresh=force_fresh,
    )
    # Kept: `matched_parent_stats` pairs each candidate on the cells both reached, and the report
    # carries the shortfall as `scored_samples < expected_samples`. A held round's headline is
    # this pass, though, so the shortfall is said aloud rather than left for a reader to count.
    if scored.stopped is not None:
        logger.warning(
            "Round parent %s stopped after %d/%d cells (%s); its floor covers only those.",
            cycle.rounds[-1].label,
            len(scored.results),
            len(scoring_set),
            scored.stopped,
        )
    return ReferenceReading(
        opt_sp=cycle.opt_sp,
        results=cast("list[dict[str, Any]]", scored.results),
        # The gateway's OWN answer — never re-run `compute_composite_fitness` over the same
        # rows, which drops the evaluator namespace on the way.
        report=build_score_report(
            cycle.opt_sp,
            (),
            None,
            scored.scores,
            scored.results,
            scoring_set,
            # The parent INDIVIDUAL's label (``C0``, ``C3.1``, …), off the round it won — it
            # reaches disk, so a synthesized round name here would name no candidate.
            label=cycle.rounds[-1].label,
            sp_hash=tr.current_sp.sp_hash(session.pipeline_schema),
            run_id=scored.run_id,
            outcome=walk_outcome(scored),
        ),
    )


class CampaignOrigin(NamedTuple):
    """The scored origin. ``report`` is C0's measurement in the one shape every individual's takes —
    the same object deposited on the ledger, so round 0's row cannot be a second computation.
    ``framing`` is the campaign's, read once here and frozen for the run."""

    resolved_origin: OptSearchPoint | None
    report: ScoredCandidate
    origin_results: list[Any] | None
    framing: TaskDecomposition
    # The scoring block this measurement LOCKED, where the campaign declared dials: the run
    # continues under it. ``None`` where there was nothing to lock.
    locked_scoring: dict[str, str] | None = None


def lock_criterion(
    session: Session, campaign_config: CampaignConfig, origin_rows: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Turn a scoring block's DECLARED dials into the ``per_cell`` formula they spell, anchored on
    what the origin just measured, and freeze it into the campaign manifest.

    Once, and it never moves: every later cycle of the campaign reads a plain ``per_cell`` off the
    manifest, so a fork or a resume is graded on the same anchors as the rounds before it. ``None``
    where the block declares no dials — a hand-written ``per_cell`` is already locked."""
    block = campaign_config.scoring
    if not isinstance(block, dict) or DIALS_KEY not in block:
        return None
    formula = realize_dials(parse_dials(block[DIALS_KEY]), origin_anchors(origin_rows))
    locked = {k: v for k, v in block.items() if k != DIALS_KEY} | {"per_cell": formula}
    campaign = session.store.campaigns.load_campaign(session.hop.campaign_id)
    # A fork's seed can declare dials over a campaign already locked: those belong to that
    # cycle's seed, and writing them here would re-grade every sibling.
    if campaign is not None and campaign.config.get("scoring") == block:
        session.store.campaigns.update_campaign(
            campaign.campaign_id, {"config": {**campaign.config, "scoring": locked}}
        )
    return locked


def try_inherit_fork_origin(
    session: Session,
    seed: CycleSeed | None,
    *,
    resolved_origin: OptSearchPoint,
    framing: TaskDecomposition,
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

    # Identity gate: an operator edit changes the render or the shots → re-score.
    if resolved_origin.prompt_field_dict() != cand.prompt_fields:
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
    # The OSP object, not a prompt-field dict, so the inherited C0 keeps the lineage the seed
    # stamped — same shape the re-score path produces.
    return CampaignOrigin(
        resolved_origin=resolved_origin,
        # The branch-point candidate's whole report, re-identified onto this fork's C0:
        # composite, evaluators, counts and whisker are the parent's measurement or they
        # are a new one.
        report=cand.model_copy(
            update={"candidate_id": resolved_origin.lineage.id, "label": candidate_label(0, 0)}
        ),
        origin_results=inherited_results,
        framing=framing,
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
                source=ORIGIN_SOURCE,
            ),
        )
    elif dataset_dir is not None and names and has_dataset_prompts(dataset_dir):
        for node_name in names:
            try:
                template = load_node_prompt(dataset_dir, node_name, "default")
            except FileNotFoundError:
                continue
            origin = OptSearchPoint.from_prompt_fields(
                template.prompt_fields(),
                lineage=IndividualLineage(
                    changes_description=(f"Origin from {dataset_dir}/prompts/ ({node_name})"),
                    source=ORIGIN_SOURCE,
                ),
            )
            break

    if origin is None:
        origin = OptSearchPoint(
            lineage=IndividualLineage(
                changes_description="Origin (no prompt node active — param-only optimization)",
                source=ORIGIN_SOURCE,
            ),
        )

    return origin


async def establish_campaign_origin(
    session: Session,
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    seed: CycleSeed | None,
    listener: RunCallbacks,
) -> CampaignOrigin:
    """The single origin-establishment seam — the OSP is resolved exactly once and shared by the
    inherited, the unmeasured and the scored branch, which return the same shape."""

    resolved_origin = resolve_origin_opt_search_point(
        prompt_node_names=session.pipeline_schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
        seed=seed,
    )
    # The SAME read identity uses. Handed the framing instead, this seam could be given a value
    # the cycle id never saw.
    framing = campaign_framing(session.store, campaign_config, session.dataset_name)
    inherited = try_inherit_fork_origin(
        session, seed, resolved_origin=resolved_origin, framing=framing
    )
    if inherited is not None:
        return inherited

    # Never sniff the searchpoint shape to guess "is there a program here?" — the L4 outer
    # origin's prose and node configs are both empty because its program IS the inner recursion,
    # and the skip that guess produced is indistinguishable downstream from a crash. An
    # unscoreable origin is caught LOUD by the round-0 origin gate, never hidden here.
    if not dataset:
        if isinstance(campaign_config.scoring, dict) and DIALS_KEY in campaign_config.scoring:
            raise PayloadInvalidError(
                "the campaign declares scoring dials, and dials are anchored on the origin's "
                "measured cells — this origin measures none. Declare 'per_cell' instead.",
                code="pipeline_config_invalid",
            )
        # The resolved origin still travels — dropping it hands back a blank
        # OptSearchPoint(instruction="").
        return CampaignOrigin(
            resolved_origin=resolved_origin,
            # Unmeasured reports as unmeasured in the shape a measured one uses: `total=0`
            # is the no-evidence marker, where a bare 0.0 reads as a real floor of zero.
            report=build_score_report(
                resolved_origin,
                (),
                None,
                INVALID_SCORES,
                [],
                [],
                label=candidate_label(0, 0),
                # No rows for an id to address.
                sp_hash="",
                run_id=None,
                outcome=ArmOutcome.MEASURED,
            ),
            origin_results=None,
            framing=framing,
        )

    pipeline_schema = session.pipeline_schema
    scoring_set = sample_dataset(dataset, campaign_config.sp_budget_origin)
    spec = split_scoring_block(
        campaign_config.scoring, judge_instrument=judge_instrument(campaign_config.judges)
    )

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    elif session.backend_client.execution == "remote_http":
        # Only a wired backend can be broken by an empty term index; an `in_process` one has no
        # `/matches` to fail.
        logger.warning("No session terms available — /matches calls will fail.")

    sp = resolved_origin.to_job_search_point(
        base_pipeline_params=session.pipeline_params,
        schema=pipeline_schema,
        framing=framing,
        demo=session.scoring.require_partition().demo,
    )
    # populate_session_scoring overwrites scoring/source; loop repopulates before round 1.
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=spec.per_sample,
        scoring_cell_formula=spec.per_cell,
        scorer_id=spec.scorer_id,
        display_metric=campaign_config.display_metric,
        judge_specs=campaign_config.judges,
        source=RunSource.ORIGIN,
    )

    # ci=0/ct=1 ⇒ dashboard ticks per-sample during origin like L1.
    emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "enter", round=0)

    # C0 is a minted candidate like any other — named on the ledger before it is measured.
    if (ledger := session.state.ledger) is not None:
        ledger.append(
            CandidateMintedRecord(
                round=0,
                idx=0,
                candidate_id=resolved_origin.lineage.id,
                parent_ids=list(resolved_origin.lineage.parent_ids),
                label=candidate_label(0, 0),
                changes_description=resolved_origin.lineage.changes_description,
                source=resolved_origin.lineage.source,
            )
        )

    # C0 announces itself exactly as an L1 candidate does — same call, same two emissions, so
    # round 0 carries a walk axis and a readable searchpoint like any other round. `n_priors=0`
    # is a fact rather than a default: C0 is the first arm, so nothing has been measured for
    # PoBB to catch up on, and there is no `pipeline_overlay` because nothing proposed a delta.
    listener.announce_candidate(
        0,
        0,
        1,
        opt_sp=resolved_origin,
        resolved_pipeline_params=sp.config_params,
        sample_order=[s.id for s in scoring_set],
    )

    try:
        # The origin is the campaign's whole reference, so a transient-transport abort must not
        # bank a corrupted floor — re-score once fresh. A config-deterministic abort is NOT
        # retried; it is a real fault the operator must fix. A pass that stays partial is graded
        # by coverage (`origin_gate`), which is why nothing here reads `stopped`.
        for attempt in range(2):
            scored = await score_search_point(
                sp,
                scoring_set,
                session,
                label=MeasurementRole.ORIGIN,
                measured=None,
                force_fresh=attempt > 0,
                on_sample_starting=partial(listener.on_sample_started, 0, 1),
                on_sample_scored=partial(listener.on_sample_scored, 0, 1),
            )
            if not is_transient_scoring_abort(scored.signal):
                break
            logger.warning(
                "Origin scoring hit a transient transport abort — re-scoring once fresh."
            )
        locked_scoring = lock_criterion(
            session, campaign_config, cast("list[dict[str, Any]]", scored.results)
        )
        if locked_scoring is not None:
            # The rows were graded on correctness alone, the only criterion an unmeasured origin
            # has. Re-grade them under the one they just anchored — nothing is re-measured.
            spec = split_scoring_block(
                locked_scoring, judge_instrument=judge_instrument(campaign_config.judges)
            )
            populate_session_scoring(
                session,
                obs=None,
                scoring_formula=spec.per_sample,
                scoring_cell_formula=spec.per_cell,
                scorer_id=spec.scorer_id,
                display_metric=campaign_config.display_metric,
                judge_specs=campaign_config.judges,
                source=RunSource.ORIGIN,
            )
            rescore_results(
                cast("list[dict[str, Any]]", scored.results), session.scoring.require_scorer()
            )
            scored.scores.update(compute_composite_fitness(scored.results, pipeline_schema))
        # Candidate 0 of round 0, measured ONCE. This object is both what the ledger receives
        # and what round 0's row is built from — never re-derive either from these rows.
        report = build_score_report(
            resolved_origin,
            (),
            None,
            scored.scores,
            scored.results,
            scoring_set,
            label=candidate_label(0, 0),
            sp_hash=sp.sp_hash(pipeline_schema),
            run_id=scored.run_id,
            outcome=walk_outcome(scored),
            resolved_pipeline_params=sp.config_params,
        )
        listener.on_candidate_scored(0, 1, report.model_dump())
    finally:
        emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "exit", round=0)

    return CampaignOrigin(
        resolved_origin=resolved_origin,
        report=report,
        origin_results=scored.results,
        framing=framing,
        locked_scoring=locked_scoring,
    )


def prospective_origin_id(stores: Stores, dataset_dir: Path, dataset_name: str) -> str | None:
    """The dataset's CURRENT committed config-aware origin id — the same hash a fresh mint would
    stamp, computed from disk with no Session.

    The PROSPECTIVE twin of ``pipeline_resolve.resolve_pipeline_for_campaign``, and deliberately
    NOT that function: it answers for a dataset that has no campaign yet, so there is no campaign
    layer to apply and no manifest to freeze. What the two share is the merge primitive
    ``resolve_pipeline_config_params``, which is what keeps this id from diverging from the one a
    real run stamps. It lived in the origins ROUTER, which put a hash computation behind an
    adapter no other entry point could reach."""
    try:
        experiment = experiment_outside_run(dataset_dir)
        schema = parse_pipeline_response(
            dataset_pipeline_declaration(stores, dataset_dir, experiment) or {}
        )
        cfg = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
        active = schema.active_steps_excluding(cfg.exclude_nodes)
        if not active:
            return None
        base_pp = resolve_pipeline_config_params(
            active,
            cfg.pipeline_overlay,
            dataset_dir,
            schema,
            judges=cfg.judges,
            experiment=experiment,
            stores=stores,
            workspace=stores.base_dir,
        )
        opt_sp = resolve_origin_opt_search_point(
            prompt_node_names=schema.prompt_node_names(),
            dataset_dir=dataset_dir,
        )
        items = resolve_dataset_items(stores, dataset_name)
        if not items:
            return None
        partition = partition_bank(bank_samples(items), cfg.dataset_split)
        return build_origin_cycle_id(
            opt_sp,
            schema,
            list(partition.search),
            base_pp,
            framing=campaign_framing(stores, cfg, dataset_name),
            demo=partition.demo,
        ).removeprefix("cycle_")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        StoredConfigInvalidError,
        NotFoundError,
        PayloadInvalidError,
    ):
        # A SURVEY over every tenant dataset: an unreadable neighbour, or an outer one whose inner
        # benchmark does not resolve, drops itself, never the list. Direct reads still raise.
        logger.exception("origins: prospective origin id failed for %s", dataset_name)
        return None
