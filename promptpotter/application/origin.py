from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.loaders import sample_dataset
from promptpotter.application.datasets.prompts import declared_origin_prompts
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import ReadArm
from promptpotter.application.scoring.arm_states import (
    conclude,
    is_transient_scoring_abort,
    measuring,
)
from promptpotter.application.scoring.closed_rounds import closed_round
from promptpotter.application.scoring.formula.compiler import (
    DIALS_KEY,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.search_point_scorer import ScoredWalk, score_search_point
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.pipeline_overlay import overlay_is_locked_axis_only
from promptpotter.domain.results import ArmSlot, Concluded, Proposed
from promptpotter.domain.run_records import (
    OPERATOR_ORIGIN_SOURCES,
    CycleSeed,
    OriginSource,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure.store.dataset_access import ORIGIN_FILE
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import CellSheet


logger = logging.getLogger(__name__)


ORIGIN_SLOT = ArmSlot(round=0, idx=0, total=1)


class CampaignOrigin(NamedTuple):
    origin: ReadArm
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
    index = store.load(session.hop, moment=None)
    if index is None or index.fork is None or index.parent_cycle_id is None:
        return None
    parent = index.parent_cycle_id
    from_round = index.fork.from_round
    from_candidate_id = index.fork.from_candidate_id
    if from_round is None or from_candidate_id is None:
        return None

    parent_round = closed_round(
        session.store,
        session.hop,
        from_round,
        session.state.scoring.require_scorer(),
        # The fork's chain at its mint, its first record: the parent as cut, before any own close.
        moment=Moment(store.ledger(session.hop), 0),
    )
    if parent_round is None:
        return None
    key = ArmKey(from_candidate_id)
    cand = parent_round.arms.get(key)
    fold = parent_round.folds.get(key)
    if not isinstance(cand, Concluded) or fold is None:
        return None

    # An operator edit changes the render or the shots → re-score.
    if resolved_origin.prompts != cand.individual.prompts:
        return None

    inherited_results = parent_round.rows(key)
    logger.info(
        "Fork %s: inheriting C0 from branch-point candidate %s (parent %s round %d) "
        "acc=%s, %d per-sample rows — skipping origin re-score, straight to L1",
        session.cycle_id,
        from_candidate_id,
        parent,
        from_round,
        fold.accuracy,
        len(inherited_results),
    )
    # The OSP object, not a prompt-field dict, so the inherited C0 keeps the seed's lineage.
    inherited = cand.model_copy(
        update={
            "role": MeasurementRole.ORIGIN,
            "slot": ORIGIN_SLOT,
            "individual": resolved_origin,
            "sp_hash": sp.sp_hash(session.pipeline_schema),
            "resolved_params": sp.config_params or {},
        }
    )
    return CampaignOrigin(
        origin=ReadArm(ArmKey(resolved_origin.id), inherited, inherited_results),
        framing=framing,
    )


_SEED_ORIGIN_LINEAGE = {
    OriginSource.FORK_SEED: "Operator-steered fork — edited searchpoint as origin",
    OriginSource.CAMPAIGN_ORIGIN: "Fresh campaign minted from a chosen prior origin",
}
assert set(_SEED_ORIGIN_LINEAGE) == OPERATOR_ORIGIN_SOURCES


def resolve_origin_opt_search_point(
    dataset_dir: Path | None = None,
    *,
    seed: CycleSeed | None = None,
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema,
) -> OptSearchPoint:
    """Seed → every prompt node's starting prompt in ``origin.yaml`` → empty."""
    origin: OptSearchPoint | None = None
    if seed is not None and seed.origin_prompts:
        # A step bearing no prompt reads none: a check-in seeds every active step alike.
        borne = {node.name for node in schema.prompt_nodes()}
        prompts = {n: p for n, p in seed.origin_prompts.items() if n in borne}
        origin = OptSearchPoint(prompts=prompts).as_origin(
            changes_description=_SEED_ORIGIN_LINEAGE[seed.origin_source]
        )
    elif dataset_dir is not None and (
        authored := declared_origin_prompts(dataset_dir, schema.prompt_nodes())
    ):
        origin = OptSearchPoint(prompts=authored).as_origin(
            changes_description=f"Origin from {dataset_dir}/{ORIGIN_FILE} ({', '.join(authored)})"
        )
    if origin is None:
        origin = OptSearchPoint().as_origin(
            changes_description="Origin (no starting prompt — param-only optimization)"
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
        dataset_dir=None if session.dataset is None else session.dataset.directory,
        seed=seed,
        pipeline_params=session.pipeline_params,
        schema=session.pipeline_schema,
    )
    # The SAME read identity uses; handed in, it could be a value the cycle id never saw.
    framing = campaign_framing(session.store, campaign_config, session.dataset)
    pipeline_schema = session.pipeline_schema
    sp = session.points(framing).job(resolved_origin)
    inherited = try_inherit_fork_origin(
        session, seed, resolved_origin=resolved_origin, sp=sp, framing=framing
    )
    if inherited is not None:
        return inherited
    proposed = Proposed(role=MeasurementRole.ORIGIN, slot=ORIGIN_SLOT, individual=resolved_origin)
    key = ArmKey(resolved_origin.id)

    # Never infer "no program" from an empty searchpoint: the L4 outer origin's is empty too.
    if not dataset:
        if isinstance(campaign_config.scoring, dict) and DIALS_KEY in campaign_config.scoring:
            raise PayloadInvalidError(
                "the campaign declares scoring dials, and dials are anchored on the origin's "
                "measured cells — this origin measures none. Declare 'per_cell' instead.",
                code="pipeline_config_invalid",
            )
        # The fold of no cell: `total=0`, never a bare 0.0 that reads as a real floor of zero.
        unmeasured = measuring(proposed, sp, pipeline_schema, expected=0)
        nothing = ScoredWalk(
            NO_CELLS,
            compute_composite_fitness(NO_CELLS, pipeline_schema),
            signal=None,
            stopped=None,
        )
        return CampaignOrigin(
            origin=ReadArm(key, conclude(unmeasured, nothing), NO_CELLS), framing=framing
        )

    scoring_set = sample_dataset(dataset, campaign_config.sp_budget_origin)

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    elif session.backend_client.execution == "remote_http":
        logger.warning("No session terms available — /matches calls will fail.")

    listener.on_phase(CampaignPhase.ORIGIN, "enter", round=0)

    arm = measuring(
        proposed,
        sp,
        pipeline_schema,
        expected=len(scoring_set),
        sample_order=[s.id for s in scoring_set],
    )
    listener.on_arm(0, key, proposed)
    listener.on_arm(0, key, arm)

    try:
        # A transient abort must not bank a corrupted floor: re-score once fresh. A pass that stays
        # partial is graded by coverage (`origin_gate`), which is why nothing here reads `stopped`.
        for attempt in range(2):
            scored = await score_search_point(
                sp,
                scoring_set,
                session,
                label=MeasurementRole.ORIGIN,
                measured=MeasuredPass(resolved_origin.id, MeasurementRole.ORIGIN),
                force_fresh=attempt > 0,
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
                partition=session.state.scoring.require_partition(),
            )
            sheet = session.state.scoring.require_scorer().sheet(cell.facts for cell in sheet)
            scores = compute_composite_fitness(sheet, pipeline_schema)
        # Both what the ledger receives and round 0's arm — never re-derive either from these rows.
        state = conclude(arm, replace(scored, sheet=sheet, scores=scores))
        listener.on_arm(0, key, state)
    finally:
        listener.on_phase(CampaignPhase.ORIGIN, "exit", round=0)

    return CampaignOrigin(
        origin=ReadArm(key, state, sheet), framing=framing, locked_scoring=locked_scoring
    )
