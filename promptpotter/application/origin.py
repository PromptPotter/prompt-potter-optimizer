from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.bench.cycle import ReadArm
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.datasets.loaders import sample_dataset
from promptpotter.application.datasets.prompts import declared_origin_prompts
from promptpotter.application.initialization.scoring_scope import scorer_setup
from promptpotter.application.initialization.session import Session
from promptpotter.application.scoring.arm_states import (
    conclude,
    measuring,
)
from promptpotter.application.scoring.closed_rounds import closed_round
from promptpotter.application.scoring.formula.compiler import (
    DIALS_KEY,
    campaign_terms,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.search_point_scorer import ScoredWalk
from promptpotter.domain.arms import ArmSlot, Concluded, Proposed
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import overlay_is_locked_axis_only
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.records.run_records import (
    OPERATOR_ORIGIN_SOURCES,
    CycleSeed,
    OriginSource,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.dataset_access import ORIGIN_FILE, ResolvedDataset
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle


logger = logging.getLogger(__name__)


ORIGIN_SLOT = ArmSlot(round=0, idx=0, total=1)


class CampaignOrigin(NamedTuple):
    origin: ReadArm
    framing: TaskDecomposition


def lock_criterion(cycle: Cycle, read: ReadArm, ledger: CycleEventLog) -> ReadArm:
    """Locked once, at round 0's close, on the rows its walk read; *read* comes back re-GRADED
    under it, nothing re-measured. ``pipeline_resolve.py::cycle_config`` reads the record back."""
    session = cycle.session
    block = cycle.config.scoring
    if not isinstance(block, dict) or DIALS_KEY not in block:
        return read
    terms = campaign_terms(session.backend_client.connector.name)
    formula = realize_dials(
        parse_dials(block[DIALS_KEY], terms), origin_anchors(read.rows, terms), terms
    )
    locked = {k: v for k, v in block.items() if k != DIALS_KEY} | {"per_cell": formula}
    ledger.append(ScoringLockedRecord(declared=dict(block), locked=locked))
    cycle.config = cycle.config.model_copy(update={"scoring": locked})
    session.state.scoring = scorer_setup(
        session, cycle.config, partition=session.state.scoring.partition
    )
    # The scorer's id keys the difficulty ruler, so round 0 is read on the locked one's.
    cycle.reopen_difficulty()
    sheet = session.state.scoring.scorer.sheet(cell.facts for cell in read.rows)
    return ReadArm(read.key, read.state, sheet)


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
    if (
        index is None
        or index.fork is None
        or (parent := index.parent_cycle_id) is None
        or index.forked_at_offset is None
    ):
        return None
    named = index.fork.from_candidate
    parent_hop = CycleHop(campaign_id=session.campaign_id, cycle_id=parent)
    parent_round = closed_round(
        session.store,
        parent_hop,
        named.round,
        session.state.scoring.scorer,
        # The parent as the fork cut it: its line through the FORK_CUT, which the mint names.
        moment=Moment(store.ledger(parent_hop), index.forked_at_offset - 1),
    )
    if parent_round is None:
        return None
    key = named.arm
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
        "fitness=%s, %d per-sample rows — skipping origin re-score, straight to L1",
        session.cycle_id,
        key,
        parent,
        named.round,
        fold.fitness,
        len(inherited_results),
    )
    # The OSP object, not a prompt-field dict, so the inherited C0 keeps the seed's lineage.
    inherited = cand.model_copy(
        update={
            "role": MeasurementRole.ORIGIN,
            "slot": ORIGIN_SLOT,
            "individual": resolved_origin,
            "sp_hash": sp.sp_hash(session.pipeline_schema),
            "resolved_params": sp.config_params.dump(),
        }
    )
    return CampaignOrigin(
        origin=ReadArm(ArmKey(resolved_origin.id), inherited, inherited_results),
        framing=framing,
    )


def standing_origin(session: Session, resolved_origin: OptSearchPoint) -> ReadArm | None:
    """A line whose round 0 stands launches on it, never a second pass: its holes are the repair's to re-send."""
    origin = closed_round(session.store, session.hop, 0, session.state.scoring.scorer, moment=None)
    head = None if origin is None else origin.head
    if origin is None or origin.election is None or head is None:
        return None
    if head.individual.id != resolved_origin.id:
        return None
    key = origin.election.ended_on
    return ReadArm(key, head, origin.rows(key))


_SEED_ORIGIN_LINEAGE = {
    OriginSource.FORK_SEED: "Operator-steered fork — edited searchpoint as origin",
    OriginSource.CAMPAIGN_ORIGIN: "Fresh campaign minted from a chosen prior origin",
}
assert set(_SEED_ORIGIN_LINEAGE) == OPERATOR_ORIGIN_SOURCES


def resolve_origin_opt_search_point(
    dataset: ResolvedDataset | None = None,
    *,
    seed: CycleSeed | None = None,
    pipeline_params: PipelineParams,
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
    elif dataset is not None and (
        authored := declared_origin_prompts(dataset, schema.prompt_nodes())
    ):
        origin = OptSearchPoint(prompts=authored).as_origin(
            changes_description=f"Origin from the dataset's {ORIGIN_FILE} ({', '.join(authored)})"
        )
    if origin is None:
        origin = OptSearchPoint().as_origin(
            changes_description="Origin (no starting prompt — param-only optimization)"
        )
    return origin.configured(pipeline_params, schema)


def seat_campaign_origin(
    session: Session,
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    cycle: CycleConfig,
) -> CampaignOrigin:
    """Round 0's seat, never its walk: inherited, standing or unmeasured. Round 0's door walks it
    after INIT closes (`runner/loop.py::open_origin_round`)."""
    resolved_origin = resolve_origin_opt_search_point(
        dataset=session.dataset,
        seed=cycle.origin,
        pipeline_params=session.pipeline_params,
        schema=session.pipeline_schema,
    )
    # The SAME read identity uses; handed in, it could be a value the cycle id never saw.
    framing = campaign_framing(session.store, campaign_config, session.dataset)
    sp = session.points(framing).job(resolved_origin)
    inherited = try_inherit_fork_origin(
        session, cycle.own_seed, resolved_origin=resolved_origin, sp=sp, framing=framing
    )
    if inherited is not None:
        return inherited
    if (standing := standing_origin(session, resolved_origin)) is not None:
        return CampaignOrigin(origin=standing, framing=framing)
    # Never infer "no program" from an empty searchpoint: the L4 outer origin's is empty too.
    dials = isinstance(campaign_config.scoring, dict) and DIALS_KEY in campaign_config.scoring
    if not dataset and dials:
        raise PayloadInvalidError(
            "the campaign declares scoring dials, and dials are anchored on the origin's "
            "measured cells — this origin measures none. Declare 'per_cell' instead.",
            code="pipeline_config_invalid",
        )
    scoring_set = sample_dataset(dataset, campaign_config.sp_budget_origin) if dataset else []
    proposed = Proposed(role=MeasurementRole.ORIGIN, slot=ORIGIN_SLOT, individual=resolved_origin)
    unmeasured = measuring(
        proposed,
        sp,
        session.pipeline_schema,
        expected=len(scoring_set),
        sample_order=[s.id for s in scoring_set],
    )
    # The fold of no cell: `total=0`, never a bare 0.0 that reads as a real floor of zero.
    nothing = ScoredWalk(
        NO_CELLS,
        compute_composite_fitness(NO_CELLS, session.state.scoring.scorer, session.pipeline_schema),
        signal=None,
        stopped=None,
    )
    seated = conclude(unmeasured, nothing, round_num=0)
    return CampaignOrigin(
        origin=ReadArm(ArmKey(resolved_origin.id), seated, NO_CELLS), framing=framing
    )
