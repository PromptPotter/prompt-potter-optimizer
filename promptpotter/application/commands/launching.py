from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter, ValidationError

from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    mint_diag_sibling,
    mint_operator_fork,
)
from promptpotter.application.campaign_config import CampaignConfig, apply_cycle_seed
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    refused_on_an_arm,
)
from promptpotter.application.commands.draft_editing import reread_draft
from promptpotter.application.datasets.origin_readiness import save_checkin_draft
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.jobs.launcher import launch as launcher
from promptpotter.application.jobs.launcher.checkin import CheckinStart, load_checkin_for_start
from promptpotter.application.jobs.launcher.launch import Inline, Launched, LaunchRequest
from promptpotter.application.jobs.launcher.mint_and_start import (
    ExistingCycle,
    FreshMint,
    OriginIncompleteError,
    dataset_campaign_config,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    resolve_campaign_config,
    resolve_pipeline_for_campaign,
)
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import LaunchLimits, RunMode
from promptpotter.domain.pipeline_overlay import steers_disallowed_model
from promptpotter.domain.pipeline_schema import ParamIntent, narrowing_of
from promptpotter.domain.run_records import CycleSeed, OriginSource
from promptpotter.infrastructure.store.dataset_access import readable_dataset_dir
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import (
    CAMPAIGN_BABYSIT_CAP,
    acting_principal_id,
    has_capability,
)

if TYPE_CHECKING:
    from promptpotter.application.commands.payloads import (
        ForkCyclePayload,
        MintCampaignPayload,
        StartCheckinPayload,
        StartRunPayload,
        StepCyclePayload,
    )
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "dispatch_start_checkin",
    "fork_cycle",
    "launch",
    "mint_campaign",
    "start_checkin",
    "start_run",
    "step_cycle",
]


async def launch(dispatcher: CommandDispatcher, request: LaunchRequest, mode: RunMode) -> Launched:
    return await launcher.launch(
        request,
        stores=dispatcher.stores,
        job_registry=dispatcher.job_registry,
        mode=mode,
        inline=dispatcher.inline,
    )


_NODE_INTENTS: TypeAdapter[dict[str, list[ParamIntent]]] = TypeAdapter(dict[str, list[ParamIntent]])


def _parse_cycle_seed(
    raw: object, campaign: CampaignConfig, stores: Stores, of: Campaign, hop: CycleHop
) -> CycleSeed:
    if not isinstance(raw, dict):
        raise PayloadInvalidError("payload.seed (object) is required.")
    if "node_narrowing" in raw:
        if "optimizer_narrowing" in raw:
            raise PayloadInvalidError(
                "payload.seed carries both node_narrowing and optimizer_narrowing; send one."
            )
        # The rows served at *hop*, not the root's: a row's menu is its cycle's own declaration.
        rows = resolve_pipeline_for_campaign(
            stores, of, at=SubjectSpec("course", hop.campaign_id, hop.cycle_id)
        ).node_config_schema
        try:
            intents = _NODE_INTENTS.validate_python(raw["node_narrowing"])
            declared = {
                node: narrowing_of(rows.get(node, []), asked) for node, asked in intents.items()
            }
        except ValueError as exc:
            raise PayloadInvalidError(f"payload.seed.node_narrowing invalid: {exc}") from exc
        raw = {k: v for k, v in raw.items() if k != "node_narrowing"}
        raw["optimizer_narrowing"] = declared
    try:
        seed = CycleSeed.model_validate({**raw, "origin_source": OriginSource.FORK_SEED})
    except ValidationError as exc:
        raise PayloadInvalidError(f"payload.seed invalid: {exc}") from exc
    # Refuses a node knob the manifest rejects before a fork is minted.
    select_optimizer(apply_cycle_seed(campaign, seed).optimization)
    return seed


def _declined(dispatcher: CommandDispatcher, hop: CycleHop) -> Applier[object] | None:
    refusal = dispatcher.run_state(hop).launch_refusal
    return Applier.refusing(refusal) if refusal else None


def _diag_target(stores: Stores, hop: CycleHop, mode: RunMode) -> CycleHop:
    if not mode.diag or mode.from_round is not None:
        return hop
    index = stores.campaigns.load(hop)
    final = None if index is None else index.final
    if final is None or final.mode != "diag":
        return hop
    return CycleHop(
        campaign_id=hop.campaign_id,
        cycle_id=mint_diag_sibling(stores=stores, hop=hop),
    )


def start_run(
    dispatcher: CommandDispatcher, payload: StartRunPayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    if declined := _declined(dispatcher, hop):
        return declined
    return Applier.silent(
        lambda: launch(
            dispatcher,
            ExistingCycle(_diag_target(dispatcher.stores, hop, payload), campaign, payload),
            payload,
        )
    )


def step_cycle(
    dispatcher: CommandDispatcher, payload: StepCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    if declined := _declined(dispatcher, hop):
        return declined
    limits = LaunchLimits(step_rounds=payload.rounds)
    return Applier.silent(
        lambda: launch(dispatcher, ExistingCycle(hop, campaign, limits), RunMode())
    )


def fork_cycle(
    dispatcher: CommandDispatcher, payload: ForkCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    if refused := refused_on_an_arm(campaign):
        return refused
    stores = dispatcher.stores
    seed = _parse_cycle_seed(
        payload.seed, resolve_campaign_config(stores, campaign, None), stores, campaign, hop
    )
    # ADR-0005 §4 babysit: a steer outside what the node permits; `fork-preview` asks the same call.
    disallowed = steers_disallowed_model(campaign.config, seed.pipeline_overlay)
    if disallowed and not has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP):
        logger.warning(
            "fork-cycle disallowed-model steer denied for principal %s (missing %s)",
            acting_principal_id(stores.identity),
            CAMPAIGN_BABYSIT_CAP,
        )
        raise NotFoundError("Not found", code="not_found")

    async def _apply_fork() -> Launched:
        new_cycle_id = mint_operator_fork(
            stores=stores,
            hop=hop,
            from_round=payload.round,
            from_candidate_id=payload.candidate_id,
            seed=seed,
            keep_rounds=payload.keep_rounds,
            reason=payload.reason,
        )
        fork = CycleHop(campaign_id=hop.campaign_id, cycle_id=new_cycle_id)
        try:
            # Declare no limits — the seed's reconciled ones govern at the runner seam.
            launched = await launch(
                dispatcher, ExistingCycle(fork, campaign, LaunchLimits()), payload
            )
        except BaseException:
            # A start raises only BEFORE the run exists, so the stub is idle.
            _cleanup_failed_fork(stores, hop, new_cycle_id)
            raise
        # Only once the fork's run is admitted: a refused fork pauses nothing.
        if not dispatcher.run_state(hop).pause_refusal:
            dispatcher.pause_superseded(hop, fork)
        return launched

    return Applier.silent(_apply_fork)


def _cleanup_failed_fork(stores: Stores, parent_hop: CycleHop, new_cycle_id: str) -> None:
    # Best-effort: it must never mask the launch failure its caller re-raises.
    try:
        deleted, reason = cleanup_stub_fork_if_empty(
            campaign_store=stores.campaigns,
            hop=CycleHop(campaign_id=parent_hop.campaign_id, cycle_id=new_cycle_id),
            parent_cycle_id=parent_hop.cycle_id,
        )
    except Exception:
        logger.exception("fork %s: launch failed and its stub could not be cleaned", new_cycle_id)
        return
    if not deleted:
        logger.warning("fork %s: launch failed and its stub was kept (%s)", new_cycle_id, reason)


def mint_campaign(dispatcher: CommandDispatcher, payload: MintCampaignPayload) -> Applier[object]:
    async def _apply_mint() -> Launched:
        optimization = payload.optimization_sent
        # Refused here, before a slot is asked for: a queued mint would refuse it only later.
        dataset_campaign_config(
            readable_dataset_dir(dispatcher.stores, payload.dataset_name),
            optimization=optimization,
            declared=payload.campaign_config,
        )
        return await launch(
            dispatcher,
            FreshMint(
                dataset_name=payload.dataset_name,
                limits=payload,
                optimization=optimization,
                arm=payload.arm,
                declared=payload.campaign_config,
                task_text=payload.task_text,
                backend_url=payload.backend_url,
                backend_id=payload.backend_id,
            ),
            RunMode(diag=payload.diag),
        )

    return Applier.silent(_apply_mint)


def start_checkin(dispatcher: CommandDispatcher, start: StartCheckinPayload) -> Applier[Launched]:
    stores = dispatcher.stores
    campaign_id = start.campaign_id
    draft = reread_draft(stores, campaign_id)

    async def _apply() -> Launched:
        try:
            hop, gated = load_checkin_for_start(stores, campaign_id)
            return await launch(
                dispatcher,
                CheckinStart(
                    hop=hop,
                    draft=gated,
                    limits=start,
                    backend_url=start.backend_url,
                    backend_id=start.backend_id,
                ),
                RunMode(diag=start.diag),
            )
        except OriginIncompleteError:
            # Lifecycle stays ``checkin``, so the operator resolves the gaps and retries.
            save_checkin_draft(stores, draft)
            raise
        except BackendUnreachableError as exc:
            exc.details["campaign_id"] = campaign_id
            raise

    # The flip from `checkin` to `active` is the retry guard: a second Start is a `LaunchError`.
    return Applier[Launched](_apply, replay=None)


async def dispatch_start_checkin(
    stores: Stores,
    call: CommandCall[StartCheckinPayload],
    *,
    job_registry: JobRegistry | None = None,
    inline: Inline | None = None,
) -> Launched:
    dispatcher = CommandDispatcher(stores, job_registry=job_registry, inline=inline)
    outcome = await dispatcher.dispatch_checkin_command(call)
    return cast(Launched, outcome.result)
