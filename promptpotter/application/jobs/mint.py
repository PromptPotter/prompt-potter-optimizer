from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.task_context import (
    campaign_framing,
    commit_task_framing,
    committed_task_context,
)
from promptpotter.application.campaign_config import (
    apply_cycle_seed,
    declare_run,
    effective_config,
)
from promptpotter.application.datasets.draft_campaign import mint_draft_id
from promptpotter.application.free_tier import outside_free_tier, refuse_outside_free_tier
from promptpotter.application.jobs.quota import on_free_tier, paid_call
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import MintFacts
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline, defaults_drift
from promptpotter.application.preflight import (
    check_search_split_holds_round,
    refuse_below_reasoning_floor,
    refuse_unfit_pipeline,
    refuse_unpriced,
    unpriced_routes,
)
from promptpotter.application.run_observers import declare_run_wiring
from promptpotter.application.runner.campaign_ids import (
    mint_campaign_id,
    origin_content_hash,
    root_cycle_id_of,
)
from promptpotter.application.scoring.formula.compiler import split_scoring_block
from promptpotter.application.scoring.formula.matchers import matcher_of
from promptpotter.domain.bench import BankPartition, PartitionRecord, partition_bank
from promptpotter.domain.campaign import (
    Arm,
    Campaign,
    Compare,
    HeadToHeadRecord,
    Purpose,
    RunDeclaration,
    SharedPart,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.results import PointContext
from promptpotter.domain.run_records import CycleSeed, DefaultsDrift, OriginSource
from promptpotter.domain.treatment import Treatment
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.infrastructure.store.dataset_access import (
    declared_backend_type,
    read_dataset_framing,
)
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store.stores import Stores


logger = logging.getLogger(__name__)


def refuse_unrunnable(
    campaign_config: CampaignConfig,
    dataset: Sequence[Sample],
    pipeline_params: Mapping[str, Any],
    schema: PipelineSchema,
    *,
    free: bool,
) -> BankPartition:
    """Every refusal a run owes, before anything is paid or written; an upload's Start asks it of the draft."""
    refuse_unfit_pipeline(campaign_config, schema)
    partition = partition_bank(
        dataset, campaign_config.dataset_split, seed=campaign_config.run_seed
    )
    refusal = check_search_split_holds_round(campaign_config, len(partition.search))
    if refusal is not None:
        raise PayloadInvalidError(refusal, code="search_split_below_round")
    per_sample = split_scoring_block(campaign_config.scoring, judge_instrument=None).per_sample
    mint = MintFacts(partition.demo, schema, matcher_of(per_sample))
    if pools := select_optimizer(campaign_config.optimization).pool_refusals(mint=mint):
        raise PayloadInvalidError("; ".join(pools), code="optimizer_overlay")
    refuse_below_reasoning_floor(campaign_config, pipeline_params)
    refuse_unpriced(unpriced_routes(campaign_config, pipeline_params, schema))
    if free:
        refuse_outside_free_tier(outside_free_tier(campaign_config, pipeline_params, schema))
    return partition


@dataclass(frozen=True)
class CyclePlan:
    """What a mint writes and a resume checks, every refusal already asked of what the cycle runs."""

    declaration: RunDeclaration
    schema: PipelineSchema
    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
    # What the origin's render splices in, which a mint's check-in may commit after the refusals.
    framing: TaskDecomposition
    treatment: Treatment
    partition: BankPartition
    # The draw every later read of the cycle restores, written by the seam that mints it.
    drawn: PartitionRecord
    # What the cycle runs under: a mint writes it, a minted cycle's ledger holds it.
    seed: CycleSeed | None
    # Configured as the declaration runs; a mint binds it to the cycle.
    session: Session

    @functools.cached_property
    def origin_hash(self) -> str:
        """The origin's content — merged params, framing, the search rows: what two declarations on one origin share."""
        points = PointContext(schema=self.schema, framing=self.framing, demo=self.partition.demo)
        return origin_content_hash(self.origin, points, list(self.partition.search))

    @property
    def cycle_id(self) -> str:
        return root_cycle_id_of(self.origin_hash, self.declaration)


@dataclass(frozen=True)
class MintedCycle:
    cycle_id: str
    campaign_id: str
    campaign_config: CampaignConfig
    session: Session


def declare(
    session: Session, campaign_config: CampaignConfig, *, purpose: Purpose
) -> RunDeclaration:
    """Every door's ONE declaration: a compare run searches prompts alone and is benched."""
    if isinstance(purpose, Compare):
        campaign_config = campaign_config.model_copy(
            update={
                "optimizer_narrowing": _prompt_axes_only(session, campaign_config),
                "bench_trigger": (
                    "at_end"
                    if campaign_config.bench_trigger == "manual"
                    else campaign_config.bench_trigger
                ),
            }
        )
    assert session.dataset is not None, "a declaration names the dataset the session opened"
    return declare_run(
        campaign_config,
        purpose=purpose,
        dataset_hash=session.bank.dataset_hash,
        connector=declared_backend_type(session.dataset.directory),
        bench_defaults_pin=bench_defaults_pin(),
    )


def resolve_cycle_plan(
    session: Session,
    declaration: RunDeclaration,
    dataset: Sequence[Sample],
    *,
    origin_override: dict[str, NodePrompt] | None = None,
) -> CyclePlan:
    """Bound to a cycle, what it RUNS: its root's origin under its own seed, which the configure lays."""
    campaign_config = effective_config(declaration)
    schema = session.pipeline_schema
    configured = configure_and_apply_pipeline(session, campaign_config)
    pipeline_params = configured.pipeline_params
    hop = session.hop if session.cycle_id else None
    runs_under: CycleSeed | None = None
    if hop is not None:
        assert origin_override is None, "a minted cycle's origin is its campaign root's seed"
        campaigns = session.store.campaigns
        campaign = campaigns.load_campaign(hop.campaign_id)
        assert campaign is not None, "a hop names a minted campaign"
        root_seed = campaigns.read_cycle_seed(campaign.root_hop)
        origin_override = (root_seed.origin_prompts or None) if root_seed else None
        runs_under = campaigns.read_cycle_seed(hop)
    partition = refuse_unrunnable(
        apply_cycle_seed(campaign_config, runs_under),
        dataset,
        pipeline_params,
        schema,
        free=on_free_tier(session.store),
    )
    seed = (
        CycleSeed(origin_prompts=origin_override, origin_source=OriginSource.CAMPAIGN_ORIGIN)
        if origin_override
        else None
    )
    return CyclePlan(
        declaration=declaration,
        schema=schema,
        pipeline_params=pipeline_params,
        origin=resolve_origin_opt_search_point(
            dataset_dir=None if session.dataset is None else session.dataset.directory,
            seed=seed,
            pipeline_params=pipeline_params,
            schema=schema,
        ),
        framing=campaign_framing(session.store, campaign_config, session.dataset),
        treatment=select_optimizer(campaign_config.optimization).treatment(),
        partition=partition,
        # Slots in the session's bank, whichever rows of it the caller hands in.
        drawn=partition.record(dataset_hash=session.bank.dataset_hash),
        seed=seed if hop is None else runs_under,
        session=configured,
    )


class ConfigDriftError(ConflictError):
    code = "config_drift"

    def __init__(
        self, campaign: Campaign, *, current_hash: str, drift: DefaultsDrift | None
    ) -> None:
        self.dataset_name = campaign.dataset_name
        cause = (
            f"what its declaration is a delta over moved since the mint ({', '.join(drift.moved())})"
            if drift is not None
            else "the dataset, its origin prompts or its framing changed, not its declaration"
        )
        super().__init__(
            f"campaign {campaign.campaign_id} no longer resolves to the origin it was minted on "
            f"(stored {campaign.root_content_hash}, now {current_hash}): {cause}. Its "
            "measurements may not apply. Revert the edit, or start a fresh campaign on "
            f"{campaign.dataset_name} — this one is preserved.",
            details={
                "campaign_id": campaign.campaign_id,
                "stored_hash": campaign.root_content_hash,
                "current_hash": current_hash,
                "defaults_pin_moved": drift is not None,
            },
        )


def refuse_drifted_resume(session: Session, campaign: Campaign, dataset: Sequence[Sample]) -> None:
    """*session* is bound to the cycle the resume runs."""
    plan = resolve_cycle_plan(session, campaign.declaration, dataset)
    hop = session.hop
    if campaign.root_content_hash == plan.origin_hash:
        return
    # A seed that steers the pipeline declared its own values at the cut, so it runs under them.
    if plan.seed is not None and plan.seed.pipeline_overlay:
        logger.info(
            "resume %s/%s: origin hash moved %s -> %s under a steering seed — resuming in place",
            hop.campaign_id,
            hop.cycle_id,
            campaign.root_content_hash,
            plan.origin_hash,
        )
        return
    raise ConfigDriftError(campaign, current_hash=plan.origin_hash, drift=defaults_drift(campaign))


def _join_head_to_head(session: Session, plan: CyclePlan, request: Arm) -> None:
    campaigns = session.store.campaigns
    shared = plan.declaration.shared_part()
    declared = campaigns.load_head_to_head(request.head_to_head_id)
    if declared is None:
        campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=request.head_to_head_id,
                created_at=utcnow_iso(),
                origin=plan.origin_hash,
                shared=shared,
                # Every arm reads this one version, so no arm's election moves another's scale.
                ruler_version=session.store.ruler.version(),
            )
        )
    else:
        differs = [
            name
            for name in SharedPart.model_fields
            if getattr(declared.shared, name) != getattr(shared, name)
        ]
        if declared.origin != plan.origin_hash:
            differs.append("origin")
        if differs:
            raise ConflictError(
                f"head-to-head {request.head_to_head_id} declares another "
                f"{', '.join(differs)}: this arm would not be graded on its bench set",
                code="arm_off_bench_set",
                details={"differs_on": differs},
            )
    taken = [
        other.campaign_id
        for other in campaigns.list_campaigns(lifecycle="all")
        if other.declaration.purpose == Compare(arm=request)
    ]
    if taken:
        raise ConflictError(
            f"arm {request.arm_key} of {request.head_to_head_id} is campaign {taken[0]} already",
            code="arm_taken",
        )


def _prompt_axes_only(
    session: Session, campaign_config: CampaignConfig
) -> dict[str, NodeSearchNarrowing]:
    narrowing: dict[str, NodeSearchNarrowing] = {}
    for node in session.pipeline_schema.declared_nodes:
        held = campaign_config.optimizer_narrowing.get(node.name, NodeSearchNarrowing())
        still_open = node.prompt_fields if held.param_keys is None else held.param_keys
        narrowing[node.name] = held.model_copy(
            update={"param_keys": [key for key in node.prompt_fields if key in still_open]}
        )
    return narrowing


def fresh_campaign_id(session: Session) -> str:
    return mint_campaign_id(session.measured_dataset)


def _write_plan(
    stores: Stores, hop: CycleHop, drawn: PartitionRecord, seed: CycleSeed | None
) -> None:
    """At the mint, so a cycle's draw is on disk before any launch, fork or read of it."""
    stores.campaigns.write_bank_partition(hop, drawn)
    if seed is not None:
        stores.campaigns.write_cycle_seed(hop, seed)


def _mint_campaign(plan: CyclePlan, *, hop: CycleHop, billed: Path | None) -> Session:
    """``hop.campaign_id`` is the CALLER's, so an L4 inner spawn lands back on a campaign it ran."""
    validate_path_component(hop.cycle_id)
    validate_path_component(hop.campaign_id)
    declaration = plan.declaration
    campaign_config = effective_config(declaration)
    now = utcnow_iso()
    session = plan.session
    campaigns = session.store.campaigns
    if billed is not None:
        campaigns.adopt_ledger(hop, billed)
    campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=session.measured_dataset,
            created_at=now,
            root_cycle_id=hop.cycle_id,
            root_content_hash=plan.origin_hash,
            treatment=plan.treatment,
            declaration=declaration,
            backend_id=session.backend_id,
            backend_url=session.backend_client.base_url,
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now,
        )
    )
    _write_plan(session.store, hop, plan.drawn, plan.seed)
    session = session.bound(hop, purpose=declaration.purpose)
    ledger = CycleEventLog.open(CycleDir(campaigns.cycle_dir(hop)))
    declare_run_wiring(session, campaign_config, ledger, tracing=None)
    campaigns.mint_cycle(hop)
    save_active_pointer(session.store.base_dir, hop)
    # So `dashboard.json` stands from the mint on, through the window before the loop starts.
    DashboardCheckout.over(CycleDir(campaigns.cycle_dir(hop)))
    logger.info("Minted fresh campaign %s — cycle %s", hop.campaign_id, hop.cycle_id)
    return session


def mint(plan: CyclePlan, *, campaign_id: str, billed: Path | None) -> MintedCycle:
    """``billed`` is a ledger that paid toward this cycle before it existed, moved in as its first lines."""
    # Never sweep the inner sandbox here: its key carries the campaign id, so only ANOTHER's dies.
    hop = CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id)
    return MintedCycle(
        cycle_id=hop.cycle_id,
        campaign_id=campaign_id,
        campaign_config=effective_config(plan.declaration),
        session=_mint_campaign(plan, hop=hop, billed=billed),
    )


def _mint_door(plan: CyclePlan, *, campaign_id: str, billed: Path | None) -> MintedCycle:
    """On the plan it mints, framing committed: the origin it joins on is that plan's."""
    if isinstance(purpose := plan.declaration.purpose, Compare):
        _join_head_to_head(plan.session, plan, purpose.arm)
    return mint(plan, campaign_id=campaign_id, billed=billed)


def _description_to_decompose(
    session: Session, campaign_config: CampaignConfig, task_text: str | None
) -> str | None:
    if task_text:
        return task_text
    if campaign_config.task_framing == "off" or session.dataset is None:
        return None
    if committed_task_context(session.store, session.dataset):
        return None
    return read_dataset_framing(session.dataset.directory).description or None


async def mint_framed_cycle(
    session: Session,
    declaration: RunDeclaration,
    dataset: Sequence[Sample],
    *,
    campaign_id: str,
    task_text: str | None,
    origin_override: dict[str, NodePrompt] | None = None,
) -> MintedCycle:
    """An L4 inner cell mints through ``mint``, so cells never race to decompose."""
    campaign_config = effective_config(declaration)
    if isinstance(declaration.purpose, Compare):
        if task_text or origin_override:
            raise PayloadInvalidError(
                "an arm runs the head-to-head's origin and framing: no task text, no origin override"
            )
        if campaign_config.optimization.halt_at_accuracy is not None:
            raise ConflictError(
                "an arm stops on its budget, never on an accuracy: drop the halt accuracy",
                code="arm_halt_refused",
            )
    # Every refusal the mint owes, before the check-in below bills.
    plan = resolve_cycle_plan(session, declaration, dataset, origin_override=origin_override)
    description = _description_to_decompose(session, campaign_config, task_text)
    if description is None:
        minted = _mint_door(plan, campaign_id=campaign_id, billed=None)
    else:
        minted = await _mint_after_framing(session, plan, description, campaign_id=campaign_id)
    relabel_minted(minted.session)
    return minted


async def _mint_after_framing(
    session: Session, plan: CyclePlan, description: str, *, campaign_id: str
) -> MintedCycle:
    """The cycle id hashes the framing, so its call bills a draft ledger the mint adopts or a refusal discards."""
    assert session.dataset_name, "a framing commits to the dataset the session opened"
    drafts = session.store.drafts
    draft_id = mint_draft_id()
    checkin_ledger = CycleEventLog.open(CycleDir(drafts.draft_dir(draft_id)))
    try:
        async with paid_call(stores=session.store, bucket="checkin"):
            await commit_task_framing(
                session.store,
                session.dataset_name,
                description,
                campaign_id=campaign_id,
                ledger=checkin_ledger,
            )
        logger.info("Committed task framing for %s from its check-in", session.dataset_name)
        framed = replace(
            plan,
            framing=campaign_framing(
                session.store, effective_config(plan.declaration), session.dataset
            ),
        )
        minted = _mint_door(framed, campaign_id=campaign_id, billed=checkin_ledger.path)
    except BaseException:
        drafts.discard(draft_id)
        raise
    drafts.close(draft_id)
    return minted


def relabel_minted(session: Session) -> None:
    """The one label move; an L4 inner cell mints through ``mint``, so it moves none."""
    session.store.tenant_datasets.relabel(session.measured_dataset, session.bank.dataset_hash)
