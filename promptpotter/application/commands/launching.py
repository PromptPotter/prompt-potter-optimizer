from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut
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
from promptpotter.application.datasets.authored import ConfigLayers
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
from promptpotter.application.jobs.launcher.held import HeldLaunch
from promptpotter.application.jobs.launcher.launch import (
    Admitted,
    Asked,
    Inline,
    Launched,
    LaunchOutcome,
    admit,
)
from promptpotter.application.jobs.launcher.mint_and_start import (
    ExistingCycle,
    ForkLaunch,
    FreshMint,
    assert_origin_ready,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    ServedPipeline,
    cycle_config,
    draft_pipeline,
    draft_served,
    permitted_narrowing,
    resolve_pipeline_for_campaign,
    resolved,
    steer_verdict,
)
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.campaign_config import CycleConfig, LaidSpan
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.command_effects import LaunchedEffect, LaunchedJob
from promptpotter.domain.command_events import CommandEffect
from promptpotter.domain.command_kinds import (
    CancelQueuedRunPayload,
    CommandKind,
    CommandPayload,
    Door,
    ForkCyclePayload,
    ForkStart,
    HeadToHeadRequest,
    LaunchPayload,
    LookaheadPress,
    MintCampaignPayload,
    MintHeadToHeadPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    StartCheckinPayload,
    StartRunPayload,
)
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun, InnerRun, Job
from promptpotter.domain.layer_fold import wire_value
from promptpotter.domain.phases import INNER_PAUSE_REFUSAL
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing, PipelineSchema, narrowing_of
from promptpotter.domain.records.lineage import ArmAct, CycleStart, OriginSource
from promptpotter.domain.run_mode import RunMode
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
    TaskUncancellableError,
)
from promptpotter.shared.identity import acting_principal, require_capability


def _typed_steer(overlay: dict[str, Any], schema: PipelineSchema) -> dict[str, NodeCall]:
    """Each steered value in its param's declared type at *schema*, as every door sends it
    (`layer_fold.py::wire_value`)."""
    typed: dict[str, NodeCall] = {}
    for node, cfg in overlay.items():
        declared = schema.get_node(node)
        if declared is None:
            names = ", ".join(n.name for n in schema.declared_nodes) or "(none)"
            raise PayloadInvalidError(
                f"payload.start steers no node called {node!r}; nodes: {names}"
            )
        params: dict[str, Any] = {}
        for param, value in cfg.items():
            kind = declared.param_types.get(param)
            if kind is None:
                known = ", ".join(sorted(declared.param_types)) or "(none)"
                raise PayloadInvalidError(
                    f"payload.start: {node!r} carries no {param!r}; params: {known}"
                )
            if isinstance(value, str):
                try:
                    value = wire_value(value, text=kind == "string")
                except ValueError as exc:
                    raise PayloadInvalidError(
                        f"payload.start steers {node}.{param}, declared {kind}: {exc}"
                    ) from None
            params[param] = value
        typed[node] = PayloadInvalidError.validating(
            NodeCall, params, under=("start", "pipeline_overlay", node)
        )
    return typed


def parse_cycle_start(sent: ForkStart, stores: Stores, at: CampaignCycle) -> CycleStart:
    of, hop = at.campaign, at.hop
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
            raise PayloadInvalidError(f"payload.start.node_narrowing invalid: {exc}") from exc
    verdict = steer_verdict(stores, permitted_narrowing(stores, at), sent.laid)
    if verdict == "refused":
        raise NotFoundError("Not found", code="not_found")
    overlay = _typed_steer(sent.laid, resolved(stores, at).declared) if sent.laid else {}
    for node, call in overlay.items():
        allowed = narrowing[node].param_allowed_values if node in narrowing else {}
        for param, value in call.laid_keys().items():
            if param in allowed and str(value) not in allowed[param]:
                raise PayloadInvalidError(
                    f"payload.start steers {node}.{param}={value!r} outside its own "
                    f"node_narrowing {allowed[param]}"
                )
    start = CycleStart(
        origin_prompts=sent.origin_prompts,
        pipeline_overlay=overlay,
        optimizer_narrowing=narrowing,
        config_overrides=sent.config_overrides,
        origin_source=OriginSource.FORK_START,
    )
    # Over what *hop* runs, so a knob its parent's start set refuses before a fork is minted.
    parent = cycle_config(stores, at).config
    select_optimizer(
        CycleConfig.laid(parent, [LaidSpan(start=start, scoring_lock=None)]).config.optimization
    )
    return start


def _launch_applier(
    dispatcher: CommandDispatcher,
    run: Callable[[], Awaitable[Launched]],
    *,
    refused_on: CycleHop | None = None,
) -> Applier[Launched]:
    """A launch's applier: its ack records the launch, which a retry under its key answers
    (`relaunched`) before *refused_on*'s start refusal is asked."""

    async def _run() -> Launched:
        if refused_on is not None and (
            refusal := dispatcher.stores.run_state(refused_on).start_refusal
        ):
            raise RejectedError(refusal)
        return await run()

    return Applier(_run, effect_fn=_launched_effect)


def _press_admitted(dispatcher: CommandDispatcher, payload: LaunchPayload) -> None:
    """A launch at depth needs the press's own rung, refused before anything is admitted."""
    if payload.lookahead is not None:
        kind = CommandKind.SET_SAMPLE_LOOKAHEAD
        require_capability(dispatcher.stores.identity, kind.capability, subject=f"command {kind}")


async def _held_at_depth(
    dispatcher: CommandDispatcher,
    door: Door,
    press: LookaheadPress | None,
    holding: Awaitable[HeldLaunch],
) -> HeldLaunch:
    """The launch's depth, filed as `set-sample-lookahead` on the cycle its run now holds: before
    the walk's first cell, so an origin's cells are held ahead too."""
    held = await holding
    if press is None:
        return held
    hop = held.hop
    pressed = SetSampleLookaheadPayload.model_validate(
        {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id, **press.model_dump()}
    )
    async with AsyncExitStack() as pressing:
        pressing.push_async_exit(held.closing)
        await dispatcher.dispatch(CommandCall(pressed, door=door))
        pressing.pop_all()
    return held


def _launched_effect(ran: LaunchOutcome) -> CommandEffect:
    """Every launch kind's ack: each job its act launched or admitted."""
    return LaunchedEffect(launches=ran.as_launches())


@replays(
    CommandKind.START_RUN,
    CommandKind.FORK_CYCLE,
    CommandKind.MINT_CAMPAIGN,
    CommandKind.HEAD_TO_HEAD,
    CommandKind.START_CHECKIN,
)
async def relaunched(replaying: Replaying[Any]) -> StartAnswer:
    """Every launch kind's retry: each job its first act's ack recorded, read live as
    `read_command` reads them (the cycle a queued mint took since that ack included), as the ack
    recorded them once a record is reaped; arm by arm where it asked a head-to-head, watching no
    exit (another act owns them)."""
    if not isinstance(effect := replaying.end.effect, LaunchedEffect):
        raise replaying.misread(f"a launch's ack names no job, its effect is {effect.kind!r}")
    recorded = effect.launches
    live = await asyncio.to_thread(
        replaying.jobs.launched_by,
        [launch.job_id for launch in recorded],
        principal_id=acting_principal(replaying.stores.identity).id,
    )
    launches = recorded if live is None else [LaunchedJob.of(job) for job in live]
    match replaying.payload:
        case MintHeadToHeadPayload(head_to_head=request):
            return head_to_head_answer(request, launches)
        case StartCheckinPayload(head_to_head=request) if request is not None:
            return head_to_head_answer(request, launches)
    (launch,) = launches
    return Launched(launch, held=None)


# What a cancel's pause of a launched run says it was asked for.
_CANCELLED = "command cancelled"


def launch_cancels(
    jobs: list[Job], stores: Stores, door: Door
) -> list[CommandCall[CommandPayload]]:
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
            acts.append(CommandCall(CancelQueuedRunPayload(job_id=job.job_id), key, door=door))
            continue
        if isinstance(job.holder, InnerRun):
            raise TaskUncancellableError(f"{job.what}: {INNER_PAUSE_REFUSAL}")
        if job.hop is None:
            raise TaskUncancellableError(f"{job.what} names no cycle to pause yet; cancel again")
        if job.workspace != str(stores.campaigns.workspace):
            raise TaskUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := stores.run_state(job.hop).pause_refusal:
            raise TaskUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.hop.campaign_id, cycle_id=job.hop.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key, door=door))
    return acts


async def start_run(dispatched: DispatchedToCycle[StartRunPayload]) -> Applier[Launched]:
    dispatcher, payload, hop = dispatched.dispatcher, dispatched.payload, dispatched.at.hop
    stores = dispatcher.stores
    _press_admitted(dispatcher, payload)

    async def _apply_start() -> Launched:
        # Refused before a slot is taken: a rewind past the last close is the operator's to fix.
        if (after := payload.mode.from_round) is not None:
            stores.campaigns.rewind_enters(hop, after)
        admitted = await asyncio.to_thread(
            admit,
            ExistingCycle(
                dispatched.at,
                payload.budget,
                step_rounds=payload.step_rounds,
                hosted=dispatcher.hosted,
            ),
            payload.mode,
            stores=stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
            asked=Asked(dispatched.command_id, dispatched.door),
        )
        held = _held_at_depth(dispatcher, dispatched.door, payload.lookahead, admitted.handed())
        return await admitted.hand_over(held)

    return _launch_applier(dispatcher, _apply_start, refused_on=hop)


async def fork_cycle(dispatched: DispatchedToCycle[ForkCyclePayload]) -> Applier[Launched]:
    dispatcher, payload, at = dispatched.dispatcher, dispatched.payload, dispatched.at
    stores = dispatcher.stores
    _press_admitted(dispatcher, payload)
    start = parse_cycle_start(payload.start, stores, at)

    async def _apply_fork() -> Launched:
        # Bounded as a start-run is: the payload's own budget, admitted from what the parent leaves.
        admitted = await asyncio.to_thread(
            admit,
            ForkLaunch(at, payload, start, payload.budget),
            payload.mode,
            stores=stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
            asked=Asked(dispatched.command_id, dispatched.door),
        )
        held = _held_fork(dispatched, admitted)
        return await admitted.hand_over(
            _held_at_depth(dispatcher, dispatched.door, payload.lookahead, held)
        )

    return _launch_applier(dispatcher, _apply_fork)


async def _held_fork(dispatched: Dispatched[ForkCyclePayload], admitted: Admitted) -> HeldLaunch:
    """Its parent untouched until the fork's run is held: a refused fork cut nothing, and leaves
    its parent's line, inbox, pointer and comparison as they stood. A held fork then leaves its
    parent and the comparison, BEFORE the cut is taken, so a fork that fails names no parent
    superseded; its opening's exit withdraws the cut."""
    dispatcher = dispatched.dispatcher
    held = await admitted.handed()
    cut = held.opening.cut
    assert cut is not None, "a fork's admission cut it"
    async with AsyncExitStack() as closing:
        closing.push_async_exit(held.closing)
        # It spends the arm's ceiling and moves its parent, so the campaign leaves.
        dispatcher.stores.campaigns.act(cut.parent, ArmAct.FORK)
        await _leave_parent(dispatcher, cut, dispatched.door)
        await asyncio.to_thread(held.opening.take_cut)
        closing.pop_all()
    return held


async def _leave_parent(dispatcher: CommandDispatcher, cut: ForkCut, door: Door) -> None:
    """On the fork's own authority over its parent: the parent's launch still queued is withdrawn,
    otherwise its run paused; a parent no pause reaches is left as it stands, its refusal the
    pause's own outcome, filed on the parent's log."""
    stores, parent = dispatcher.stores, cut.parent
    holder = stores.campaigns.holder(parent)
    job = None if holder is None else holder.job
    cancel = dispatcher.job_registry.cancel_queued
    if job is not None and await asyncio.to_thread(cancel, job.job_id) is not None:
        return
    pause = PauseCyclePayload(
        campaign_id=parent.campaign_id,
        cycle_id=parent.cycle_id,
        reason=f"superseded by fork {cut.child.cycle_id}",
    )
    with contextlib.suppress(ConflictError):
        await dispatcher.dispatch(CommandCall(pause, door=door))


async def mint_campaign(dispatched: Dispatched[MintCampaignPayload]) -> Applier[Launched]:
    dispatcher, payload = dispatched.dispatcher, dispatched.payload
    _press_admitted(dispatcher, payload)

    async def _apply_mint() -> Launched:
        dataset = offered_dataset(dispatcher.stores, payload.dataset_name)
        request = FreshMint(
            dataset=dataset,
            budget=payload.budget,
            layers=ConfigLayers.of(dataset, payload.campaign_config).over(
                payload.optimization_sent
            ),
            task_text=payload.task_text,
            step_rounds=payload.step_rounds,
            hosted=dispatcher.hosted,
        )
        # Refused here, before a slot is asked for: a queued mint would refuse it only later.
        request.campaign_config(dispatcher.stores)
        admitted = await asyncio.to_thread(
            admit,
            request,
            RunMode(),
            stores=dispatcher.stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
            asked=Asked(dispatched.command_id, dispatched.door),
        )
        held = _held_at_depth(dispatcher, dispatched.door, payload.lookahead, admitted.handed())
        return await admitted.hand_over(held)

    return _launch_applier(dispatcher, _apply_mint)


# A Start's answer, and a hosted head-to-head's: its admission, launched once its mint closes (a
# host's held, its arms minted in its own loop); or, answering a retry, the launch its first act
# recorded.
type StartAnswer = Admitted | AdmittedHeadToHead | Launched | HeadToHeadLaunched


async def _withdraw(answer: StartAnswer, exc: BaseException) -> None:
    if isinstance(answer, Admitted | AdmittedHeadToHead):
        await answer.withdraw(exc)


async def mint_head_to_head(
    dispatched: Dispatched[MintHeadToHeadPayload],
) -> Applier[AdmittedHeadToHead | HeadToHeadLaunched]:
    dispatcher, payload = dispatched.dispatcher, dispatched.payload
    stores, request, hosted = dispatcher.stores, payload.head_to_head, dispatcher.hosted
    name = payload.dataset_name

    async def _act() -> AdmittedHeadToHead | HeadToHeadLaunched:
        dataset = offered_dataset(stores, name)
        admitted = await admit_head_to_head(
            await dataset_arms(stores, dataset, payload.campaign_config),
            request,
            job_registry=dispatcher.job_registry,
            holder=CampaignLaunch(name) if hosted is None else EmbeddedRun(name),
        )
        return admitted if hosted is not None else await admitted.launched()

    return Applier(_act, effect_fn=_launched_effect, rides=_withdraw)


async def start_checkin(dispatched: DispatchedToDraft[StartCheckinPayload]) -> Applier[StartAnswer]:
    dispatcher, start, claim = dispatched.dispatcher, dispatched.payload, dispatched.claim
    stores = dispatcher.stores
    _press_admitted(dispatcher, start)

    async def _ready() -> tuple[DraftFile, ServedPipeline]:
        # Refused before the ack, on the Start's one backend read, which its open lands on.
        draft = owned_draft(stores, claim.draft_id)
        served = await draft_served(stores, draft)
        assert_origin_ready(draft, draft_pipeline(stores, draft, served))
        return draft, served

    async def _admit() -> Admitted:
        draft, served = await _ready()
        return await asyncio.to_thread(
            admit,
            CheckinStart(claim, draft.dataset_name, start.budget, served),
            RunMode(),
            stores=stores,
            job_registry=dispatcher.job_registry,
            inline=dispatcher.inline,
            asked=Asked(dispatched.command_id, dispatched.door),
        )

    async def _admit_arms(request: HeadToHeadRequest) -> AdmittedHeadToHead:
        # Its draft lands once every arm passed its refusals.
        source = await checkin_arms(stores, claim, request, (await _ready())[1])
        return await admit_head_to_head(
            source,
            request,
            job_registry=dispatcher.job_registry,
            holder=CampaignLaunch(source.opened.draft.dataset_name, draft_id=claim.draft_id),
        )

    # The claim and the slot ride the admitted launch to its mint; a second Start refuses while it
    # holds the claim, and is not found once it minted. A retry under its key answers the first
    # launch, minted or not (`relaunched`).
    async def _run() -> StartAnswer:
        request = start.head_to_head
        return await _admit() if request is None else await _admit_arms(request)

    return Applier(_run, effect_fn=_launched_effect, ack="accepted", rides=_withdraw)


async def launched(
    result: object, payload: CommandPayload, dispatcher: CommandDispatcher, door: Door
) -> Launched | HeadToHeadLaunched:
    """A launch kind's act carried past its ack: a Start's admission, minted and launched at the
    depth it asked, as every door that waits on the launch takes it."""
    if isinstance(result, Admitted):
        press = payload.lookahead if isinstance(payload, LaunchPayload) else None
        return await result.hand_over(_held_at_depth(dispatcher, door, press, result.handed()))
    if isinstance(result, AdmittedHeadToHead):
        return await result.launched()
    assert isinstance(result, Launched | HeadToHeadLaunched), "a launch kind answers its launch"
    return result


async def dispatch_start_checkin(
    stores: Stores,
    call: CommandCall[StartCheckinPayload],
    *,
    job_registry: JobRegistry | None = None,
    inline: Inline | None = None,
) -> Launched | HeadToHeadLaunched:
    """Acked at admission, before the mint moves the draft's ledger into the root cycle."""
    dispatcher = CommandDispatcher(stores, job_registry=job_registry, inline=inline)
    outcome = await dispatcher.answer(start_checkin, call)
    return await launched(outcome.result, outcome.payload, dispatcher, call.door)
