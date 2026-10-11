from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.task_context import (
    campaign_framing,
    commit_task_framing,
    committed_task_context,
)
from promptpotter.application.campaign_config import under_record
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.application.knobs import DiffScope, classify_config_diff
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    frozen_config,
    resolved_dataset_name,
)
from promptpotter.application.preflight import (
    check_search_pool_holds_round,
    refuse_below_reasoning_floor,
)
from promptpotter.application.run_observers import declare_run_wiring
from promptpotter.application.runner.campaign_ids import (
    build_origin_cycle_id,
    mint_campaign_id,
    mint_checkin_cycle_id,
)
from promptpotter.domain.bench import BankPartition, partition_bank
from promptpotter.domain.campaign import (
    Arm,
    ArmRequest,
    BenchSet,
    Campaign,
    HeadToHeadRecord,
    Treatment,
    bench_set_of,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.launch_limits import LaunchLimits, refuse_arm_halt
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import CycleSeed, OriginSource
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PayloadInvalidError
from promptpotter.shared.hashing import dataset_hash

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores


logger = logging.getLogger(__name__)


class _Runnable(NamedTuple):
    treatment: Treatment
    partition: BankPartition


def _runnable(campaign_config: CampaignConfig, dataset: list[Sample]) -> _Runnable:
    treatment = select_optimizer(campaign_config.optimization).treatment()
    partition = partition_bank(dataset, campaign_config.dataset_split)
    refusal = check_search_pool_holds_round(campaign_config, len(partition.search))
    if refusal is not None:
        raise PayloadInvalidError(refusal, code="search_pool_below_round")
    return _Runnable(treatment, partition)


@dataclass(frozen=True)
class CyclePlan:
    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
    cycle_id: str
    treatment: Treatment
    partition: BankPartition
    seed: CycleSeed | None


@dataclass(frozen=True)
class MintedCycle:
    cycle_id: str
    campaign_id: str
    campaign_config: CampaignConfig


def _plan(
    session: Session,
    campaign_config: CampaignConfig,
    runnable: _Runnable,
    *,
    origin_override: dict[str, Any] | None,
) -> CyclePlan:
    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(session, campaign_config)
    refuse_below_reasoning_floor(campaign_config, pipeline_params)
    seed = (
        CycleSeed(origin_prompt_fields=origin_override, origin_source=OriginSource.CAMPAIGN_ORIGIN)
        if origin_override
        else None
    )
    origin = resolve_origin_opt_search_point(
        prompt_node_names=schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
        seed=seed,
        pipeline_params=pipeline_params,
        schema=schema,
    )
    partition = runnable.partition
    return CyclePlan(
        pipeline_params=pipeline_params,
        origin=origin,
        # The id hashes the render the measurement key does: merged params, framing, the search rows.
        cycle_id=build_origin_cycle_id(
            origin,
            schema,
            list(partition.search),
            framing=campaign_framing(session.store, campaign_config, session.dataset_name),
            demo=partition.demo,
        ),
        treatment=runnable.treatment,
        partition=partition,
        seed=seed,
    )


def resolve_cycle_plan(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    origin_override: dict[str, Any] | None = None,
) -> CyclePlan:
    return _plan(
        session,
        campaign_config,
        _runnable(campaign_config, dataset),
        origin_override=origin_override,
    )


class ConfigDriftError(ConflictError):
    code = "config_drift"

    def __init__(
        self, campaign: Campaign, *, current_hash: str, scope: DiffScope, changed: list[str]
    ) -> None:
        self.dataset_name = campaign.dataset_name
        super().__init__(
            f"campaign {campaign.campaign_id} no longer resolves to the origin it was minted on "
            f"(stored {campaign.root_content_hash}, now {current_hash}; changed: "
            f"{', '.join(changed) or 'the dataset, not the campaign config'}). Its measurements "
            "may not apply. Revert the edit, or start a fresh campaign on "
            f"{campaign.dataset_name} — this one is preserved.",
            details={
                "campaign_id": campaign.campaign_id,
                "stored_hash": campaign.root_content_hash,
                "current_hash": current_hash,
                "scope": scope.value,
                "changed": changed,
            },
        )


def refuse_drifted_resume(
    session: Session, campaign_config: CampaignConfig, campaign: Campaign, hop: CycleHop
) -> None:
    if campaign.root_content_hash is None:
        raise PayloadInvalidError(
            f"campaign {campaign.campaign_id} has no stamped identity — an unstarted check-in "
            "cannot be resumed. Start it first."
        )
    campaigns = session.store.campaigns
    # The origin the campaign was minted on is its ROOT's — a chosen one rides the root's seed.
    root_seed = campaigns.read_cycle_seed(campaign.root_hop)
    plan = resolve_cycle_plan(
        session,
        campaign_config,
        session.samples,
        origin_override=(root_seed.origin_prompt_fields or None) if root_seed else None,
    )
    current_hash = plan.cycle_id.removeprefix("cycle_")
    if campaign.root_content_hash == current_hash:
        return
    scope, changed = classify_config_diff(
        campaign_config, frozen_config(session.store, campaign), arm=campaign.arm is not None
    )
    seed = campaigns.read_cycle_seed(hop)
    # A seed that steers the pipeline declared its own values at the cut, so it runs under them.
    if scope is DiffScope.POLICY_ONLY or (seed is not None and seed.pipeline_overlay):
        logger.info(
            "resume %s/%s: origin hash moved %s -> %s (%s; changed: %s) — resuming in place",
            hop.campaign_id,
            hop.cycle_id,
            campaign.root_content_hash,
            current_hash,
            scope.value,
            ", ".join(changed) or "none",
        )
        return
    raise ConfigDriftError(campaign, current_hash=current_hash, scope=scope, changed=changed)


def write_plan_seed(stores: Stores, hop: CycleHop, plan: CyclePlan) -> None:
    if plan.seed is not None:
        stores.campaigns.write_cycle_seed(hop, plan.seed)


def _join_head_to_head(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    plan: CyclePlan,
    request: ArmRequest,
) -> Arm:
    optimization = campaign_config.optimization
    partition = plan.partition
    bench_set = bench_set_of(
        dataset_name=resolved_dataset_name(session, campaign_config),
        dataset_hash=dataset_hash(dataset),
        split=campaign_config.dataset_split,
        bench_ids=[s.id for s in partition.bench],
        scorer_id=scorer_of(campaign_config, verifier_graded=False).id,
        origin_params=plan.pipeline_params,
        origin=plan.cycle_id.removeprefix("cycle_"),
    )
    budget = optimization.arm_budget
    campaigns = session.store.campaigns
    declared = campaigns.load_head_to_head(request.head_to_head_id)
    if declared is None:
        campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=request.head_to_head_id,
                created_at=utcnow_iso(),
                bench_set=bench_set,
                budget=budget,
            )
        )
    else:
        differs = [
            f"bench_set.{name}"
            for name in BenchSet.model_fields
            if getattr(declared.bench_set, name) != getattr(bench_set, name)
        ]
        if differs:
            raise ConflictError(
                f"head-to-head {request.head_to_head_id} declares another "
                f"{', '.join(differs)}: this arm would not be graded on its bench set",
                code="arm_off_bench_set",
                details={"differs_on": differs},
            )
    taken = [
        other.campaign_id
        for campaign_dir in campaigns.iter_campaign_dirs()
        if (other := campaigns.load_campaign(campaign_dir.name)) is not None
        and other.arm is not None
        and other.arm.head_to_head_id == request.head_to_head_id
        and other.arm.arm_key == request.arm_key
    ]
    if taken:
        raise ConflictError(
            f"arm {request.arm_key} of {request.head_to_head_id} is campaign {taken[0]} already",
            code="arm_taken",
        )
    return Arm(
        head_to_head_id=request.head_to_head_id,
        arm_key=request.arm_key,
        treatment_digest=plan.treatment.digest,
    )


def _prompt_axes_only(
    session: Session, campaign_config: CampaignConfig
) -> dict[str, NodeSearchNarrowing]:
    narrowing: dict[str, NodeSearchNarrowing] = {}
    for node in session.pipeline_schema.declared_nodes:
        held = campaign_config.optimizer_narrowing.get(node.name, NodeSearchNarrowing())
        still_open = PROMPT_STRING_FIELDS if held.param_keys is None else held.param_keys
        narrowing[node.name] = held.model_copy(
            update={"param_keys": [key for key in PROMPT_STRING_FIELDS if key in still_open]}
        )
    return narrowing


def under_declared_record(
    stores: Stores, campaign_config: CampaignConfig, arm: ArmRequest | None
) -> CampaignConfig:
    if arm is None:
        return campaign_config
    declared = stores.campaigns.load_head_to_head(arm.head_to_head_id)
    return campaign_config if declared is None else under_record(campaign_config, declared)


def _under_declaration(
    session: Session, campaign_config: CampaignConfig, arm: ArmRequest | None
) -> CampaignConfig:
    if arm is None:
        return campaign_config
    narrowed = campaign_config.model_copy(
        update={
            "optimizer_narrowing": _prompt_axes_only(session, campaign_config),
            # An arm's comparison IS its bench score, so a `manual` trigger is minted `at_end`.
            "bench_trigger": (
                "at_end"
                if campaign_config.bench_trigger == "manual"
                else campaign_config.bench_trigger
            ),
        }
    )
    return under_declared_record(session.store, narrowed, arm)


def fresh_campaign_id(session: Session, campaign_config: CampaignConfig) -> str:
    return mint_campaign_id(resolved_dataset_name(session, campaign_config))


def _declare_frozen_wiring(
    session: Session, campaign_config: CampaignConfig, hop: CycleHop
) -> None:
    """Runs AHEAD of the record taking the cycle past check-in, so no read finds it stateless."""
    ledger = CycleEventLog.open(CycleDir(session.store.campaigns.cycle_dir(hop)))
    declare_run_wiring(session, campaign_config, ledger, tracing=None)


def _mint_campaign(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    treatment: Treatment,
    arm: Arm | None,
) -> None:
    """``hop.campaign_id`` is the CALLER's, so an L4 inner spawn lands back on a campaign it ran."""

    target_hash = hop.cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    now = utcnow_iso()
    dataset_name = resolved_dataset_name(session, campaign_config)
    validate_path_component(hop.campaign_id)
    root_cycle = hop.cycle_id

    campaigns = session.store.campaigns
    campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=dataset_name,
            created_at=now,
            root_cycle_id=root_cycle,
            root_content_hash=target_hash,
            treatment=treatment,
            arm=arm,
            backend_id=session.backend_id,
            backend_url=session.backend_client.base_url,
            backend_type=backend_type_of_dataset(session.store, dataset_name),
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now,
            config=campaign_config.frozen(arm=arm is not None),
        )
    )

    _declare_frozen_wiring(session, campaign_config, hop)
    campaigns.mint_cycle(hop)

    session.campaign_id = hop.campaign_id
    session.state.cycle_id = root_cycle

    save_active_pointer(session.store.base_dir, hop)

    # So the mint → loop-start window serves `dashboard.json` off the campaign's own declarations.
    LiveDashboardProjection.for_session(session.hop, tenant_root=session.tenant_root)

    logger.info("Minted fresh campaign %s — cycle %s", hop.campaign_id, root_cycle)


def mint_checkin_skeleton(stores: Stores, *, slug: str, backend_type: str) -> CycleHop:
    """Claims NO active pointer: an unrun check-in would pull a watching workspace off authoring."""

    now = utcnow_iso()
    hop = CycleHop(campaign_id=mint_campaign_id(slug), cycle_id=mint_checkin_cycle_id())

    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=slug,
            created_at=now,
            root_cycle_id=hop.cycle_id,
            backend_id="",
            backend_type=backend_type,
            owner_user_id=str(stores.identity.user_id),
            lifecycle_changed_at=now,
            config={},
        )
    )
    stores.campaigns.mint_cycle(hop, checkin=True)

    logger.info("Minted check-in campaign %s — cycle %s", hop.campaign_id, hop.cycle_id)
    return hop


def finalize_checkin_to_active(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    cycle_plan: CyclePlan,
) -> None:
    """The cycle id stays the provisional ``cycle_chk_*``: drift reads ``root_content_hash``."""

    target_hash = cycle_plan.cycle_id.removeprefix("cycle_")

    session.store.campaigns.update_campaign(
        hop.campaign_id,
        root_content_hash=target_hash,
        treatment=cycle_plan.treatment.model_dump(mode="json"),
        backend_id=session.backend_id,
        backend_url=session.backend_client.base_url,
        # Re-read, not trusted from the skeleton: the check-in writes `pipeline.yaml` in between.
        backend_type=backend_type_of_dataset(session.store, session.dataset_name or ""),
        config=campaign_config.frozen(arm=False),
    )
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id

    # The store's OWN workspace: a sandboxed inner cycle (L4) never stamps the outer tenant's.
    save_active_pointer(session.store.base_dir, hop)

    _declare_frozen_wiring(session, campaign_config, hop)
    session.store.campaigns.close_checkin(hop)
    LiveDashboardProjection.for_session(session.hop, tenant_root=session.tenant_root)

    logger.info("Check-in campaign %s started — cycle %s", hop.campaign_id, hop.cycle_id)


def _mint_runnable(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    runnable: _Runnable,
    *,
    campaign_id: str,
    arm: ArmRequest | None,
    origin_override: dict[str, Any] | None,
) -> MintedCycle:
    plan = _plan(session, campaign_config, runnable, origin_override=origin_override)
    arm_of = (
        None if arm is None else _join_head_to_head(session, campaign_config, dataset, plan, arm)
    )
    # Never sweep the inner sandbox here: its key carries the campaign id, so only ANOTHER's dies.
    hop = CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id)
    _mint_campaign(session, campaign_config, hop=hop, treatment=plan.treatment, arm=arm_of)
    write_plan_seed(session.store, hop, plan)
    return MintedCycle(
        cycle_id=hop.cycle_id,
        campaign_id=campaign_id,
        campaign_config=campaign_config,
    )


def prepare_fresh_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    campaign_id: str,
    arm: ArmRequest | None,
    origin_override: dict[str, Any] | None = None,
) -> MintedCycle:
    """``campaign_id`` takes no default: who owns the campaign's identity is the caller's decision."""
    campaign_config = _under_declaration(session, campaign_config, arm)
    return _mint_runnable(
        session,
        campaign_config,
        dataset,
        _runnable(campaign_config, dataset),
        campaign_id=campaign_id,
        arm=arm,
        origin_override=origin_override,
    )


def _description_to_decompose(
    session: Session, campaign_config: CampaignConfig, task_text: str | None
) -> str | None:
    if task_text:
        return task_text
    if campaign_config.task_framing == "off" or session.dataset_config_dir is None:
        return None
    if committed_task_context(session.store, session.dataset_name):
        return None
    path = Path(session.dataset_config_dir) / "task_description.md"
    return (path.read_text(encoding="utf-8").strip() if path.is_file() else "") or None


async def mint_framed_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    campaign_id: str,
    task_text: str | None,
    arm: ArmRequest | None,
    limits: LaunchLimits,
    origin_override: dict[str, Any] | None = None,
) -> MintedCycle:
    """An L4 inner cell mints through ``prepare_fresh_cycle``, so cells never race to decompose."""
    if arm is not None:
        if task_text or origin_override:
            raise PayloadInvalidError(
                "an arm runs the head-to-head's origin and framing: no task text, no origin override"
            )
        refuse_arm_halt(limits.halt_at_accuracy)
    campaign_config = _under_declaration(session, campaign_config, arm)
    # Before the check-in below bills.
    runnable = _runnable(campaign_config, dataset)
    description = _description_to_decompose(session, campaign_config, task_text)
    # The cycle id hashes the framing this commits, so check-in bills a scratch ledger first.
    with TemporaryDirectory() as scratch:
        checkin_ledger = CycleEventLog.open(CycleDir(Path(scratch)))
        if description is not None:
            assert session.dataset_name, "a framing commits to the dataset the session opened"
            async with paid_verb(stores=session.store, bucket="checkin", hop=None):
                await commit_task_framing(
                    session.store,
                    session.dataset_name,
                    description,
                    campaign_id=campaign_id,
                    ledger=checkin_ledger,
                )
            logger.info("Committed task framing for %s from its check-in", session.dataset_name)
        minted = _mint_runnable(
            session,
            campaign_config,
            dataset,
            runnable,
            campaign_id=campaign_id,
            arm=arm,
            origin_override=origin_override,
        )
        run_ledger = CycleEventLog.open(
            CycleDir(
                session.store.campaigns.cycle_dir(
                    CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
                )
            )
        )
        for _offset, record in checkin_ledger.iter():
            run_ledger.append(record)
    return minted


__all__ = [
    "ConfigDriftError",
    "CyclePlan",
    "finalize_checkin_to_active",
    "fresh_campaign_id",
    "mint_checkin_skeleton",
    "mint_framed_cycle",
    "prepare_fresh_cycle",
    "refuse_drifted_resume",
    "resolve_cycle_plan",
    "under_declared_record",
    "write_plan_seed",
]
