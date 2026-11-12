from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, NoReturn, assert_never

from promptpotter import connectors
from promptpotter.application.account import account_of
from promptpotter.application.jobs.coupon import Send
from promptpotter.application.jobs.quota import (
    admit_launch,
    call_book,
    check_launch_quotas,
    declare_run_budget,
    hold_moved,
    hold_run,
    land_job,
    move_campaign_money,
    next_launch_budget,
    release_run,
    sends_on,
    share_campaign_ceiling,
    slot_rate,
)
from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.run_phase_control import CycleHold, close_dead_bench
from promptpotter.domain.campaign import CampaignRefusal, CampaignRefusedError
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.job import (
    UNRESOLVED_HOP,
    CallBucket,
    CampaignLaunch,
    EmbeddedRun,
    InnerRun,
    Job,
    PaidCall,
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
from promptpotter.infrastructure.llm.spend_book import LiveCycle, RunHold, SpendBook
from promptpotter.infrastructure.store.dataset_access import (
    definition_version,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    ConflictError,
    CycleBusyError,
    DatasetAccessError,
    FileUnreadableError,
    MachineBusyError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal
from promptpotter.shared.run_scope import run_scope, scoped

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import LaunchRequest
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.results import CycleResult

logger = logging.getLogger(__name__)


# A share's run: started under its hold, it answers what it ran and the result its job's release
# records.
type SharedRun[S] = Callable[[HeldBudget], Awaitable[tuple[S, CycleResult]]]


@dataclass(frozen=True, slots=True)
class RunStart:
    """A job's slot, from its admission to its one release: every launch, arm, slotless share and
    paid pass holds one."""

    stores: Stores
    job_registry: JobRegistry
    job: Job

    @property
    def job_id(self) -> str:
        return self.job.job_id

    def release(
        self, ended: CycleResult | BaseException | None = None, *, stamps: CycleHop | None = None
    ) -> None:
        """A raise is the cycle's stop on *stamps* (a refused one ``NOT_ADMITTED``), and off a
        cycle the claim goes; a result is what the run returned; nothing, a pass that returned. An
        admitted job's hold on the account closes with it."""
        match ended:
            case BaseException():
                job = self.job_registry.release(self.job_id, ended)
            case None:
                job = self.job_registry.release(self.job_id)
            case _:
                job = self.job_registry.release(
                    self.job_id, ended.stop_reason, stepped=ended.stepped
                )
        if job is not None and job.admitted:
            release_run(self.stores, self.job_id)
        if isinstance(ended, BaseException):
            self._fail(job, ended, stamps)

    def land(self, hop: CycleHop, held: HeldBudget) -> None:
        """The job onto the cycle its hold just minted, its share with it; a slotted job claims the
        cycle off the job as it stands now: promoted out of the queue, its stage the one the cycle
        reads."""
        land_job(self.stores, self.job_registry, self.job_id, hop, held.share)
        if not isinstance(self.job.holder, CampaignLaunch):
            return
        promoted = self.job_registry.get(self.job_id)
        if promoted is None:
            raise RuntimeError(f"job {self.job_id} was cleared while its cycle was minted")
        claim_cycle(self.stores, self.job_registry, promoted, hop)

    def _fail(self, job: Job | None, exc: BaseException, stamps: CycleHop | None) -> None:
        stop_reason = StopReason.NOT_ADMITTED if job is None or job.ending is None else job.ending
        stores = self.stores
        # Best-effort — it must never mask *exc*.
        try:
            if stamps is None:
                if job is not None and job.hop != UNRESOLVED_HOP:
                    stores.campaigns.release_claim(job.hop, job_id=self.job_id, detail=str(exc))
                return
            stores.declare_stop(
                stamps,
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
            logger.exception("failed to record launch stop for job %s", self.job_id)


async def probe_backend(backend_type: str) -> BackendUnreachableError | MissingExtraError | None:
    connector = connectors.CONNECTORS.get(backend_type)
    if connector.extra is not None and (absent := missing(connector.extra)):
        return MissingExtraError(connector.extra, absent)
    if (down := await connector.unreachable()) is not None:
        return BackendUnreachableError(connector.name, connector.base_url(), down)
    return None


class _DefinitionRead(NamedTuple):
    """The connector a dataset's definition names, read as the mint read it, or why it reads as
    none (``connector`` empty then)."""

    connector: str
    unread: str | None


def _definition_read(stores: Stores, name: str) -> _DefinitionRead:
    """Derived on the definition's stats, so a served read parses it once per edit."""

    def read() -> _DefinitionRead:
        try:
            named = runnable_definition(resolve_dataset(stores, name)).backend_type
        except (DatasetAccessError, FileUnreadableError, PayloadInvalidError) as unread:
            return _DefinitionRead("", str(unread))
        return _DefinitionRead(named, None)

    return derived(
        ("definition_read", str(stores.identity.tenant_id), name, stores.research_datasets_root),
        sig=definition_version(stores, name),
        compute=read,
    )


def campaign_refusal(stores: Stores, campaign: Campaign) -> CampaignRefusedError | None:
    """THE campaign's own refusal, a pure read of it and of its dataset's definition (read as the
    mint read it), answered, never raised: admission raises it (`refuse_campaign`), and a served
    can-run answer serves its `refusal` (`runner/bench.py::BenchLine.refused`)."""
    if not campaign.active:
        return CampaignRefusedError(
            CampaignRefusal.NOT_ACTIVE,
            f"campaign {campaign.campaign_id} is {campaign.lifecycle_status}, so nothing runs on "
            "it: restore it, or start a fresh campaign",
        )
    read = _definition_read(stores, campaign.dataset_name)
    if read.unread is not None:
        return CampaignRefusedError(
            CampaignRefusal.DEFINITION_UNREAD,
            f"campaign {campaign.campaign_id} runs on {campaign.dataset_name}, which reads as no "
            f"definition naming a connector, so nothing ran: {read.unread}",
        )
    if (named := read.connector) != campaign.connector:
        return CampaignRefusedError(
            CampaignRefusal.CONNECTOR_MOVED,
            f"campaign {campaign.campaign_id} was minted on {campaign.connector}, and "
            f"{campaign.dataset_name} now names {named}, so nothing ran: its measurements are "
            f"{campaign.connector}'s. Point {campaign.dataset_name} back at {campaign.connector}, "
            "or start a fresh campaign on it; this one is preserved.",
        )
    return None


def refuse_campaign(stores: Stores, campaign: Campaign) -> None:
    """Asked of every run and pass on *campaign* before anything is queued, claimed, probed or
    bound: first under the slot door's gate (`request_jobs`) or, for an embedded resume, before
    its session opens (`embedded_run.py`), and again past a queue's wait or a detached run's
    handoff. The inner door asks `campaign_refusal` itself: its refusal is a cell's."""
    if (refused := campaign_refusal(stores, campaign)) is not None:
        raise refused


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
        # First, under the gate an archive or a delete takes too: a refused campaign's act is
        # queued, claimed and debited nothing.
        if hop != UNRESOLVED_HOP:
            refuse_campaign(stores, campaign_at(stores, hop))
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
            refuse_taken_cycle(stores, job_registry, hop)
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


@contextmanager
def destroying(
    stores: Stores, job_registry: JobRegistry, campaign_id: str, *, cycle_id: str | None = None
) -> Iterator[None]:
    """THE guard of every destroyer (a campaign's archive or delete, a stub's, a refused fork's
    cut, a landing's torn mint): under the admission gate, no cycle it destroys is taken, so no
    launch or pass is admitted between the check and the act. *cycle_id* ``None`` destroys every
    cycle of *campaign_id*, listed under the gate."""
    with job_registry.admission_gate():
        cycle_ids = (
            [d.name for d in stores.campaigns.campaign_cycle_dirs(campaign_id)]
            if cycle_id is None
            else [cycle_id]
        )
        for one in cycle_ids:
            refuse_taken_cycle(
                stores, job_registry, CycleHop(campaign_id=campaign_id, cycle_id=one)
            )
        yield


def deletable_stubs(stores: Stores, job_registry: JobRegistry, hop: CycleHop) -> list[CycleHop]:
    """What `cleanup-empty-cycles` at *hop* deletes now, the count `/hierarchy` serves and the list
    the command walks alike: its root's stub forks (`CampaignStore.stub_forks`) that no run or pass
    holds (`refuse_taken_cycle`). The act asks again, under the gate (`destroying`)."""
    free: list[CycleHop] = []
    for stub in stores.campaigns.stub_forks(hop):
        try:
            refuse_taken_cycle(stores, job_registry, stub)
        except CycleBusyError:
            continue
        free.append(stub)
    return free


def refuse_taken_cycle(stores: Stores, job_registry: JobRegistry, hop: CycleHop) -> None:
    """ONE check for launches, passes and destroyers alike, asked under the admission gate: a run
    attached, or a pass scoring it."""
    run = stores.run_state(hop)
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
    withdraw_queued(stores, job_registry, job.job_id)
    raise _machine_busy(job_registry)


def _machine_busy(job_registry: JobRegistry) -> MachineBusyError:
    oldest = next(iter(job_registry.list_running()), None)
    return MachineBusyError(
        holding="" if oldest is None else oldest.what,
        holder_user="" if oldest is None else oldest.user_id,
        started_at=None if oldest is None else oldest.started_at,
    )


def withdraw_queued(stores: Stores, job_registry: JobRegistry, job_id: str) -> bool:
    if (withdrawn := job_registry.cancel_queued(job_id)) is None:
        return False
    if withdrawn.hop != UNRESOLVED_HOP:
        stores.campaigns.release_claim(
            withdrawn.hop, job_id=job_id, detail="withdrawn from the queue"
        )
    return True


async def admit_and_hold(request: LaunchRequest, start: RunStart) -> HeldBudget:
    """The request's config is resolved IN here, after the queue wait, so the budget admitted is the one the run starts under."""
    stores, job_registry, job = start.stores, start.job_registry, start.job
    # A fresh mint has no cycle yet: nothing to claim, and no seed or standing ceiling to read.
    hop = None if request.hop == UNRESOLVED_HOP else request.hop
    if job.queued:
        await job_registry.await_claim(job.job_id)
        if hop is not None:
            # Asked again past the wait: the slot door's claim held off an archive or a delete,
            # never a definition moved while the job waited.
            refuse_campaign(stores, campaign_at(stores, hop))
            if (promoted := job_registry.get(job.job_id)) is not None:
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
    enclosing: SpendBook | None = None,
) -> HeldBudget:
    """THE admission of every run and pass, sending *sends*, and its hold on the account; an inner
    run is admitted against what its *enclosing* book has left, whose hold binds what it sends."""
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
            left = enclosing.money_left()
            room = Budget(
                usd=None if left.usd is None else left.usd + spent.usd_amount,
                tokens=None if left.tokens is None else left.tokens + spent.token_amount,
            )
            admitted, reserve = declared.over(_within(declared, room)), Budget()
            ceiling = admitted
        allotment = share_campaign_ceiling(
            ceiling, stores=stores, job_registry=job_registry, job=job, hop=hop, meter=meter
        )
        hold_run(
            stores, job.job_id, reserve, parent=None if enclosing is None else enclosing.hold_id
        )
        job_registry.mark_admitted(job.job_id)
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
    embedded host holds no slot (`slotless_share`)."""
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
                RunStart(stores, job_registry, refused).release(exc)
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
    hop: CycleHop | None,
    meters: CeilingMeter,
    opened: float,
    live: bool,
) -> SpendBook:
    """On a cycle its share is read live off the cycle's record, which a reshare moves; off one, as
    admitted. Its reserve is its hold's on the account, which a set-limits restates. A *live* book
    reads the cycle's standing axes too."""
    book = SpendBook(
        declared=held.admitted,
        share=held.share if hop is None else LiveCycle(stores.campaigns, hop),
        reserve=RunHold(stores.charges, held.job_id),
        hold_id=held.job_id,
        meters=meters,
        opened=opened,
        cycle=LiveCycle(stores.campaigns, hop) if live and hop is not None else None,
    )
    if hop is None:
        return book
    left = book.take_up(stores.charges, stores.campaigns.ledger(hop)).total
    if left.unreported_usd or left.unreported_tokens:
        logger.warning(
            "paid sends on %s reported no bill; the ceiling holds up to $%.4f and %d tokens for "
            "them as unreported.",
            hop,
            left.unreported_usd,
            left.unreported_tokens,
        )
    return book


async def slotless_share[S](
    config: CampaignConfig,
    *,
    stores: Stores,
    holder: EmbeddedRun | InnerRun,
    hop: CycleHop | None,
    requested: Budget,
    step_rounds: int | None,
    sends: tuple[Send, ...],
    run: SharedRun[S],
) -> tuple[S, CycleResult]:
    """A run's admission with no slot, taken BEFORE anything paid: the job, then its hold, then
    *run* under it, whose result the job's release records. An inner run's share is of what the
    enclosing run's book has left at its spawn."""
    enclosing = run_scope().book if isinstance(holder, InnerRun) else None
    if isinstance(holder, InnerRun) and enclosing is None:
        raise RuntimeError("an inner run spawns only under its enclosing run's book")
    job_registry = JobRegistry.attach(stores.jobs_dir)
    start = RunStart(
        stores,
        job_registry,
        job_registry.create(
            user_id=str(stores.identity.user_id),
            principal_id=acting_principal(stores.identity).id,
            workspace=stores.campaigns.workspace,
            hop=UNRESOLVED_HOP if hop is None else hop,
            holder=holder,
        ),
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
            job=start.job,
            hop=hop,
            step_rounds=step_rounds,
            sends=sends,
            enclosing=enclosing,
        )
        job_registry.mark_started(start.job_id)
        ran = await run(held)
    except BaseException as exc:
        start.release(exc)
        raise
    start.release(ran[1])
    return ran


def _admit_call(stores: Stores, holder: PaidCall, provider: str) -> tuple[RunStart, SpendBook]:
    job_registry = JobRegistry.attach(stores.jobs_dir)
    with job_registry.admission_gate(), slot_rate(stores, holder, UNRESOLVED_HOP, asked=1):
        start = RunStart(
            stores,
            job_registry,
            job_registry.create(
                user_id=str(stores.identity.user_id),
                principal_id=acting_principal(stores.identity).id,
                workspace=stores.campaigns.workspace,
                hop=UNRESOLVED_HOP,
                holder=holder,
            ),
        )
        try:
            book = call_book(stores, provider, job_registry=job_registry, hold_id=start.job_id)
            # Unbounded: it holds what its calls out hold (`Money.held_back`), and closes with its job.
            hold_run(stores, start.job_id, Budget(), parent=None)
            job_registry.mark_admitted(start.job_id)
        except BaseException as exc:
            start.release(exc)
            raise
    return start, book


@asynccontextmanager
async def paid_call(
    *, stores: Stores, bucket: CallBucket, provider: str
) -> AsyncIterator[SpendBook]:
    """ONE LLM call on *provider*, on no cycle, slotless so a chat turn never queues behind
    campaigns: a job whose hold its sends draw on, closed with it or by its dead producer's reap."""
    # Offloaded: admission globs every cycle ledger.
    start, book = await asyncio.to_thread(_admit_call, stores, PaidCall(bucket), provider)
    try:
        with scoped(account=stores, book=book):
            yield book
    except BaseException as exc:
        start.release(exc)
        raise
    start.release()


class PassStoppedError(ConflictError):
    """A pause reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


@asynccontextmanager
async def paid_pass(
    *,
    stores: Stores,
    verb: ScoringVerb,
    hop: CycleHop | None,
    declared: Budget,
    sends: tuple[Send, ...],
    acting: str | None = None,
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer. On a cycle it holds it, so the one pause stops
    it; *acting* is the command it acts for, whose `cancel` stops it too."""
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
    start = RunStart(stores, job_registry, job)
    holding: CycleHold | None = None
    try:
        meters: CeilingMeter = (
            "bill" if hop is None else stores.campaigns.ceiling_meter_of(hop.campaign_id)
        )
        if hop is not None:
            holding = CycleHold(
                stores, hop, scoring_pass=True, acting=() if acting is None else (acting,)
            )
            # What a producer gone since was asked stands no more, as a launch's RUNNING record voids it.
            stores.void_inbox(hop)
            # The cycle's own file, never a bound ledger: the bracket is scanned there alone.
            cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
            close_dead_bench(CycleEventLog.open(cycle_dir))
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
            hop=hop,
            meters=meters,
            opened=time.monotonic(),
            live=False,
        )
        job_registry.mark_started(job.job_id)
        with scoped(
            account=stores,
            book=book,
            # The rate-limit and slot waits poll the cycle's pause too, as a loop's do.
            abort_check=run_scope().abort_check
            if holding is None
            else holding.control.pause_requested,
        ):
            yield book
    except BaseException as exc:
        start.release(exc)
        if holding is None:
            raise
        try:
            # Taken only on the stop it made on it; the release voids any other.
            taken = holding.control.take_pause() if launch_interrupted(exc) else None
        finally:
            holding.release()
        if taken is not None:
            raise PassStoppedError(
                f"The {verb} pass stopped: {taken[1]}. Every cell it measured is banked, and a "
                "rerun replays them."
            ) from None
        raise
    start.release()
    if holding is not None:
        holding.release()


def _campaign_ceiling(stores: Stores, hop: CycleHop) -> Budget:
    config = cycle_config(stores, campaign_at(stores, hop), hop).config
    return next_launch_budget(config, stores=stores, hop=hop).only(MONEY_AXES)


def campaign_at(stores: Stores, hop: CycleHop) -> Campaign:
    if (campaign := stores.campaigns.load_campaign(hop.campaign_id)) is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    return campaign


def campaign_sends(stores: Stores, hop: CycleHop) -> tuple[Send, ...]:
    """What a run or pass on *hop* sends, under its campaign's resolved config and connector."""
    campaign = campaign_at(stores, hop)
    config = cycle_config(stores, campaign, hop).config
    return sends_on(stores, config, campaign.connector)
