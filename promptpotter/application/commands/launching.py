from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

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
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.origin_readiness import save_checkin_draft
from promptpotter.application.datasets.startability import offered_dataset
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.jobs.launcher.checkin import CheckinStart, start_ready_draft
from promptpotter.application.jobs.launcher.launch import (
    Admitted,
    Inline,
    Launched,
    admit,
    launch,
)
from promptpotter.application.jobs.launcher.mint_and_start import (
    ExistingCycle,
    FreshMint,
    OriginIncompleteError,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    resolve_campaign_config,
    resolve_pipeline_for_campaign,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_overlay import steers_disallowed_model
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing, narrowing_of
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import CycleSeed, OriginSource
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import (
    CAMPAIGN_BABYSIT_CAP,
    acting_principal,
    has_capability,
)

if TYPE_CHECKING:
    from promptpotter.application.commands.payloads import (
        MintCampaignPayload,
        StartCheckinPayload,
    )
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.command_kinds import (
        ForkCyclePayload,
        ForkSeed,
        StartRunPayload,
        StepCyclePayload,
    )
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def parse_cycle_seed(
    sent: ForkSeed, campaign: CampaignConfig, stores: Stores, of: Campaign, hop: CycleHop
) -> CycleSeed:
    narrowing: dict[str, NodeSearchNarrowing] = {}
    if sent.node_narrowing:
        # The rows served at *hop*, not the root's: a row's menu is its cycle's own declaration.
        rows = resolve_pipeline_for_campaign(
            stores, of, at=SubjectSpec("course", hop.campaign_id, hop.cycle_id)
        ).node_config_schema
        try:
            narrowing = {
                node: narrowing_of(rows.get(node, []), asked)
                for node, asked in sent.node_narrowing.items()
            }
        except ValueError as exc:
            raise PayloadInvalidError(f"payload.seed.node_narrowing invalid: {exc}") from exc
    seed = CycleSeed(
        origin_prompts=sent.origin_prompts,
        pipeline_overlay=sent.pipeline_overlay,
        optimizer_narrowing=narrowing,
        config_overrides=sent.config_overrides,
        origin_source=OriginSource.FORK_SEED,
    )
    # Refuses a node knob the manifest rejects before a fork is minted.
    select_optimizer(apply_cycle_seed(campaign, seed).optimization)
    return seed


def _declined(dispatcher: CommandDispatcher, hop: CycleHop) -> Applier[object] | None:
    refusal = dispatcher.run_state(hop).launch_refusal
    return Applier.refusing(refusal) if refusal else None


def _diag_target(stores: Stores, hop: CycleHop, mode: RunMode) -> CycleHop:
    if not mode.diag or mode.from_round is not None:
        return hop
    index = stores.campaigns.load(hop, moment=None)
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
            ExistingCycle(
                _diag_target(dispatcher.stores, hop, payload.mode),
                campaign,
                payload.budget,
                step_rounds=payload.step_rounds,
            ),
            payload.mode,
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        )
    )


def step_cycle(
    dispatcher: CommandDispatcher, payload: StepCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    if declined := _declined(dispatcher, hop):
        return declined
    request = ExistingCycle(hop, campaign, Budget(), step_rounds=payload.step_rounds)
    return Applier.silent(
        lambda: launch(
            request,
            RunMode(),
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        )
    )


def fork_cycle(
    dispatcher: CommandDispatcher, payload: ForkCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    if refused := refused_on_an_arm(campaign):
        return refused
    stores = dispatcher.stores
    campaign_config = resolve_campaign_config(stores, campaign, None)
    seed = parse_cycle_seed(payload.seed, campaign_config, stores, campaign, hop)
    # The babysit act (decisions.md § 0005): a steer outside what the node permits; `fork-preview` asks the same call.
    disallowed = steers_disallowed_model(campaign_config.optimizer_narrowing, seed.pipeline_overlay)
    if disallowed and not has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP):
        logger.warning(
            "fork-cycle disallowed-model steer denied for principal %s (missing %s)",
            acting_principal(stores.identity).id,
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
            # Declares no budget: the seed's governs, admitted from what the parent leaves.
            launched = await launch(
                ExistingCycle(fork, campaign, Budget()),
                payload.mode,
                stores=stores,
                job_registry=dispatcher.job_registry,
                inline=dispatcher.inline,
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
        )
    except Exception:
        logger.exception("fork %s: launch failed and its stub could not be cleaned", new_cycle_id)
        return
    if not deleted:
        logger.warning("fork %s: launch failed and its stub was kept (%s)", new_cycle_id, reason)


def mint_campaign(dispatcher: CommandDispatcher, payload: MintCampaignPayload) -> Applier[object]:
    async def _apply_mint() -> Launched:
        request = FreshMint(
            dataset=offered_dataset(dispatcher.stores, payload.dataset_name),
            budget=payload.budget,
            optimization=payload.optimization_sent,
            purpose=payload.purpose,
            declared=payload.campaign_config,
            task_text=payload.task_text,
        )
        # Refused here, before a slot is asked for: a queued mint would refuse it only later.
        request.campaign_config(dispatcher.stores)
        return await launch(
            request,
            RunMode(diag=payload.diag),
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        )

    return Applier.silent(_apply_mint)


def start_checkin(
    dispatcher: CommandDispatcher, start: StartCheckinPayload, claim: DraftClaim
) -> Applier[Admitted]:
    stores = dispatcher.stores

    def _admit() -> Admitted:
        # The claim rides the admitted job to the end of its hold; a refusal before it ends it here.
        try:
            draft = start_ready_draft(stores, claim.draft_id)
            return admit(
                CheckinStart(claim=claim, slug=draft.slug, budget=start.budget),
                RunMode(diag=start.diag),
                stores=stores,
                job_registry=dispatcher.job_registry,
                inline=dispatcher.inline,
            )
        except BaseException as exc:
            if isinstance(exc, OriginIncompleteError):
                # The draft stays, so the operator resolves the gaps and retries.
                save_checkin_draft(stores, owned_draft(stores, claim.draft_id))
            claim.release()
            raise

    # A second Start on it refuses while this one holds it, and is not found once it minted.
    return Applier[Admitted](_admit, replay=None, ack="accepted")


async def dispatch_start_checkin(
    stores: Stores,
    call: CommandCall[StartCheckinPayload],
    *,
    job_registry: JobRegistry | None = None,
    inline: Inline | None = None,
) -> Launched:
    """Acked at admission, before the mint moves the draft's ledger into the root cycle."""
    dispatcher = CommandDispatcher(stores, job_registry=job_registry, inline=inline)
    outcome = await dispatcher.dispatch(call)
    return await cast(Admitted, outcome.result).launched()
