from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    mint_diag_sibling,
    mint_operator_fork,
)
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    Dispatched,
    DispatchedToCycle,
    DispatchedToDraft,
    RejectedError,
    Replaying,
    replays,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.startability import offered_dataset
from promptpotter.application.jobs.launcher.checkin import CheckinStart
from promptpotter.application.jobs.launcher.head_to_head import (
    AdmittedHeadToHead,
    HeadToHeadLaunched,
    admit_head_to_head,
    checkin_arms,
    dataset_arms,
    head_to_head_answer,
)
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
    assert_origin_ready,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    cycle_config,
    draft_pipeline,
    draft_served,
    permitted_narrowing,
    recorded_schema,
    resolve_pipeline_for_campaign,
    steer_verdict,
)
from promptpotter.domain.campaign_config import CycleConfig, LaidSpan
from promptpotter.domain.command_events import CommandEffect
from promptpotter.domain.command_kinds import (
    CancelQueuedRunPayload,
    CommandKind,
    MintHeadToHeadPayload,
    PauseCyclePayload,
    StartCheckinPayload,
)
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, InnerRun, Job
from promptpotter.domain.phases import INNER_PAUSE_REFUSAL
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing, PipelineSchema, narrowing_of
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ComparisonExit,
    CycleSeed,
    LaunchedEffect,
    LaunchedJob,
    OriginSource,
)
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.shared.errors import (
    LedgerUnreadableError,
    NotFoundError,
    PayloadInvalidError,
    TaskUncancellableError,
)
from promptpotter.shared.identity import CAMPAIGN_BABYSIT_CAP, acting_principal

if TYPE_CHECKING:
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.command_kinds import (
        CommandPayload,
        ForkCyclePayload,
        ForkSeed,
        HeadToHeadRequest,
        MintCampaignPayload,
        StartRunPayload,
    )
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def _typed_steer(overlay: dict[str, Any], schema: PipelineSchema) -> dict[str, Any]:
    """Each steered value in its param's declared type at *schema*, as every door sends it: a
    string stays text, and a string sent for any other type is read as JSON."""
    typed = dict(overlay)
    for node, cfg in overlay.items():
        declared = schema.get_node(node)
        if declared is None:
            names = ", ".join(n.name for n in schema.declared_nodes) or "(none)"
            raise PayloadInvalidError(
                f"payload.seed steers no node called {node!r}; nodes: {names}"
            )
        params: dict[str, Any] = {}
        for param, value in cfg.items():
            kind = declared.param_types.get(param)
            if kind is None:
                known = ", ".join(sorted(declared.param_types)) or "(none)"
                raise PayloadInvalidError(
                    f"payload.seed: {node!r} carries no {param!r}; params: {known}"
                )
            if kind != "string" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    raise PayloadInvalidError(
                        f"payload.seed steers {node}.{param}, declared {kind}; {value!r} is not "
                        "one (spell it as JSON)"
                    ) from None
            params[param] = value
        typed[node] = params
    return typed


def parse_cycle_seed(sent: ForkSeed, stores: Stores, of: Campaign, hop: CycleHop) -> CycleSeed:
    narrowing: dict[str, NodeSearchNarrowing] = {}
    if sent.node_narrowing:
        # The rows served at *hop*, not the root's: a row's menu is its cycle's own declaration.
        rows = resolve_pipeline_for_campaign(stores, of, at=Subject.of((hop,))).node_config_schema
        try:
            narrowing = {
                node: narrowing_of(rows.get(node, []), asked)
                for node, asked in sent.node_narrowing.items()
            }
        except ValueError as exc:
            raise PayloadInvalidError(f"payload.seed.node_narrowing invalid: {exc}") from exc
    verdict = steer_verdict(stores, permitted_narrowing(stores, of), sent.pipeline_overlay)
    if verdict == "refused":
        logger.warning(
            "fork-cycle disallowed-model steer denied for principal %s (missing %s)",
            acting_principal(stores.identity).id,
            CAMPAIGN_BABYSIT_CAP,
        )
        raise NotFoundError("Not found", code="not_found")
    overlay = (
        _typed_steer(sent.pipeline_overlay, recorded_schema(stores, hop))
        if sent.pipeline_overlay
        else {}
    )
    for node, cfg in overlay.items():
        allowed = narrowing[node].param_allowed_values if node in narrowing else {}
        for param, value in cfg.items():
            if param in allowed and str(value) not in allowed[param]:
                raise PayloadInvalidError(
                    f"payload.seed steers {node}.{param}={value!r} outside its own "
                    f"node_narrowing {allowed[param]}"
                )
    seed = CycleSeed(
        origin_prompts=sent.origin_prompts,
        pipeline_overlay=overlay,
        optimizer_narrowing=narrowing,
        config_overrides=sent.config_overrides,
        origin_source=OriginSource.FORK_SEED,
    )
    # Over what *hop* runs, so a knob its parent's seed set refuses before a fork is minted.
    parent = cycle_config(stores, of, hop).config
    select_optimizer(
        CycleConfig.laid(parent, [LaidSpan(seed=seed, scoring_lock=None)]).config.optimization
    )
    return seed


def _launch_applier(
    dispatcher: CommandDispatcher,
    run: Callable[[], Awaitable[Launched]],
    *,
    refused_on: CycleHop | None = None,
) -> Applier[Launched]:
    """A launch's applier: its ack records the launch, which a retry under its key answers
    (`relaunched`) before *refused_on*'s start refusal is asked."""

    async def _run() -> Launched:
        if refused_on is not None and (refusal := dispatcher.run_state(refused_on).start_refusal):
            raise RejectedError(refusal)
        return await run()

    return Applier(_run, effect_fn=lambda ran: LaunchedEffect(launches=[ran.launch]))


@replays(
    CommandKind.START_RUN,
    CommandKind.FORK_CYCLE,
    CommandKind.MINT_CAMPAIGN,
    CommandKind.HEAD_TO_HEAD,
    CommandKind.START_CHECKIN,
)
async def relaunched(replaying: Replaying[Any]) -> StartAnswer:
    """Every launch kind's retry: what its first act's ack recorded, arm by arm where it asked a
    head-to-head, watching no exit (another act owns them)."""
    launches = _recorded_launches(replaying.end.effect)
    match replaying.payload:
        case MintHeadToHeadPayload(head_to_head=request):
            return head_to_head_answer(request, launches)
        case StartCheckinPayload(head_to_head=request) if request is not None:
            return head_to_head_answer(request, launches)
    (launch,) = launches
    return Launched(launch, held=None)


# What a cancel's pause of a launched run says it was asked for.
_CANCELLED = "command cancelled"


def launch_cancels(jobs: list[Job], stores: Stores) -> list[CommandCall[CommandPayload]]:
    """A launch's `cancel`: each live job's own act, resolved for every job before any is
    dispatched: a queued one withdrawn, a running run paused on its own cycle in *stores*, each
    keyed by the job so a retried cancel acts once. A job no command of *stores* cancels now — its
    cycle refusing the pause (`RunState.pause_refusal`) among them — refuses them all."""
    acts: list[CommandCall[CommandPayload]] = []
    for job in jobs:
        key = f"cancel:{job.job_id}"
        if job.released:
            continue
        if job.queued:
            acts.append(CommandCall(CancelQueuedRunPayload(job_id=job.job_id), key))
            continue
        if isinstance(job.holder, InnerRun):
            raise TaskUncancellableError(f"{job.what}: {INNER_PAUSE_REFUSAL}")
        if job.hop == UNRESOLVED_HOP:
            raise TaskUncancellableError(f"{job.what} names no cycle to pause yet; cancel again")
        if job.workspace != str(stores.campaigns.workspace):
            raise TaskUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := stores.run_state(job.hop).pause_refusal:
            raise TaskUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.campaign_id, cycle_id=job.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key))
    return acts


def _recorded_launches(effect: CommandEffect) -> list[LaunchedJob]:
    """What a launch's ack recorded, which every launch kind's retry answers."""
    if not isinstance(effect, LaunchedEffect):
        raise LedgerUnreadableError(
            ledger="the launch's ledger",
            offset=None,
            reason=f"a launch's ack names no job, its effect is {effect.kind!r}",
            campaign_id=None,
        )
    return effect.launches


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


async def start_run(dispatched: DispatchedToCycle[StartRunPayload]) -> Applier[Launched]:
    dispatcher, payload, hop = dispatched.dispatcher, dispatched.payload, dispatched.hop
    return _launch_applier(
        dispatcher,
        lambda: launch(
            ExistingCycle(
                _diag_target(dispatcher.stores, hop, payload.mode),
                dispatched.campaign,
                payload.budget,
                step_rounds=payload.step_rounds,
            ),
            payload.mode,
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        ),
        refused_on=hop,
    )


async def fork_cycle(dispatched: DispatchedToCycle[ForkCyclePayload]) -> Applier[Launched]:
    dispatcher, payload, campaign, hop = (
        dispatched.dispatcher,
        dispatched.payload,
        dispatched.campaign,
        dispatched.hop,
    )
    stores = dispatcher.stores
    seed = parse_cycle_seed(payload.seed, stores, campaign, hop)

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
        # Before it runs: it spends the arm's ceiling and pauses its parent, so the campaign leaves.
        stores.campaigns.leave_comparison(fork, ComparisonExit.FORK)
        try:
            # Bounded as a start-run is: the payload's own budget, admitted from what the parent leaves.
            launched = await launch(
                ExistingCycle(fork, campaign, payload.budget),
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

    return _launch_applier(dispatcher, _apply_fork)


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


async def mint_campaign(dispatched: Dispatched[MintCampaignPayload]) -> Applier[Launched]:
    dispatcher, payload = dispatched.dispatcher, dispatched.payload

    async def _apply_mint() -> Launched:
        request = FreshMint(
            dataset=offered_dataset(dispatcher.stores, payload.dataset_name),
            budget=payload.budget,
            optimization=payload.optimization_sent,
            declared=payload.declared,
            task_text=payload.task_text,
        )
        # Refused here, before a slot is asked for: a queued mint would refuse it only later.
        request.campaign_config()
        return await launch(
            request,
            RunMode(diag=payload.diag),
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        )

    return _launch_applier(dispatcher, _apply_mint)


async def mint_head_to_head(
    dispatched: Dispatched[MintHeadToHeadPayload],
) -> Applier[HeadToHeadLaunched]:
    dispatcher, payload = dispatched.dispatcher, dispatched.payload
    stores, request = dispatcher.stores, payload.head_to_head

    async def _act() -> HeadToHeadLaunched:
        source = await dataset_arms(stores, offered_dataset(stores, payload.dataset_name))
        admitted = await admit_head_to_head(
            source,
            request,
            job_registry=dispatcher.job_registry,
            holder=CampaignLaunch(payload.dataset_name),
        )
        return await admitted.launched()

    return Applier(_act, effect_fn=lambda ran: LaunchedEffect(launches=ran.launches))


# A Start's answer: its admission, launched once its mint closes; or, answering a retry, the
# launch its first act recorded.
type StartAnswer = Admitted | AdmittedHeadToHead | Launched | HeadToHeadLaunched


async def start_checkin(dispatched: DispatchedToDraft[StartCheckinPayload]) -> Applier[StartAnswer]:
    dispatcher, start, claim = dispatched.dispatcher, dispatched.payload, dispatched.claim
    stores = dispatcher.stores

    async def _admit() -> Admitted:
        # Refused before the ack; the Start's own read waits for its slot (`_commit_checkin`).
        draft = owned_draft(stores, claim.draft_id)
        served = await draft_served(stores, draft)
        assert_origin_ready(draft, draft_pipeline(stores, draft, served))
        return admit(
            CheckinStart(claim=claim, dataset_name=draft.dataset_name, budget=start.budget),
            RunMode(diag=start.diag),
            stores=stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
        )

    async def _admit_arms(request: HeadToHeadRequest) -> AdmittedHeadToHead:
        # Its draft lands once every arm passed its refusals.
        source = await checkin_arms(stores, claim, request)
        return await admit_head_to_head(
            source,
            request,
            job_registry=dispatcher.job_registry,
            holder=CampaignLaunch(source.opened.draft.dataset_name, draft_id=claim.draft_id),
        )

    def _launches(answer: StartAnswer) -> CommandEffect:
        match answer:
            case Admitted(job=job):
                launches = [LaunchedJob.of(job)]
            case AdmittedHeadToHead(admitted=admitted):
                launches = [LaunchedJob.of(job) for job, _ in admitted]
            case Launched(launch=launch):
                launches = [launch]
            case HeadToHeadLaunched(launches=launched):
                launches = list(launched)
        return LaunchedEffect(launches=launches)

    def _withdraw(answer: StartAnswer, exc: BaseException) -> None:
        if isinstance(answer, Admitted | AdmittedHeadToHead):
            answer.withdraw(exc)

    # The claim and the slot ride the admitted launch to its mint; a second Start refuses while it
    # holds the claim, and is not found once it minted. A retry under its key answers the first
    # launch, minted or not (`relaunched`).
    async def _run() -> StartAnswer:
        request = start.head_to_head
        return await _admit() if request is None else await _admit_arms(request)

    return Applier(_run, effect_fn=_launches, ack="accepted", rides=_withdraw)


async def dispatch_start_checkin(
    stores: Stores,
    call: CommandCall[StartCheckinPayload],
    *,
    job_registry: JobRegistry | None = None,
    inline: Inline | None = None,
) -> Launched | HeadToHeadLaunched:
    """Acked at admission, before the mint moves the draft's ledger into the root cycle."""
    dispatcher = CommandDispatcher(stores, job_registry=job_registry, inline=inline)
    answer = (await dispatcher.answer(start_checkin, call)).result
    if isinstance(answer, Launched | HeadToHeadLaunched):
        return answer
    return await answer.launched()
