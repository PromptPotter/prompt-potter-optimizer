from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, assert_never

from promptpotter import connectors
from promptpotter.application.account import account_of
from promptpotter.application.initialization.loop_start import verb_ledger
from promptpotter.application.jobs.coupon import Send, bind_account
from promptpotter.application.jobs.quota import (
    admit_launch,
    check_launch_quotas,
    declare_run_budget,
    hold_moved,
    move_campaign_money,
    next_launch_budget,
    sends_on,
    share_campaign_ceiling,
    slot_rate,
)
from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.domain.command_kinds import PauseCyclePayload
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, decode_cycle_path
from promptpotter.domain.job import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    InnerRun,
    Job,
    ScoringPass,
    ScoringVerb,
    SlotHolder,
)
from promptpotter.domain.phases import (
    ErrorRecord,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import RunPhaseRecord
from promptpotter.domain.spend import (
    MONEY_AXES,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    HeldBudget,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.send_pacing import set_abort_check
from promptpotter.infrastructure.llm.spend_book import (
    Hold,
    LiveHold,
    SpendBook,
    bound_spend_book,
    spending_under,
)
from promptpotter.infrastructure.producer_lock import hold_pass, release_pass
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import (
    ConflictError,
    CycleBusyError,
    MachineBusyError,
    NotFoundError,
    TaskUncancellableError,
)
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import LaunchRequest
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.results import CycleResult

logger = logging.getLogger(__name__)


# A share's run: started under its hold, it answers what it ran and the result its job's release
# records.
type SharedRun[S] = Callable[[HeldBudget], Awaitable[tuple[S, CycleResult]]]


def release_slot(
    job_registry: JobRegistry,
    job_id: str,
    exc: BaseException,
    *,
    stores: Stores,
    hop: CycleHop | None,
) -> None:
    """The cycle's stop is the job's release's reading of *exc*: a refused one is ``NOT_ADMITTED``."""
    job = job_registry.release(job_id, exc)
    stop_reason = StopReason.NOT_ADMITTED if job is None or job.ending is None else job.ending
    # Best-effort — it must never mask *exc*.
    try:
        if hop is None:
            if job is not None and job.hop != UNRESOLVED_HOP:
                stores.campaigns.release_claim(job.hop, job_id=job_id, detail=str(exc))
            return
        stores.campaigns.declare_stop(
            hop,
            RunPhaseRecord.stop(
                stop_reason,
                cause=(
                    PauseCause.CANCELLED
                    if isinstance(exc, asyncio.CancelledError)
                    else PauseCause.INTERRUPT
                ),
                detail="the launch was interrupted before it held a run",
            ),
            error=(
                None
                if stop_reason_outcome(stop_reason) is StopOutcome.PAUSED
                else ErrorRecord.raised(exc, kind="launch_failed", stop_reason=stop_reason)
            ),
        )
    except Exception:
        logger.exception("failed to record launch stop for job %s", job_id)


async def probe_backend(backend_type: str) -> BackendUnreachableError | MissingExtraError | None:
    connector = connectors.CONNECTORS.get(backend_type)
    if connector.extra is not None and (absent := missing(connector.extra)):
        return MissingExtraError(connector.extra, absent)
    if (down := await connector.unreachable()) is not None:
        return BackendUnreachableError(connector.name, connector.base_url(), down)
    return None


def request_jobs(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    holder: SlotHolder,
    hop: CycleHop = UNRESOLVED_HOP,
    count: int = 1,
) -> list[Job]:
    """THE slot door of every launch, head-to-head and scoring pass: *count* jobs or none. Only a
    lone ask queues; N asked together never do, so no arm searches while a sibling waits on
    another machine. A rate token is debited per job returned, past every refusal but the queue's."""
    assert count == 1 or hop == UNRESOLVED_HOP, "jobs asked together mint their cycles"
    # ONE gate: the quota count and the slot count come off the same on-disk jobs.
    with job_registry.admission_gate(), slot_rate(stores, holder, hop, asked=count):
        # A scoring pass is load, never a campaign the concurrent and daily ceilings count.
        if isinstance(holder, CampaignLaunch):
            check_launch_quotas(
                user=account_of(stores),
                stores=stores,
                job_registry=job_registry,
                hop=hop,
                launches=count,
            )
        if hop != UNRESOLVED_HOP:
            _refuse_taken_cycle(stores, job_registry, hop)
        if count > 1 and not job_registry.has_room(count):
            raise _machine_busy(job_registry)
        jobs = [
            job_registry.request_slot(
                user_id=str(stores.identity.user_id),
                principal_id=acting_principal(stores.identity).id,
                workspace=stores.campaigns.workspace,
                holder=holder,
                hop=hop,
            )
            for _ in range(count)
        ]
        # A pass takes no claim: it never queues, and holds the cycle itself once admitted (`paid_pass`).
        if hop != UNRESOLVED_HOP and isinstance(holder, CampaignLaunch):
            claim_cycle(stores, job_registry, jobs[0], hop)
        return jobs


def _refuse_taken_cycle(stores: Stores, job_registry: JobRegistry, hop: CycleHop) -> None:
    """ONE check for launches and passes alike: a run attached, or a pass scoring it."""
    run = derive_run_state(stores.campaigns.cycle_dir(hop))
    # The pass first: it registers before it holds the cycle, and holding reads as attached.
    if (holder := job_registry.pass_on(stores.campaigns.workspace, hop)) is not None:
        status = RunPhase.SCORING.value
    elif run.producer.attached:
        claim = stores.campaigns.launch_claim(hop)
        holder = (
            job_registry.running_job_for(stores.campaigns.workspace, hop)
            if claim is None
            else job_registry.get(claim.job_id)
        )
        status = run.run_phase.value
    else:
        return
    raise CycleBusyError(
        job_id="unregistered" if holder is None else holder.job_id,
        status=status,
        holder_user="" if holder is None else holder.user_id,
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        started_at=None if holder is None else holder.started_at,
    )


def claim_cycle(stores: Stores, job_registry: JobRegistry, job: Job, hop: CycleHop) -> None:
    if job.stage is RunPhase.RUNNING:
        raise RuntimeError(f"job {job.job_id} is running: its cycle is held, not claimed")
    stores.campaigns.claim_launch(
        hop,
        stage=job.stage,
        job_id=job.job_id,
        claimant_lock=job_registry.claimant_lock(),
    )


def refuse_as_busy(stores: Stores, job_registry: JobRegistry, job: Job) -> NoReturn:
    # A REFUSAL takes its queue entry with it: one left behind starts the run later.
    withdraw_queued(stores, job_registry, job.job_id, principal_id=job.principal_id)
    raise _machine_busy(job_registry)


def _machine_busy(job_registry: JobRegistry) -> MachineBusyError:
    oldest = next(iter(job_registry.list_running()), None)
    return MachineBusyError(
        holding="" if oldest is None else oldest.what,
        holder_user="" if oldest is None else oldest.user_id,
        started_at=None if oldest is None else oldest.started_at,
    )


def withdraw_queued(
    stores: Stores, job_registry: JobRegistry, job_id: str, *, principal_id: str
) -> bool:
    job = job_registry.get(job_id)
    if not job_registry.cancel_queued(job_id, principal_id=principal_id):
        return False
    if job is not None and job.hop != UNRESOLVED_HOP:
        stores.campaigns.release_claim(job.hop, job_id=job_id, detail="withdrawn from the queue")
    return True


async def admit_and_hold(
    request: LaunchRequest, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldBudget:
    """The request's config is resolved IN here, after the queue wait, so the budget admitted is the one the run starts under."""
    # A fresh mint has no cycle yet: nothing to claim, and no seed or standing ceiling to read.
    hop = None if request.hop == UNRESOLVED_HOP else request.hop
    if job.queued:
        await job_registry.await_claim(job.job_id)
        promoted = job_registry.get(job.job_id)
        if hop is not None and promoted is not None:
            claim_cycle(stores, job_registry, promoted, hop)

    t0 = time.perf_counter()
    if (down := await probe_backend(request.backend_type(stores))) is not None:
        raise down
    t_probe = time.perf_counter()
    declared, asked = declare_run_budget(
        request.declared_budget(stores), stores=stores, hop=hop, requested=request.budget
    )
    # Offloaded: the wallet read scans every cycle ledger and must not block the event loop.
    held = await asyncio.to_thread(
        admit_and_share,
        declared,
        asked,
        stores=stores,
        job_registry=job_registry,
        job=job,
        hop=hop,
        step_rounds=request.step_rounds,
        sends=request.sends(stores),
    )
    t_caps = time.perf_counter()

    logger.info(
        "admission[%s %s]: probe=%.2fs wallet=%.2fs (job %s)",
        request.verb,
        request.dataset_name,
        t_probe - t0,
        t_caps - t_probe,
        job.job_id,
    )
    return held


def admit_and_share(
    declared: Budget,
    asked: Budget,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job,
    hop: CycleHop | None,
    step_rounds: int | None,
    sends: tuple[Send, ...],
    enclosing: Budget | None = None,
) -> HeldBudget:
    """THE admission of every run and pass, sending *sends*; an inner run is admitted against its
    *enclosing* book, whose reserve binds what it sends."""
    meter: CeilingMeter = (
        "bill" if hop is None else stores.campaigns.ceiling_meter_of(hop.campaign_id)
    )
    with job_registry.admission_gate():
        if enclosing is None:
            admitted, reserve = admit_launch(
                declared=declared,
                user=account_of(stores),
                stores=stores,
                job_registry=job_registry,
                job_id=job.job_id,
                hop=hop,
                sends=sends,
            )
            # The campaign's ceiling as composed, never as a delegate's claim clamped it: the
            # account binds through the reservation.
            ceiling = admitted.over(
                Budget(usd=admitted.usd if declared.usd is None else declared.usd)
            )
        else:
            # The enclosing book's room is spendable from NOW, on top of what the campaign spent.
            spent = (
                NOTHING_METERED
                if hop is None
                else stores.campaigns.metered_spend(hop.campaign_id, meter)
            )
            room = Budget(
                usd=None if enclosing.usd is None else enclosing.usd + spent.usd_amount,
                tokens=None if enclosing.tokens is None else enclosing.tokens + spent.token_amount,
            )
            admitted, reserve = declared.over(_within(declared, room)), Budget()
            ceiling = admitted
        allotment = share_campaign_ceiling(
            ceiling, stores=stores, job_registry=job_registry, job=job, hop=hop, meter=meter
        )
        job_registry.mark_admitted(job.job_id, reserve)
        if hop is not None:
            hold_moved(stores, hop, allotment)
            move_campaign_money(
                asked, stores=stores, job_registry=job_registry, campaign_id=hop.campaign_id
            )
    return HeldBudget(
        admitted=admitted,
        standing=asked,
        job_id=job.job_id,
        share=allotment,
        step_rounds=step_rounds,
    )


def admit_arms(
    declared: Sequence[tuple[Budget, tuple[Send, ...]]],
    *,
    stores: Stores,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> list[tuple[Job, HeldBudget]]:
    """Every arm's job and share, each at its budget and sends, or none, under one gate. An
    embedded host holds no slot (`embedded_share`)."""
    with job_registry.admission_gate():
        jobs: list[Job] = []
        try:
            jobs = _arm_jobs(stores, job_registry, holder, count=len(declared))
            admitted = [
                (
                    job,
                    admit_and_share(
                        budget,
                        Budget(),
                        stores=stores,
                        job_registry=job_registry,
                        job=job,
                        hop=None,
                        step_rounds=None,
                        sends=sends,
                    ),
                )
                for job, (budget, sends) in zip(jobs, declared, strict=True)
            ]
        except BaseException as exc:
            # Inside the gate, so no admission between reads the arms a refusal takes back.
            for refused in jobs:
                release_slot(job_registry, refused.job_id, exc, stores=stores, hop=None)
            raise
    return admitted


def _arm_jobs(
    stores: Stores, job_registry: JobRegistry, holder: CampaignLaunch | EmbeddedRun, *, count: int
) -> list[Job]:
    match holder:
        case CampaignLaunch():
            jobs = request_jobs(
                stores=stores, job_registry=job_registry, holder=holder, count=count
            )
            # A lone arm the door queued: an arm never waits on another machine.
            if jobs[0].queued:
                refuse_as_busy(stores, job_registry, jobs[0])
            return jobs
        case EmbeddedRun():
            return [
                job_registry.create(
                    user_id=str(stores.identity.user_id),
                    principal_id=acting_principal(stores.identity).id,
                    workspace=stores.campaigns.workspace,
                    hop=UNRESOLVED_HOP,
                    holder=holder,
                )
                for _ in range(count)
            ]
        case _:
            assert_never(holder)


def _within(declared: Budget, enclosing: Budget) -> Budget:
    usd = [v for v in (declared.usd, enclosing.usd) if v is not None]
    tokens = [v for v in (declared.tokens, enclosing.tokens) if v is not None]
    return Budget(usd=min(usd, default=None), tokens=min(tokens, default=None))


def open_spend_book(
    held: HeldBudget,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop | None,
    meters: CeilingMeter,
    opened: float,
    live: bool,
) -> SpendBook:
    """On a cycle its share is read live off the cycle's record, which a reshare moves, and its
    reserve off the job; off one, as admitted. A *live* book reads the cycle's standing axes too."""
    hold: Hold | LiveHold
    if hop is None:
        job = job_registry.get(held.job_id)
        if job is None:
            raise RuntimeError(f"a book opens only on its own job, and {held.job_id} is gone")
        hold = Hold(held.share, job.reserve)
    else:
        hold = LiveHold(stores.campaigns.cycle_dir(hop), job_registry.job_file(held.job_id))
    return SpendBook(
        declared=held.admitted,
        hold=hold,
        meters=meters,
        opened=opened,
        cycle_dir=stores.campaigns.cycle_dir(hop) if live and hop is not None else None,
        metered=NOTHING_METERED if hop is None else stores.campaigns.cycle_metered(hop, meters),
    )


async def embedded_share[S](
    config: CampaignConfig,
    *,
    stores: Stores,
    dataset_name: str,
    hop: CycleHop | None,
    requested: Budget,
    step_rounds: int | None,
    sends: tuple[Send, ...],
    run: SharedRun[S],
) -> tuple[S, CycleResult]:
    """A launch's admission with no slot, taken BEFORE anything paid: the job, then its hold, then
    *run* under it, whose result the job's release records."""
    job_registry = JobRegistry.attach(stores.jobs_dir)
    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=UNRESOLVED_HOP if hop is None else hop,
        holder=EmbeddedRun(dataset_name),
    )
    try:
        declared, asked = declare_run_budget(
            config.optimization.budget, stores=stores, hop=hop, requested=requested
        )
        held = await asyncio.to_thread(
            admit_and_share,
            declared,
            asked,
            stores=stores,
            job_registry=job_registry,
            job=job,
            hop=hop,
            step_rounds=step_rounds,
            sends=sends,
        )
        job_registry.mark_started(job.job_id)
        ran = await run(held)
    except BaseException as exc:
        job_registry.release(job.job_id, exc)
        raise
    job_registry.release(job.job_id, ran[1].stop_reason, stepped=ran[1].stepped)
    return ran


async def inner_share[S](
    config: CampaignConfig, *, stores: Stores, dataset_name: str, hop: CycleHop, run: SharedRun[S]
) -> tuple[S, CycleResult]:
    """An L4 inner campaign's share, of what the enclosing run's book has left at its spawn, and
    *run* under it, whose result the job's release records."""
    enclosing = bound_spend_book()
    if enclosing is None:
        raise RuntimeError("an inner run spawns only under its enclosing run's book")
    job_registry = JobRegistry.attach(stores.jobs_dir)
    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=hop,
        holder=InnerRun(dataset_name),
    )
    try:
        declared, asked = declare_run_budget(
            config.optimization.budget, stores=stores, hop=hop, requested=Budget()
        )
        held = admit_and_share(
            declared,
            asked,
            stores=stores,
            job_registry=job_registry,
            job=job,
            hop=hop,
            step_rounds=None,
            sends=(),
            enclosing=enclosing.money_left(),
        )
        job_registry.mark_started(job.job_id)
        ran = await run(held)
    except BaseException as exc:
        job_registry.release(job.job_id, exc)
        raise
    job_registry.release(job.job_id, ran[1].stop_reason, stepped=ran[1].stepped)
    return ran


class PassStoppedError(ConflictError):
    """A pause reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


class PassHold:
    """A paid pass's hold of the cycle it scores, the one truth its stop reads: unheld until the
    pass holds the cycle, held to its let-go, then ended. The hold, its let-go and a stop each act
    under the hold's own lock, so a stop lands while the pass holds its cycle or not at all."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cycle_dir: Path | None = None
        self._ended = False

    async def take(self, cycle_dir: Path) -> None:
        async with self._lock:
            hold_pass(cycle_dir)
            self._cycle_dir, self._ended = cycle_dir, False

    async def let_go(self, stores: Stores, holding: CycleHop | None) -> str | None:
        """Applies a pause asked of the pass, however it ended, before the cycle is let go; who asked."""
        async with self._lock:
            try:
                return None if holding is None else _take_pause(stores, holding)
            finally:
                if self._cycle_dir is not None:
                    release_pass(self._cycle_dir)
                self._cycle_dir, self._ended = None, True

    @asynccontextmanager
    async def stopping(self, stores: Stores, pause: PauseCyclePayload) -> AsyncIterator[None]:
        """Where *pause* addresses the cycle this pass holds, its body sends it before any let-go:
        a stop never lands on a run holding the cycle after. Refused before the pass holds it, when
        nothing is spent yet, and once it let go."""
        hop = CycleHop(campaign_id=pause.campaign_id, cycle_id=pause.cycle_id)
        leaf_stores, leaf = await asyncio.to_thread(
            resolve_cycle_path, stores, (hop, *decode_cycle_path(pause.descend))
        )
        async with self._lock:
            if self._ended:
                raise TaskUncancellableError("the pass ended: nothing is left to stop")
            if self._cycle_dir != leaf_stores.campaigns.cycle_dir(leaf):
                raise TaskUncancellableError(
                    f"the pass holds {leaf.cycle_id} not yet, so no pause stops it; ask again"
                )
            yield

    @contextmanager
    def bound(self) -> Iterator[None]:
        """A paid pass run inside this context holds its cycle through this hold, which the caller
        that may stop it keeps."""
        token = _BOUND_HOLD.set(self)
        try:
            yield
        finally:
            _BOUND_HOLD.reset(token)


# The hold of the paid pass a caller's task runs, bound by the caller that may stop it.
_BOUND_HOLD: ContextVar[PassHold | None] = ContextVar("pass_hold", default=None)


@asynccontextmanager
async def paid_pass(
    *,
    stores: Stores,
    verb: ScoringVerb,
    hop: CycleHop | None,
    declared: Budget,
    sends: tuple[Send, ...],
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer. On a cycle it holds it, so the one pause stops it."""
    job_registry = JobRegistry.attach(stores.jobs_dir)
    (job,) = await asyncio.to_thread(
        request_jobs,
        stores=stores,
        job_registry=job_registry,
        holder=ScoringPass(verb),
        hop=UNRESOLVED_HOP if hop is None else hop,
    )
    if job.queued:
        refuse_as_busy(stores, job_registry, job)
    holding: CycleHop | None = None
    pass_hold = _BOUND_HOLD.get() or PassHold()
    try:
        meters: CeilingMeter = (
            "bill" if hop is None else stores.campaigns.ceiling_meter_of(hop.campaign_id)
        )
        if hop is not None:
            cycle_dir = stores.campaigns.cycle_dir(hop)
            await pass_hold.take(cycle_dir)
            holding = hop
            # Asked of a producer gone since, as a launch's RUNNING record empties the inbox.
            _take_pause(stores, hop)
            # The cycle's own file, never a bound ledger: the bracket is scanned there alone.
            RunCallbacks(CycleEventLog.open(CycleDir(cycle_dir))).close_dead_bench()
            # The rate-limit and slot waits poll it too, as a loop's do.
            set_abort_check(RunControl(cycle_dir=cycle_dir).pause_requested)
            ceiling = await asyncio.to_thread(_campaign_ceiling, stores, hop)
            declared = ceiling.over(declared)
        held = await asyncio.to_thread(
            admit_and_share,
            declared,
            Budget(),
            stores=stores,
            job_registry=job_registry,
            job=job,
            hop=hop,
            step_rounds=None,
            sends=sends,
        )
        book = open_spend_book(
            held,
            stores=stores,
            job_registry=job_registry,
            hop=hop,
            meters=meters,
            opened=time.monotonic(),
            live=False,
        )
        job_registry.mark_started(job.job_id)
        unbind = bind_account(stores)
        try:
            with spending_under(book):
                yield book
        finally:
            unbind()
    except BaseException as exc:
        job_registry.release(job.job_id, exc)
        stopped_by = await pass_hold.let_go(stores, holding)
        if stopped_by is not None and launch_interrupted(exc):
            raise PassStoppedError(
                f"The {verb} pass stopped: {stopped_by}. Every cell it measured is banked, and a "
                "rerun replays them."
            ) from None
        raise
    job_registry.release(job.job_id)
    await pass_hold.let_go(stores, holding)


def _take_pause(stores: Stores, hop: CycleHop) -> str | None:
    """Acks the pause standing on *hop* applied; who asked it, ``None`` where none stands."""
    control = RunControl(cycle_dir=stores.campaigns.cycle_dir(hop))
    if not control.pause_requested():
        return None
    with verb_ledger(stores, hop):
        taken = control.take_pause()
    return None if taken is None else taken[1]


def _campaign_ceiling(stores: Stores, hop: CycleHop) -> Budget:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    config = cycle_config(stores, campaign, hop).config
    return next_launch_budget(config, stores=stores, hop=hop).only(MONEY_AXES)


def campaign_sends(stores: Stores, hop: CycleHop) -> tuple[Send, ...]:
    """What a run or pass on *hop* sends, under its campaign's resolved config and connector."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    config = cycle_config(stores, campaign, hop).config
    return sends_on(stores, config, campaign.connector)
