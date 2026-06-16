"""Campaign data loading and origin scoring."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.config.settings import DATASET_NAME
from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint
from promptpotter.domain.results import RoundOrigin
from promptpotter.domain.run_records import CycleSeed
from promptpotter.domain.sample import Sample

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


logger = logging.getLogger(__name__)

__all__ = [
    "ORIGIN_RESOLUTION_PRIORITY",
    "CampaignOrigin",
    "DatasetSummary",
    "build_campaign_emitter",
    "establish_campaign_origin",
    "extract_campaign_origin",
    "prepare_datasets",
    "prepare_scoring_context",
    "rescore_origin",
    "resolve_origin_opt_search_point",
    "try_inherit_fork_origin",
]


async def rescore_origin(
    cycle: Cycle,
    scoring_set: list[Sample],
    round_num: int,
    *,
    callbacks: RunCallbacks,
    force_fresh: bool = False,
) -> RoundOrigin:
    """Score the incumbent champion on THIS round's ``scoring_set`` so winner election
    compares candidate-vs-incumbent on the SAME hard-first samples the candidates run.

    ``force_fresh`` bypasses the measurement cache (see ``score_search_point``). The
    winner-election path leaves it ``False`` — the incumbent's own prior measurements
    replay for free. The origin gate sets it ``True`` so a re-score after a backend-code
    fix reflects the fix instead of replaying the stale (broken) origin.

    The online picker scores each candidate on a different hard-first subset, but the
    incumbent (the origin at round 1, the prior winner after) was only ever scored on
    its own earlier rounds. ``matched_origin_stats`` therefore intersected disjoint
    sample sets and returned a fake ``0.0`` floor — letting a candidate "improve" over an
    origin floor that was never measured (round 1: 0 hits, ``improved=True``). Re-scoring the
    incumbent here, through the ``score_search_point`` gateway + content-hash cache,
    yields a real same-subset floor; samples it already measured replay from cache for
    free, so the cost is one measurement per *new* hard sample the incumbent hasn't seen.

    ``candidate_idx=-1`` is the backfill sentinel (the display layer prefixes the row so
    the operator sees the origin's spend); ``degradation_checks=None`` blocks the floor
    from aborting itself.
    """
    from functools import partial

    from promptpotter.application.scoring.metrics import compute_composite_fitness
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    session = cycle.session
    schema = session.pipeline_schema
    tr = cycle.tracking
    assert tr.current_sp is not None
    results, _scores, _cached, _signal = await score_search_point(
        tr.current_sp,
        scoring_set,
        session,
        label="origin_baseline",
        degradation_checks=None,
        candidate_idx=-1,
        n_total_candidates=0,
        axes=cycle.axes,
        l1_diversity=0.0,
        on_sample_scored=partial(callbacks.on_sample_scored, -1, 0),
        on_sample_starting=partial(callbacks.on_sample_started, -1, 0),
        force_fresh=force_fresh,
    )
    accuracy = tr.current_accuracy
    composite_fitness = tr.current_composite_fitness
    if schema is not None and results:
        s = compute_composite_fitness(
            results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        accuracy = s["accuracy"]
        composite_fitness = s["composite_fitness"]
    return RoundOrigin(
        accuracy=accuracy,
        composite_fitness=composite_fitness,
        osp=cycle.opt_sp,
        results=results,
        label=f"round_{round_num}" if round_num > 0 else "origin",
    )


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    origin_accuracy: float,
    resumed_from_round: int | None = None,
    recorder: Any | None = None,
    seed_from_cycle_id: str | None = None,
) -> Any:
    """Live dashboard projection from session + config (shared by CLI + runner).

    ``seed_from_cycle_id`` (set when building a fork's dashboard) names the
    parent cycle to seed prior trajectory from; ``None`` seeds from the cycle's
    own dir.
    """
    from promptpotter.infrastructure.projections import LiveDashboardView

    opt = campaign_config.optimization
    return LiveDashboardView.for_session(
        session.state.cycle_id,
        project_root=session.project_root,
        session_id=session.session_id,
        campaign_id=session.campaign_id,
        l1_patience=opt.l1_patience,
        n_variants=opt.n_variants,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        resumed_from_round=resumed_from_round,
        recorder=recorder,
        seed_from_cycle_id=seed_from_cycle_id,
    )


class CampaignOrigin(NamedTuple):
    """Extracted origin state. ``resolved_origin`` is the origin OptSearchPoint —
    carrying its full lineage/memory, not just prompt strings — so the C0
    individual keeps its ``source`` marker (e.g. ``fork_seed``) downstream."""

    resolved_origin: OptSearchPoint | None
    origin_acc: float
    origin_results: list[Any] | None
    instruction: str


def extract_campaign_origin(campaign_rounds: list[dict[str, Any]]) -> CampaignOrigin:
    """Origin prompt state, accuracy, results from campaign rounds.
    Walks reversed rounds for the last with scoring results; the origin OSP rides
    the tip's ``origin_search_point`` slot."""
    if not campaign_rounds:
        return CampaignOrigin(
            resolved_origin=OptSearchPoint(instruction=""),
            origin_acc=0.0,
            origin_results=None,
            instruction="",
        )

    tip = campaign_rounds[-1]

    # Prefer accuracy from last round with scoring results; fall back to tip (scan winner: acc but no results).
    origin_acc = tip.get("accuracy", 0.0)
    origin_results: list[Any] = []
    for rd in reversed(campaign_rounds):
        if rd.get("results"):
            origin_acc = rd.get("accuracy", origin_acc)
            origin_results = rd["results"]
            break

    tip_ps = tip["origin_search_point"]

    return CampaignOrigin(
        resolved_origin=tip_ps,
        origin_acc=origin_acc,
        origin_results=origin_results,
        instruction=tip_ps.instruction,
    )


def try_inherit_fork_origin(
    session: Session,
    seed: CycleSeed | None,
    *,
    resolved_origin: OptSearchPoint,
) -> CampaignOrigin | None:
    """Inherit a no-modification operator fork's C0 from its branch-point candidate.

    When an operator forks from a searchpoint WITHOUT editing it, that searchpoint
    *is* the fork's origin — its accuracy was already measured in the parent round.
    Re-scoring it would re-roll a different number under a nondeterministic backend
    (the same prompt + samples does NOT reproduce the same accuracy), so C0 would no
    longer equal the branch point and the lineage would jump. Instead we inherit the
    recorded measurement and skip the origin scoring pass — the loop goes straight to
    L1_generate.

    *resolved_origin* is the already-resolved fork origin (resolved once by
    ``establish_campaign_origin``). Returns the inherited :class:`CampaignOrigin` only
    when this is an operator-steered fork whose origin renders identically to the
    ``from_candidate_id`` candidate in the parent's recorded round. Any miss (non-fork,
    missing coords, edited prompt → different render) returns ``None`` and the caller
    re-scores as before.
    """
    if seed is None or not seed.origin_prompt_fields:
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
    if not isinstance(parent_round, dict):
        return None
    scores = parent_round.get("candidate_scores")
    if not isinstance(scores, list):
        return None
    cand = next(
        (c for c in scores if isinstance(c, dict) and c.get("candidate_id") == from_candidate_id),
        None,
    )
    if cand is None or not isinstance(cand.get("accuracy"), int | float):
        return None
    cand_fields = cand.get("prompt_fields")
    if not isinstance(cand_fields, dict):
        return None

    # Identity gate: the fork origin's prompt must render identically to the
    # branch-point candidate's. An operator edit changes the render → re-score.
    if resolved_origin.render() != OptSearchPoint.from_prompt_fields(cand_fields).render():
        return None

    origin_acc = float(cand["accuracy"])
    logger.info(
        "Fork %s: inheriting C0 from branch-point candidate %s (parent %s round %d) "
        "acc=%.4f — skipping origin re-score, straight to L1",
        session.state.cycle_id,
        from_candidate_id,
        parent,
        from_round,
        origin_acc,
    )
    # origin_results left empty: per-candidate per-sample results aren't in the round
    # file, and the reuse cache is noisy under nondeterminism. Hard-sample seeding for
    # round 1 starts clean; the inherited C0 accuracy (the operator-facing value) is exact.
    # resolved_origin carries the OSP object (not a prompt-field dict) so the inherited C0 keeps
    # its lineage(source=seed.origin_source) — same shape the re-score path produces.
    return CampaignOrigin(
        resolved_origin=resolved_origin,
        origin_acc=origin_acc,
        origin_results=[],
        instruction=resolved_origin.instruction,
    )


ORIGIN_RESOLUTION_PRIORITY = (
    "seed",  # operator-steered fork OR campaign-from-origin: the chosen searchpoint IS the origin
    "experiment",  # experiment_extract dependencies' prompt registry
    "dataset",  # {dataset_dir}/prompts/{node}.json (tenant-first)
    "empty",  # no prompt node active — param-only optimization
)
"""Origin-OSP resolution order, highest wins — the single legible statement of the
precedence that ``resolve_origin_opt_search_point`` walks branch by branch."""

# C0 lineage description per ``CycleSeed.origin_source`` — keyed lookup, no
# branch: the seed declares its own provenance, the resolver stamps it.
_SEED_ORIGIN_LINEAGE = {
    "fork_seed": "Operator-steered fork — edited searchpoint as origin",
    "campaign_origin": "Fresh campaign minted from a chosen prior origin",
}


def resolve_origin_opt_search_point(
    experiment_extract: dict[str, Any],
    prompt_node_names: list[str] | None = None,
    dataset_dir: Path | None = None,
    *,
    seed: CycleSeed | None = None,
) -> OptSearchPoint:
    """Resolve the origin OptSearchPoint by :data:`ORIGIN_RESOLUTION_PRIORITY`
    (seed → experiment prompts → {dataset_dir}/prompts → empty).

    A *seed* with non-empty ``origin_prompt_fields`` wins outright — an
    operator-steered fork's (or a campaign-from-origin's) origin *is* the chosen
    searchpoint, so we build the OSP straight from those fields and short-circuit
    (no dataset/experiment lookup), stamping the C0 lineage from
    ``seed.origin_source``. *dataset_dir* is the resolved config dir
    (``Session.dataset_config_dir``, tenant-first), so an ingested dataset's
    authored prompts are found the same way a repo benchmark's are."""
    if seed is not None and seed.origin_prompt_fields:
        return OptSearchPoint.from_prompt_fields(
            seed.origin_prompt_fields,
            lineage=IndividualLineage(
                changes_description=_SEED_ORIGIN_LINEAGE[seed.origin_source],
                source=seed.origin_source,
            ),
        )

    dependencies = experiment_extract.get("dependencies", {})
    prompts = dependencies.get("prompts", {})
    names = prompt_node_names or []

    matched_prompt = None
    matched_key = None
    for node_name in names:
        for key, prompt_info in prompts.items():
            if node_name in key:
                matched_prompt = prompt_info
                matched_key = key
                break
        if matched_prompt:
            break

    if matched_prompt is None and not names and prompts:
        matched_key, matched_prompt = next(iter(prompts.items()))

    if matched_prompt is not None:
        label = names[0] if names else matched_key
        return OptSearchPoint(
            instruction=matched_prompt["template"],
            lineage=IndividualLineage(
                changes_description=f"Origin prompt from {label} registry",
                source="origin",
            ),
        )

    if dataset_dir is not None and names:
        from promptpotter.application.datasets import (
            has_dataset_prompts,
            load_node_prompt,
        )

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
        instruction="",
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
    """The single origin-establishment seam: resolve the origin OSP once, then either
    inherit it (no-modification operator fork) or score it.

    A no-edit operator fork inherits its branch-point candidate's recorded C0 and skips
    the scoring pass (straight to L1); everything else scores the origin via
    ``prepare_scoring_context``. Both branches share the one resolved OSP, so the origin
    is resolved exactly once and both paths return the same ``CampaignOrigin`` shape."""
    resolved_origin = resolve_origin_opt_search_point(
        session.experiment_extract,
        prompt_node_names=(
            session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
        ),
        dataset_dir=getattr(session, "dataset_config_dir", None),
        seed=seed,
    )
    inherited = try_inherit_fork_origin(session, seed, resolved_origin=resolved_origin)
    if inherited is not None:
        return inherited

    _, _, campaign_rounds, _ = await prepare_scoring_context(
        session.experiment_extract,
        dataset,
        campaign_config,
        pipeline_params=session.pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=listener,
        seed=seed,
        resolved_origin=resolved_origin,
    )
    return extract_campaign_origin(campaign_rounds)


async def prepare_scoring_context(
    experiment_extract: dict[str, Any] | None,
    train_data: list[Sample] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    pipeline_params: dict[str, Any] | None = None,
    pipeline_schema: PipelineSchema | None = None,
    svc: Any = None,
    listener: Any | None = None,
    obs: Any | None = None,
    seed: CycleSeed | None = None,
    resolved_origin: OptSearchPoint | None = None,
) -> tuple[OptSearchPoint, list[Sample], list[dict[str, Any]], list[Any]]:
    """Resolve origin (fork-seed wins), set dataset, produce a populated ``campaign_rounds[0]``.

    *resolved_origin* lets the caller pass an already-resolved origin OSP (so it isn't
    resolved twice on the runner path); when ``None`` it's resolved here (the notebook path)."""
    from promptpotter.application.datasets import sample_dataset

    if resolved_origin is None:
        prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else []
        resolved_origin = resolve_origin_opt_search_point(
            experiment_extract or {},
            prompt_node_names=prompt_nodes,
            dataset_dir=getattr(svc, "dataset_config_dir", None),
            seed=seed,
        )
    dataset = train_data or []

    campaign_rounds: list[dict[str, Any]] = []
    origin_results: list[Any] = []
    if not (
        campaign_config is not None
        and svc is not None
        and dataset
        and resolved_origin.render().strip()
    ):
        return resolved_origin, dataset, campaign_rounds, origin_results

    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.phases import CampaignPhase, emit_phase
    from promptpotter.shared.errors import graceful

    session: Session = svc
    sp_budget = campaign_config.sp_budget_ttest
    scoring_set = sample_dataset(dataset, sp_budget)
    spec = split_scoring_block(campaign_config.scoring)

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    else:
        logger.warning(
            "No session terms available — /matches calls will fail. "
            "Load datasets first (Excel ground truth → DatasetStore)."
        )

    if obs:
        with graceful("Dataset registration in origin scoring failed"):
            obs.register_dataset(DATASET_NAME, scoring_set)

    sp = resolved_origin.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )
    # populate_session_scoring overwrites scoring/source; loop repopulates before round 1.
    prior_schema = session.pipeline_schema
    if pipeline_schema is not None:
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

    try:
        origin_results, scores, _cached, _ = await score_search_point(
            sp,
            scoring_set,
            session,
            label="Origin",
            on_sample_starting=(
                partial(listener.on_sample_started, 0, 1) if listener is not None else None
            ),
            on_sample_scored=(
                partial(listener.on_sample_scored, 0, 1) if listener is not None else None
            ),
        )
        # Origin is candidate 0 of round 0 — deposit its aggregate score so the live
        # round buffer (→ round_0000.json node block) carries the same stats every
        # round's candidates do.
        if listener is not None:
            listener.on_candidate_scored(0, 1, scores)
    finally:
        if listener is not None:
            emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "exit", round=0)
        session.pipeline_schema = prior_schema

    campaign_rounds = [
        {
            "round": 0,
            "label": "origin",
            "origin_search_point": resolved_origin,
            "accuracy": scores["accuracy"],
            "hits": scores["hits"],
            "total": scores["total"],
            "results": origin_results,
        }
    ]

    return resolved_origin, dataset, campaign_rounds, origin_results


class DatasetSummary(NamedTuple):
    """Return from ``prepare_datasets()``."""

    train_data: list[Sample] | None
    index_terms: list[str]
    splits: dict[str, list[Sample]]
    n_unique_samples: int


def prepare_datasets(
    store: Stores,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> DatasetSummary:
    """Load/create datasets + build session terms (pure orchestration)."""
    from promptpotter.application.datasets import (
        SHEET_COLUMN_MAP,
        load_excel_ground_truth,
        samples_from_dicts,
        split_train_test,
    )

    if excel_path:
        excel_path = Path(excel_path)
        existing = store.backends.load_dataset("train")
        needs_create = force or not (existing and existing.get("items"))

        if needs_create:
            all_rows = load_excel_ground_truth(excel_path, SHEET_COLUMN_MAP)
            train, test_sets = split_train_test(all_rows)
            store.backends.save_dataset("train", train, source_file=excel_path.name)
            for name, items in test_sets.items():
                store.backends.save_dataset(name, items, source_file=excel_path.name)

    # Single pass per split: samples + GT set (/match index) + unique query set. Test contributes GTs only.
    splits: dict[str, list[Sample]] = {}
    gt_set: set[str] = set()
    all_queries: set[str] = set()
    for name in ("train", "test_processes", "test_material"):
        ds = store.backends.load_dataset(name)
        raw_items = ds["items"] if ds and ds.get("items") else []
        splits[name] = samples_from_dicts(raw_items)
        for item in raw_items:
            gt = item.get("ground_truth", "").strip()
            if gt:
                gt_set.add(gt)
        for s in splits[name]:
            q = s.query.strip()
            if q:
                all_queries.add(q)

    return DatasetSummary(
        train_data=splits["train"] or None,
        index_terms=sorted(gt_set),
        splits=splits,
        n_unique_samples=len(all_queries),
    )
