from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field

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
    build_score_report,
    is_transient_scoring_abort,
    walk_outcome,
)
from promptpotter.application.scoring.cells import closed_round
from promptpotter.application.scoring.formula import (
    DIALS_KEY,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.metrics import INVALID_SCORES, compute_composite_fitness
from promptpotter.application.scoring.query_loop import ArmSlot
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.pipeline_overlay import overlay_is_locked_axis_only
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import (
    ArmOutcome,
    ReferenceReading,
    ScoredCandidate,
    candidate_label,
)
from promptpotter.domain.run_records import (
    OPERATOR_ORIGIN_SOURCES,
    CandidateMintedRecord,
    CycleSeed,
    OriginSource,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    is_dataset_dir,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)
from promptpotter.shared.measurement_context import (
    NO_ROUND_SLOT,
    MeasuredCandidate,
    MeasurementRole,
)

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import CellSheet


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignOrigin",
    "DatasetIndexEntry",
    "OriginEntry",
    "canonical_origin_campaign",
    "establish_campaign_origin",
    "list_datasets",
    "list_origins",
    "prospective_origin_id",
    "rescore_parent",
    "resolve_origin_opt_search_point",
    "try_inherit_fork_origin",
]


async def rescore_parent(
    cycle: Cycle,
    scoring_set: list[Sample],
    *,
    force_fresh: bool = False,
) -> ReferenceReading:
    session = cycle.session
    tr = cycle.tracking
    assert tr.current_sp is not None
    scored = await score_search_point(
        tr.current_sp,
        scoring_set,
        session,
        label=MeasurementRole.PARENT,
        sample_index=cycle.sample_index,
        # Ticked, not silenced: a silent whole-panel walk serves `between_samples` and reads as hung.
        slot=ArmSlot(NO_ROUND_SLOT, 0, cycle.opt_sp.id),
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=cycle.opt_sp.id,
            label=cycle.rounds[-1].label,
            role=MeasurementRole.PARENT,
        ),
        force_fresh=force_fresh,
    )
    # A partial pass is kept: `read_pair` pairs each candidate on the cells both scored.
    if scored.stopped is not None:
        logger.warning(
            "Round parent %s stopped after %d/%d cells (%s); its floor covers only those.",
            cycle.rounds[-1].label,
            len(scored.sheet),
            len(scoring_set),
            scored.stopped,
        )
    return ReferenceReading(
        opt_sp=cycle.opt_sp,
        results=scored.sheet,
        # The gateway's OWN scores: re-running `compute_composite_fitness` drops the evaluators.
        report=build_score_report(
            cycle.opt_sp,
            (),
            None,
            scored.scores,
            scored.sheet.cells,
            scoring_set,
            # The parent INDIVIDUAL's label, off the round it won — it reaches disk.
            label=cycle.rounds[-1].label,
            sp_hash=tr.current_sp.sp_hash(session.pipeline_schema),
            outcome=walk_outcome(scored),
            resolved_pipeline_params=tr.current_sp.config_params,
        ),
    )


class CampaignOrigin(NamedTuple):
    resolved_origin: OptSearchPoint | None
    report: ScoredCandidate
    origin_results: CellSheet | None
    framing: TaskDecomposition
    # ``None`` where the campaign declared no dials to lock.
    locked_scoring: dict[str, str] | None = None


def lock_criterion(
    session: Session, campaign_config: CampaignConfig, origin_rows: CellSheet
) -> dict[str, str] | None:
    """Locked once: ``pipeline_resolve.py::resolve_campaign_config`` reads the record back."""
    block = campaign_config.scoring
    if not isinstance(block, dict) or DIALS_KEY not in block:
        return None
    formula = realize_dials(parse_dials(block[DIALS_KEY]), origin_anchors(origin_rows))
    locked = {k: v for k, v in block.items() if k != DIALS_KEY} | {"per_cell": formula}
    if (ledger := session.state.ledger) is not None:
        ledger.append(ScoringLockedRecord(declared=dict(block), locked=locked))
    return locked


def try_inherit_fork_origin(
    session: Session,
    seed: CycleSeed | None,
    *,
    resolved_origin: OptSearchPoint,
    sp: JobSearchPoint,
    framing: TaskDecomposition,
) -> CampaignOrigin | None:
    """Re-scoring a no-edit or model/provider-only fork re-rolls C0 and the lineage jumps."""
    if seed is None:
        return None
    # A non-locked param edit changes what the origin measures → re-score.
    if seed.pipeline_overlay and not overlay_is_locked_axis_only(seed.pipeline_overlay):
        return None

    store = session.store.campaigns
    index = store.load(session.hop)
    if index is None or index.fork is None or index.parent_cycle_id is None:
        return None
    parent = index.parent_cycle_id
    from_round = index.fork.from_round
    from_candidate_id = index.fork.from_candidate_id
    if from_round is None or from_candidate_id is None:
        return None

    parent_round = closed_round(
        session.store,
        CycleHop(campaign_id=session.campaign_id, cycle_id=parent),
        from_round,
        session.scoring.require_scorer(),
    )
    if parent_round is None:
        return None
    cand = next(
        (c for c in parent_round.candidate_scores if c.candidate_id == from_candidate_id),
        None,
    )
    if cand is None:
        return None

    # An operator edit changes the render or the shots → re-score.
    if resolved_origin.prompt_field_dict() != cand.prompt_fields:
        return None

    origin_acc = cand.accuracy
    inherited_results = (
        parent_round.all_candidate_results.get(from_candidate_id) or parent_round.results
    )
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
    # The OSP object, not a prompt-field dict, so the inherited C0 keeps the seed's lineage.
    return CampaignOrigin(
        resolved_origin=resolved_origin,
        report=cand.model_copy(
            update={
                "candidate_id": resolved_origin.id,
                "label": candidate_label(0, 0),
                "sp_hash": sp.sp_hash(session.pipeline_schema),
                "resolved_pipeline_params": sp.config_params,
            }
        ),
        origin_results=inherited_results,
        framing=framing,
    )


_SEED_ORIGIN_LINEAGE = {
    OriginSource.FORK_SEED: "Operator-steered fork — edited searchpoint as origin",
    OriginSource.CAMPAIGN_ORIGIN: "Fresh campaign minted from a chosen prior origin",
}
assert set(_SEED_ORIGIN_LINEAGE) == OPERATOR_ORIGIN_SOURCES


def resolve_origin_opt_search_point(
    prompt_node_names: list[str] | None = None,
    dataset_dir: Path | None = None,
    *,
    seed: CycleSeed | None = None,
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema,
) -> OptSearchPoint:
    names = prompt_node_names or []
    origin: OptSearchPoint | None = None

    if seed is not None and seed.origin_prompt_fields:
        origin = OptSearchPoint.from_prompt_fields(seed.origin_prompt_fields).as_origin(
            changes_description=_SEED_ORIGIN_LINEAGE[seed.origin_source]
        )
    elif dataset_dir is not None and names and has_dataset_prompts(dataset_dir):
        for node_name in names:
            try:
                template = load_node_prompt(dataset_dir, node_name, "default")
            except FileNotFoundError:
                continue
            origin = OptSearchPoint.from_prompt_fields(template.prompt_fields()).as_origin(
                changes_description=f"Origin from {dataset_dir}/prompts/ ({node_name})"
            )
            break

    if origin is None:
        origin = OptSearchPoint().as_origin(
            changes_description="Origin (no prompt node active — param-only optimization)"
        )

    return origin.configured(pipeline_params, schema)


async def establish_campaign_origin(
    session: Session,
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    seed: CycleSeed | None,
    listener: RunCallbacks,
) -> CampaignOrigin:
    resolved_origin = resolve_origin_opt_search_point(
        prompt_node_names=session.pipeline_schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
        seed=seed,
        pipeline_params=session.pipeline_params,
        schema=session.pipeline_schema,
    )
    # The SAME read identity uses; handed in, it could be a value the cycle id never saw.
    framing = campaign_framing(session.store, campaign_config, session.dataset_name)
    pipeline_schema = session.pipeline_schema
    sp = resolved_origin.to_job_search_point(
        schema=pipeline_schema, framing=framing, demo=session.scoring.require_partition().demo
    )
    inherited = try_inherit_fork_origin(
        session, seed, resolved_origin=resolved_origin, sp=sp, framing=framing
    )
    if inherited is not None:
        return inherited

    # Never infer "no program" from an empty searchpoint: the L4 outer origin's is empty too.
    if not dataset:
        if isinstance(campaign_config.scoring, dict) and DIALS_KEY in campaign_config.scoring:
            raise PayloadInvalidError(
                "the campaign declares scoring dials, and dials are anchored on the origin's "
                "measured cells — this origin measures none. Declare 'per_cell' instead.",
                code="pipeline_config_invalid",
            )
        return CampaignOrigin(
            resolved_origin=resolved_origin,
            # `total=0` is the no-evidence marker; a bare 0.0 reads as a real floor of zero.
            report=build_score_report(
                resolved_origin,
                (),
                None,
                INVALID_SCORES,
                [],
                [],
                label=candidate_label(0, 0),
                sp_hash=sp.sp_hash(pipeline_schema),
                outcome=ArmOutcome.MEASURED,
                resolved_pipeline_params=sp.config_params,
            ),
            origin_results=None,
            framing=framing,
        )

    scoring_set = sample_dataset(dataset, campaign_config.sp_budget_origin)

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    elif session.backend_client.execution == "remote_http":
        logger.warning("No session terms available — /matches calls will fail.")

    listener.on_phase(CampaignPhase.ORIGIN, "enter", round=0)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            CandidateMintedRecord(
                round=0,
                idx=0,
                candidate_id=resolved_origin.id,
                label=candidate_label(0, 0),
                lineage=resolved_origin.lineage,
            )
        )

    listener.announce_candidate(
        0,
        0,
        1,
        opt_sp=resolved_origin,
        resolved_pipeline_params=sp.config_params,
        sample_order=[s.id for s in scoring_set],
    )

    try:
        # A transient abort must not bank a corrupted floor: re-score once fresh. A pass that stays
        # partial is graded by coverage (`origin_gate`), which is why nothing here reads `stopped`.
        for attempt in range(2):
            scored = await score_search_point(
                sp,
                scoring_set,
                session,
                label=MeasurementRole.ORIGIN,
                measured=None,
                force_fresh=attempt > 0,
                slot=ArmSlot(0, 1, resolved_origin.id),
            )
            if not is_transient_scoring_abort(scored.signal):
                break
            logger.warning(
                "Origin scoring hit a transient transport abort — re-scoring once fresh."
            )
        sheet, scores = scored.sheet, scored.scores
        locked_scoring = lock_criterion(session, campaign_config, sheet)
        if locked_scoring is not None:
            # Re-GRADED under the criterion these rows just anchored; nothing is re-measured.
            populate_session_scoring(
                session,
                campaign_config.model_copy(update={"scoring": locked_scoring}),
                source=RunSource.ORIGIN,
            )
            sheet = session.scoring.require_scorer().sheet(cell.facts for cell in sheet)
            scores = compute_composite_fitness(sheet, pipeline_schema)
        # Both what the ledger receives and round 0's row — never re-derive either from these rows.
        report = build_score_report(
            resolved_origin,
            (),
            None,
            scores,
            sheet.cells,
            scoring_set,
            label=candidate_label(0, 0),
            sp_hash=sp.sp_hash(pipeline_schema),
            outcome=walk_outcome(scored),
            resolved_pipeline_params=sp.config_params,
        )
        listener.on_candidate_scored(0, 1, report)
    finally:
        listener.on_phase(CampaignPhase.ORIGIN, "exit", round=0)

    return CampaignOrigin(
        resolved_origin=resolved_origin,
        report=report,
        origin_results=sheet,
        framing=framing,
        locked_scoring=locked_scoring,
    )


def prospective_origin_id(stores: Stores, dataset_dir: Path, dataset_name: str) -> str | None:
    """Not ``resolve_pipeline_for_campaign``: a dataset with no campaign has no manifest to freeze."""
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
            pipeline_params=base_pp,
            schema=schema,
        )
        items = resolve_dataset_items(stores, dataset_name)
        if not items:
            return None
        partition = partition_bank(bank_samples(items), cfg.dataset_split)
        return build_origin_cycle_id(
            opt_sp,
            schema,
            list(partition.search),
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
        # A SURVEY: an unreadable neighbour drops itself, never the list. Direct reads still raise.
        logger.exception("origins: prospective origin id failed for %s", dataset_name)
        return None


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "install"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``install`` = content that ships with the product at ``datasets/{slug}/`` "
            "(benchmarks, demos, ``promptpotter-self``) — tracked in git, so readable by "
            "anyone using the install. A ``yours`` slug shadows an ``install`` one."
        ),
    )
    n_samples: int | None = Field(
        default=None,
        description=(
            "Sample bank size from ``cache.json``; ``null`` when the cache has not been "
            "materialized, which is not the same as a dataset holding zero usable rows."
        ),
    )


def list_datasets(stores: Stores) -> list[DatasetIndexEntry]:
    return [
        DatasetIndexEntry(name=ref.name, title=ref.title, tier=ref.tier, n_samples=ref.n_samples)
        for ref in list_readable_datasets(stores)
    ]


class OriginEntry(StrictModel):
    origin_id: str = Field(
        description="Origin content identity — a campaign's root_content_hash (or the "
        "dataset's prospective origin hash for a prepared origin)"
    )
    dataset_name: str = Field(description="Dataset this origin starts from")
    label: str = Field(default="", description="Operator-supplied label, if any")
    n_samples: int | None = Field(
        default=None, description="Dataset sample count; ``null`` if unmaterialized"
    )
    n_campaigns: int = Field(
        default=0,
        description="Active campaigns minted from this origin (0 = prepared, not yet run)",
    )
    origin_accuracy: float | None = Field(
        default=None,
        description="The origin's C0 score, as the root cycle's standing round 0 closed",
    )
    prepared: bool = Field(
        default=False, description="True = a ready dataset config with no campaign yet"
    )
    created_at: str = Field(default="", description="ISO 8601 — earliest campaign on this origin")


def _campaigns_by_origin(stores: Stores) -> dict[str, list[Campaign]]:
    """NOT owner-filtered: a CLI mint's owner id differs from a browser session's in ONE tenant."""
    by_origin: dict[str, list[Campaign]] = {}
    for campaign in stores.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None):
        by_origin.setdefault(campaign.origin_id, []).append(campaign)
    return by_origin


def _canonical(group: list[Campaign]) -> Campaign:
    return min(group, key=lambda c: c.created_at)


def canonical_origin_campaign(stores: Stores, origin_id: str) -> Campaign | None:
    """``None`` for a *prepared* origin, which has no campaign yet."""
    group = _campaigns_by_origin(stores).get(origin_id)
    return _canonical(group) if group else None


def _campaign_backed_origins(stores: Stores, n_samples: dict[str, int | None]) -> list[OriginEntry]:
    out: list[OriginEntry] = []
    for origin_id, group in _campaigns_by_origin(stores).items():
        canonical = _canonical(group)
        try:
            readable_dataset_dir(stores, canonical.dataset_name)
        except DatasetAccessError:
            logger.info(
                "origins: skipping origin %s — dataset %r no longer resolves",
                origin_id,
                canonical.dataset_name,
            )
            continue
        # The BEST across the origin's campaigns: origin scoring is nondeterministic at the backend.
        scores = [
            origin.accuracy
            for c in group
            if (index := stores.campaigns.load(c.root_hop)) is not None
            for origin in index.rounds[:1]
            if origin.round == 0 and origin.accuracy is not None
        ]
        out.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=canonical.dataset_name,
                label=canonical.label,
                n_samples=n_samples.get(canonical.dataset_name),
                n_campaigns=len(group),
                origin_accuracy=max(scores) if scores else None,
                created_at=canonical.created_at,
            )
        )
    return sorted(out, key=lambda o: o.created_at, reverse=True)


def list_origins(stores: Stores) -> list[OriginEntry]:
    datasets = list_readable_datasets(stores)
    campaign_backed = _campaign_backed_origins(stores, {r.name: r.n_samples for r in datasets})
    taken = {o.origin_id for o in campaign_backed}
    prepared: list[OriginEntry] = []
    for ref in datasets:
        if ref.tier != "yours" or not ref.n_samples:
            continue
        dataset_dir = stores.tenant_datasets.dataset_dir(ref.name)
        if not has_dataset_prompts(dataset_dir) or not is_dataset_dir(dataset_dir):
            continue
        origin_id = prospective_origin_id(stores, dataset_dir, ref.name)
        if origin_id is None or origin_id in taken:
            continue
        prepared.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=ref.name,
                label=ref.title or "",
                n_samples=ref.n_samples,
                prepared=True,
            )
        )
    return [*prepared, *campaign_backed]
