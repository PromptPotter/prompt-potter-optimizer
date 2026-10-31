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
from promptpotter.application.datasets.authored import with_optimization
from promptpotter.application.datasets.draft_campaign import mint_draft_id
from promptpotter.application.free_tier import outside_free_tier, refuse_outside_free_tier
from promptpotter.application.jobs.quota import on_free_tier, paid_call
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    campaign_admits,
    configure_and_apply_pipeline,
    defaults_drift,
    mint_facts,
)
from promptpotter.application.preflight import (
    check_search_split_holds_round,
    optimizer_node_configs,
    refuse_arm_below_round,
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
from promptpotter.application.scoring.sample_measurement import cell_bound, measured_cell_price
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import BankPartition, PartitionRecord, partition_bank
from promptpotter.domain.campaign import (
    Arm,
    Campaign,
    Compare,
    HeadToHeadRecord,
    Optimize,
    Purpose,
    RunDeclaration,
    SharedPart,
)
from promptpotter.domain.campaign_config import (
    apply_cycle_seed,
    declare_run,
    effective_config,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import (
    BackendRevision,
    CycleSeed,
    DefaultsDrift,
    OriginSource,
)
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

    from promptpotter.application.initialization.session import Session
    from promptpotter.config.prompt_blocks import PackShelf
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.command_kinds import ArmRequest, HeadToHeadRequest
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import CapabilityMenu, PipelineSchema
    from promptpotter.domain.prompt_block import Presumption
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.domain.treatment import Treatment
    from promptpotter.infrastructure.store.stores import Stores


logger = logging.getLogger(__name__)


def refuse_unrunnable(
    campaign_config: CampaignConfig,
    selected: SelectedOptimizer,
    dataset: Sequence[Sample],
    pipeline_params: Mapping[str, Any],
    schema: PipelineSchema,
    *,
    free: bool,
    shelf: PackShelf,
) -> BankPartition:
    """Every refusal a run owes, before anything is paid or written; an upload's Start asks it of the draft."""
    refuse_unfit_pipeline(selected, schema)
    partition = partition_bank(
        dataset, campaign_config.dataset_split, seed=campaign_config.run_seed
    )
    refusal = check_search_split_holds_round(selected, len(partition.search))
    if refusal is not None:
        raise PayloadInvalidError(refusal, code="search_split_below_round")
    mint = mint_facts(campaign_config, partition.demo, schema, shelf)
    if pools := selected.pool_refusals(mint=mint):
        raise PayloadInvalidError("; ".join(pools), code="optimizer_overlay")
    refuse_below_reasoning_floor(
        [*node_config_items(dict(pipeline_params)), *optimizer_node_configs(selected)]
    )
    refuse_unpriced(unpriced_routes(campaign_config, selected, pipeline_params, schema))
    if free:
        refuse_outside_free_tier(
            outside_free_tier(campaign_config, selected, pipeline_params, schema)
        )
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
    # The one selection every refusal was asked of: the mint hashes it into `Campaign.treatment`,
    # declares its wiring under it, and the launch binds it (`binding_optimizer`).
    optimizer: SelectedOptimizer
    # What the cycle's chain and scorer admit, which its pools draw and `treatment` hashes.
    admits: frozenset[Presumption]
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
        return origin_content_hash(
            self.origin,
            points,
            list(self.partition.search),
            candidate_library=self.session.candidate_library,
        )

    @property
    def cycle_id(self) -> str:
        return root_cycle_id_of(self.origin_hash, self.declaration)

    @property
    def treatment(self) -> Treatment:
        return self.optimizer.treatment(self.admits, self.session.store.block_packs)


@dataclass(frozen=True)
class MintedCycle:
    cycle_id: str
    campaign_id: str
    campaign_config: CampaignConfig
    session: Session
    optimizer: SelectedOptimizer


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
    runs = apply_cycle_seed(campaign_config, runs_under)
    selected = select_optimizer(runs.optimization)
    partition = refuse_unrunnable(
        runs,
        selected,
        dataset,
        pipeline_params,
        configured.pipeline_schema,
        free=on_free_tier(session.store),
        shelf=session.store.block_packs,
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
        optimizer=selected,
        admits=campaign_admits(runs, configured.pipeline_schema),
        partition=partition,
        # Slots in the session's bank, whichever rows of it the caller hands in.
        drawn=partition.record(dataset_hash=session.bank.dataset_hash),
        seed=seed if hop is None else runs_under,
        session=configured,
    )


class ConfigDriftError(ConflictError):
    code = "config_drift"

    def __init__(
        self,
        campaign: Campaign,
        *,
        current_hash: str,
        drift: DefaultsDrift | None,
        capabilities_moved: list[str],
        revision_moved: str | None,
    ) -> None:
        self.dataset_name = campaign.dataset_name
        causes: list[str] = []
        if drift is not None:
            moved = ", ".join(drift.moved())
            causes.append(f"what its declaration is a delta over moved since the mint ({moved})")
        if capabilities_moved:
            causes.append(
                f"the model capability snapshot moved for {', '.join(capabilities_moved)}, "
                "so a reasoning floor laid into the origin may have moved"
            )
        if revision_moved is not None:
            causes.append(revision_moved)
        cause = "; ".join(causes) or (
            "the dataset, its origin prompts, its candidate library or its framing changed, "
            "not its declaration"
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
                "capabilities_moved": capabilities_moved,
                "backend_revision_moved": revision_moved is not None,
            },
        )


def _capabilities_moved(recorded: CapabilityMenu, running: CapabilityMenu) -> list[str]:
    """``provider/model`` whose answered capability moved; an empty *recorded* menu names none."""
    if not recorded:
        return []
    return sorted(
        f"{provider}/{model}"
        for provider in {*recorded, *running}
        for model in {*recorded.get(provider, {}), *running.get(provider, {})}
        if recorded.get(provider, {}).get(model) != running.get(provider, {}).get(model)
    )


def _revision_moved(
    recorded: BackendRevision | None, running: BackendRevision | None
) -> str | None:
    """``None`` where nothing recorded one: an unknown revision names no move."""
    ran = None if recorded is None else recorded.reported
    now = None if running is None else running.reported
    if ran is None or ran == now:
        return None
    return f"the backend reports revision {now or 'none'}, not the {ran} it ran under"


def resolve_resume(
    session: Session, campaign: Campaign, dataset: Sequence[Sample]
) -> SelectedOptimizer:
    """*session* is bound to the cycle the resume runs; its refusals are that cycle's, asked of the selection it answers."""
    plan = resolve_cycle_plan(session, campaign.declaration, dataset)
    checked = plan
    if plan.seed is not None and plan.seed.pipeline_overlay:
        # A steering seed is its cycle's declared delta: the root's own content is what is checked.
        root = session.bound(campaign.root_hop, purpose=session.purpose)
        checked = resolve_cycle_plan(root, campaign.declaration, dataset)
    if campaign.root_content_hash == checked.origin_hash:
        return plan.optimizer
    campaigns = session.store.campaigns
    recorded = campaigns.read_resolved_run(campaign.root_hop)
    wired = campaigns.read_run_wiring(campaign.root_hop)
    raise ConfigDriftError(
        campaign,
        current_hash=checked.origin_hash,
        drift=defaults_drift(campaign, running=checked.treatment),
        capabilities_moved=_capabilities_moved(
            recorded.model_capabilities, checked.session.pipeline_schema.model_capabilities
        ),
        revision_moved=_revision_moved(
            None if wired is None else wired.backend_revision, checked.session.backend_revision
        ),
    )


def _differs_line(name: str, declared: object, running: object) -> str:
    """A scalar names both values; a structured part (budget, loop, environment) names itself."""
    if isinstance(declared, str | int | None) and isinstance(running, str | int | None):
        return f"{name} (declared {declared}, this arm {running})"
    return name


def _arm_of(plan: CyclePlan) -> Arm:
    purpose = plan.declaration.purpose
    assert isinstance(purpose, Compare), "an arm's plan declares its head-to-head"
    return purpose.arm


def standing_arms(stores: Stores, h2h_id: str) -> dict[str, Campaign]:
    """Arm key → campaign, for every campaign minted as an arm of *h2h_id*."""
    return {
        purpose.arm.arm_key: other
        for other in stores.campaigns.list_campaigns(lifecycle="all")
        if isinstance(purpose := other.declaration.purpose, Compare)
        and purpose.arm.head_to_head_id == h2h_id
    }


def _record_of(plan: CyclePlan) -> HeadToHeadRecord:
    return HeadToHeadRecord(
        head_to_head_id=_arm_of(plan).head_to_head_id,
        created_at=utcnow_iso(),
        origin=plan.origin_hash,
        shared=plan.declaration.shared_part(),
        # Every arm reads this one version, so no arm's election moves another's scale.
        ruler_version=plan.session.store.ruler.version(),
    )


def _refuse_off_record(
    record: HeadToHeadRecord, plan: CyclePlan, arms: Mapping[str, str], *, stood_by: str
) -> None:
    """*stood_by* names what *record* is: the stored declaration, or this act's first arm."""
    arm = _arm_of(plan)
    shared = plan.declaration.shared_part()
    differs = [
        name
        for name in SharedPart.model_fields
        if getattr(record.shared, name) != getattr(shared, name)
    ]
    if record.origin != plan.origin_hash:
        differs.append("origin")
    if differs:
        running = {**shared.model_dump(mode="json"), "origin": plan.origin_hash}
        stood = {**record.shared.model_dump(mode="json"), "origin": record.origin}
        raise ConflictError(
            f"arm {arm.arm_key} of head-to-head {arm.head_to_head_id} differs from {stood_by} on "
            f"{'; '.join(_differs_line(n, stood[n], running[n]) for n in differs)}, so it would "
            "not be graded on that bench set. Nothing was minted: declare the arms alike, or name "
            "a new head-to-head id (`cycles` lists the taken ones).",
            code="arm_off_bench_set",
            details={"differs_on": differs, "declared_origin": record.origin, "arms": dict(arms)},
        )
    if (taken := arms.get(arm.arm_key)) is not None:
        raise ConflictError(
            f"arm {arm.arm_key} of {arm.head_to_head_id} is campaign {taken} already; nothing was "
            "minted",
            code="arm_taken",
        )


def _declared_by(record: HeadToHeadRecord, arms: Mapping[str, str]) -> str:
    held = ", ".join(f"{key}={cid}" for key, cid in sorted(arms.items())) or "no campaign left"
    return f"its record, declared {record.created_at} on origin {record.origin} (arms: {held})"


def arm_config(
    declared: CampaignConfig, request: HeadToHeadRequest, arm: ArmRequest
) -> CampaignConfig:
    return with_optimization(declared, request.declared_optimization(arm))


async def arm_plans(
    session: Session,
    declared: CampaignConfig,
    request: HeadToHeadRequest,
    dataset: Sequence[Sample],
    *,
    origin_override: dict[str, NodePrompt] | None = None,
) -> list[CyclePlan]:
    """THE one place an arm is planned: every refusal it owes — held to its head-to-head's record
    (the stored one, else this act's first arm) and priced on its own budget — before any arm's
    key is taken, any arm admitted, or anything paid."""
    plans: list[CyclePlan] = []
    for arm in request.arms:
        config = arm_config(declared, request, arm)
        if config.optimization.halt_at_accuracy is not None:
            raise ConflictError(
                "an arm stops on its budget, never on an accuracy: drop the halt accuracy",
                code="arm_halt_refused",
            )
        declaration = declare(session, config, purpose=request.purpose(arm))
        plans.append(
            resolve_cycle_plan(session, declaration, dataset, origin_override=origin_override)
        )
    arms = {
        key: campaign.campaign_id
        for key, campaign in standing_arms(session.store, request.head_to_head_id).items()
    }
    stored = session.store.campaigns.load_head_to_head(request.head_to_head_id)
    record = _record_of(plans[0]) if stored is None else stored
    stood_by = f"arm {request.arms[0].arm_key}" if stored is None else _declared_by(stored, arms)
    for plan in plans:
        _refuse_off_record(record, plan, arms, stood_by=stood_by)
    for plan in plans:
        bound = await cell_bound(plan.session, plan.pipeline_params)
        measured = measured_cell_price(plan.session, plan.pipeline_params)
        refuse_arm_below_round(
            _arm_of(plan),
            effective_config(plan.declaration),
            plan.optimizer,
            search=len(plan.partition.search),
            cell_usd=None if bound is None else bound.usd,
            unpriced=() if bound is None else bound.unpriced,
            measured_cell_usd=None if measured is None else measured.usd,
        )
    return plans


def _prompt_axes_only(
    session: Session, campaign_config: CampaignConfig
) -> dict[str, NodeSearchNarrowing]:
    return {
        node.name: campaign_config.optimizer_narrowing.get(
            node.name, NodeSearchNarrowing()
        ).model_copy(update={"param_keys": []})
        for node in session.pipeline_schema.declared_nodes
    }


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
    # What the origin hash was taken under, so a resume that no longer hashes alike names why.
    session.record_resolved_run()
    ledger = CycleEventLog.open(CycleDir(campaigns.cycle_dir(hop)))
    declare_run_wiring(session, campaign_config, ledger, selected=plan.optimizer, tracing=None)
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
        optimizer=plan.optimizer,
    )


def _mint_all(
    plans: Sequence[CyclePlan], campaign_ids: Sequence[str], *, billed: Path | None
) -> list[MintedCycle]:
    """*billed* moves into the first cycle alone. Arms join their head-to-head on the framed plans
    they mint, so a record this act declares is its first arm's as minted."""
    first = plans[0]
    if isinstance(first.declaration.purpose, Compare):
        campaigns = first.session.store.campaigns
        if campaigns.load_head_to_head(_arm_of(first).head_to_head_id) is None:
            campaigns.declare_head_to_head(_record_of(first))
    return [
        mint(plan, campaign_id=campaign_id, billed=billed if i == 0 else None)
        for i, (plan, campaign_id) in enumerate(zip(plans, campaign_ids, strict=True))
    ]


def mint_arms(plans: Sequence[CyclePlan], *, billed: Path | None) -> list[MintedCycle]:
    """*plans* from ``arm_plans``, framed already: a check-in's arms frame nothing."""
    minted = _mint_all(plans, [fresh_campaign_id(plan.session) for plan in plans], billed=billed)
    relabel_minted(minted[0].session)
    return minted


async def mint_framed_arms(session: Session, plans: Sequence[CyclePlan]) -> list[MintedCycle]:
    """*plans* from ``arm_plans``: a framing the dataset still owes is paid once and frames every arm."""
    campaign_ids = [fresh_campaign_id(session) for _ in plans]
    return await _mint_framed(session, plans, campaign_ids, task_text=None)


async def _mint_framed(
    session: Session,
    plans: Sequence[CyclePlan],
    campaign_ids: Sequence[str],
    *,
    task_text: str | None,
) -> list[MintedCycle]:
    description = _description_to_decompose(
        session, effective_config(plans[0].declaration), task_text
    )
    if description is None:
        minted = _mint_all(plans, campaign_ids, billed=None)
    else:
        minted = await _mint_after_framing(session, plans, description, campaign_ids=campaign_ids)
    relabel_minted(minted[0].session)
    return minted


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
    assert isinstance(declaration.purpose, Optimize), "an arm is planned with its head-to-head"
    # Every refusal the mint owes, before the check-in below bills.
    plan = resolve_cycle_plan(session, declaration, dataset, origin_override=origin_override)
    (minted,) = await _mint_framed(session, [plan], [campaign_id], task_text=task_text)
    return minted


async def _mint_after_framing(
    session: Session, plans: Sequence[CyclePlan], description: str, *, campaign_ids: Sequence[str]
) -> list[MintedCycle]:
    """The cycle id hashes the framing, so its call bills a draft ledger the first mint adopts or a refusal discards."""
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
                ledger=checkin_ledger,
            )
        logger.info("Committed task framing for %s from its check-in", session.dataset_name)
        framed = [
            replace(
                plan,
                framing=campaign_framing(
                    session.store, effective_config(plan.declaration), session.dataset
                ),
            )
            for plan in plans
        ]
        minted = _mint_all(framed, campaign_ids, billed=checkin_ledger.path)
    except BaseException:
        drafts.discard(drafts.hold(draft_id))
        raise
    drafts.close(draft_id)
    return minted


def relabel_minted(session: Session) -> None:
    """The one label move; an L4 inner cell mints through ``mint``, so it moves none."""
    session.store.tenant_datasets.relabel(session.measured_dataset, session.bank.dataset_hash)
