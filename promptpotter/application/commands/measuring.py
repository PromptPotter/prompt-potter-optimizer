from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.command_reading import (
    TERMINAL,
    command_status,
    launched_jobs,
    owned_command,
)
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    addressed,
)
from promptpotter.application.diagnostics import verify
from promptpotter.application.jobs.launcher.admission import PassHold, campaign_sends, paid_pass
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CancelQueuedRunPayload,
    CyclePayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.job import UNRESOLVED_HOP, InnerRun, Job
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_views import VerifyGradedView
from promptpotter.domain.phases import INNER_PAUSE_REFUSAL
from promptpotter.domain.run_records import (
    CommandEffect,
    NoEffect,
    VerifySaturated,
)
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_verify
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import owned_campaign
from promptpotter.shared.errors import NotFoundError, TaskUncancellableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched, DispatchedToCycle
    from promptpotter.domain.command_kinds import (
        CancelCommandPayload,
        CommandPayload,
        GradeBenchPayload,
        VerifyCandidatePayload,
    )
    from promptpotter.infrastructure.store.stores import Stores


def pass_pause(stores: Stores, payload: CommandPayload, *, reason: str) -> PauseCyclePayload:
    """The stop of a verify or bench pass in flight, as every door sends it: a pause of the cycle
    it scores — the cycle a verify names, the line holder a bench grades — at the address the
    dispatcher acts on (`addressed`), sent under the pass's own hold (`stop_pass`)."""
    match acted := addressed(stores, payload):
        case CyclePayload():
            return PauseCyclePayload(
                campaign_id=acted.campaign_id,
                cycle_id=acted.cycle_id,
                descend=acted.descend,
                reason=reason,
            )
        case LinePayload():
            campaign = owned_campaign(stores, acted.campaign_id)
            holder = stores.campaigns.line_holder(campaign.root_hop)
            return PauseCyclePayload(
                campaign_id=holder.campaign_id, cycle_id=holder.cycle_id, reason=reason
            )
        case _:
            raise TaskUncancellableError(f"no command stops a {payload.kind} in flight")


async def stop_pass(stores: Stores, hold: PassHold, pause: CommandCall[PauseCyclePayload]) -> None:
    """Every door's stop of a paid pass in flight: *pause*, sent under the pass's own hold
    (`PassHold.stopping`), so it reaches the pass or is refused, never a run holding the cycle after."""
    async with hold.stopping(stores, pause.payload):
        await CommandDispatcher(stores).dispatch(pause)


async def verify_candidate(
    dispatched: DispatchedToCycle[VerifyCandidatePayload],
) -> Applier[VerifyGradedView | VerifySaturated]:
    """Answers the graded pass, or what was already measured where that is every sample; a retry
    answers the candidate's pass as its cycle's ledger banked it, or the saturation its ack
    recorded, never a second pass."""
    stores, hop, payload = dispatched.dispatcher.stores, dispatched.hop, dispatched.payload

    async def _apply_verify() -> VerifyGradedView | VerifySaturated:
        # Taken HERE, not inside `verify_candidate`: the loop's saturation check calls that under its book.
        async with paid_pass(
            stores=stores,
            verb="verify",
            hop=hop,
            declared=Budget(),
            sends=campaign_sends(stores, hop),
        ):
            outcome = await verify.verify_candidate(
                stores=stores,
                hop=hop,
                candidate_id=payload.candidate_id,
                samples=payload.samples,
                strategy=payload.strategy,
                seed=payload.seed,
            )
        if outcome.verify_pass is None or outcome.reading is None:
            return VerifySaturated(
                dataset_name=outcome.dataset_name, already_measured=outcome.already_measured
            )
        return VerifyGradedView(verify_pass=outcome.verify_pass, reading=outcome.reading)

    async def _banked(effect: CommandEffect) -> VerifyGradedView | VerifySaturated:
        if isinstance(effect, VerifySaturated):
            return effect
        graded = scan_ledger_verify(stores.campaigns.ledger(hop)).graded
        if (banked := graded.get(ArmKey(payload.candidate_id))) is None:
            raise NotFoundError(f"{hop.cycle_id} banks no verify of {payload.candidate_id}")
        return VerifyGradedView(verify_pass=banked[0], reading=banked[1])

    def _effect(answer: VerifyGradedView | VerifySaturated) -> CommandEffect:
        return answer if isinstance(answer, VerifySaturated) else NoEffect()

    return Applier(_apply_verify, replay=_banked, effect_fn=_effect)


async def grade_bench(dispatched: DispatchedToCycle[GradeBenchPayload]) -> Applier[BenchScore]:
    """Answers the line's headline; a retry answers it as the cycle's export restated it."""
    stores, hop = dispatched.dispatcher.stores, dispatched.hop

    async def _restated(_: CommandEffect) -> BenchScore:
        export = stores.campaigns.read_export(hop)
        if export is None:
            raise NotFoundError(
                f"{hop.campaign_id}/{hop.cycle_id} holds no export to read its bench"
            )
        return export.bench

    return Applier(lambda: grade_line_bench(stores=stores, hop=hop), replay=_restated)


# What a cancel's pause and withdrawal say they were asked for.
_CANCELLED = "command cancelled"


def _job_cancels(jobs: list[Job], workspace: WorkspaceDir) -> list[CommandCall[CommandPayload]]:
    """Each live job's own cancel, resolved for every job before any is dispatched: a queued one
    withdrawn, a running run paused on its own cycle in *workspace*, each keyed by the job so a
    retried cancel acts once. A job no command of *workspace* cancels now — its cycle refusing the
    pause (`RunState.pause_refusal`) among them — refuses them all."""
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
        if job.workspace != str(workspace):
            raise TaskUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := derive_run_state(cycle_dir_for(workspace, job.hop)).pause_refusal:
            raise TaskUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.campaign_id, cycle_id=job.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key))
    return acts


async def cancel_command(dispatched: Dispatched[CancelCommandPayload]) -> Applier[object]:
    """The one stop of a command at every door, by its id: a pass in flight paused under its own
    hold (`stop_pass`), held by the process running it; a launch's jobs each by its own act; a
    command already ended, as its status reads it, left as it ended. Anything else acting is refused, nothing touched."""
    dispatcher, target = dispatched.dispatcher, dispatched.payload.command_id
    stores = dispatcher.stores

    async def _apply() -> None:
        state = owned_command(stores, target)
        kind = state.filed.kind
        if (jobs := launched_jobs(dispatcher.job_registry, state)) is not None:
            for act in _job_cancels(jobs, stores.campaigns.workspace):
                await dispatcher.dispatch(act)
            return
        if command_status(stores, dispatcher.job_registry, target).status in TERMINAL:
            return
        if state.status == "accepted":
            raise TaskUncancellableError(f"{kind} stands for its loop, which takes it")
        hold = PassHold.of(target)
        if hold is None:
            raise TaskUncancellableError(
                f"{kind} acts in a process that holds no stop of it here; ask again once it ends"
            )
        payload = PAYLOAD_MODEL_FOR_KIND[kind].model_validate(state.filed.payload)
        pause = pass_pause(stores, payload, reason=_CANCELLED)
        await stop_pass(stores, hold, CommandCall(pause, f"stop:{target}"))

    return Applier.silent(_apply)
