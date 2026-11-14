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
from promptpotter.application.jobs.quota import (
    admit_launch,
    call_book,
    check_launch_quotas,
    declare_run_budget,
    hold_moved,
    hold_run,
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
from promptpotter.domain.bench import CampaignRefusal
from promptpotter.domain.campaign import Campaign, CampaignRefusedError
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.job import (
    UNRESOLVED_HOP,
    CallBucket,
    CampaignLaunch,
    EmbeddedRun,
    InnerRun,
    Job,
    LaunchMove,
    PaidCall,
    ScoringPass,
    SlotHolder,
)
from promptpotter.domain.phases import (
    PauseCause,
    RunAdmission,
    RunPhase,
    RunState,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import Hold, RunPhaseRecord
from promptpotter.domain.spend import (
    MONEY_AXES,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    HeldBudget,
)
from promptpotter.infrastructure.llm.spend_book import LiveCycle, SpendBook, draws_on
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.account_spend import unreported_under
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_READ_ERRORS,
    definition_version,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    ConflictError,
    CycleBusyError,
    MachineBusyError,
    NotFoundError,
)
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import LaunchRequest

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
        """A raise is the cycle's stop on *stamps* (a refused one ``NOT_ADMITTED``); a result is
        what the run returned; nothing, a pass that returned. Every hold the job holds closes with
        it: its hold on the account by a `Release`, filed first so a crash before the job's stamp
        leaves an unreleased job the reaper still ends; its claim of a cycle as it reads released."""
        held = self.job_registry.get(self.job_id)
        if held is not None and held.admitted and not held.released:
            release_run(self.stores, self.job_id)
        match ended:
            case BaseException():
                job = self.job_registry.release(self.job_id, ended)
            case None:
                job = self.job_registry.release(self.job_id)
            case _:
                job = self.job_registry.release(
                    self.job_id, ended.stop_reason, stepped=ended.stepped
                )
        if isinstance(ended, BaseException):
            self._fail(job, ended, stamps)

    def land(self, hop: CycleHop, held: HeldBudget) -> None:
        """The job onto the cycle its hold just minted, its share with it; a slotted job claims the
        cycle."""
        land_job(self.stores, self.job_registry, self.job_id, hop, held.share)

    def _fail(self, job: Job | None, exc: BaseException, stamps: CycleHop | None) -> None:
        """Off a cycle the job's release is its claim's: nothing more to stamp."""
        if stamps is None:
            return
        stop_reason = StopReason.NOT_ADMITTED if job is None or job.ending is None else job.ending
        stores = self.stores
        # Best-effort — it must never mask *exc*.
        try:
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


async def probe_backend(
    name: str, endpoint: str | None
) -> BackendUnreachableError | MissingExtraError | None:
    connector = connectors.CONNECTORS.get(name)
    if connector.extra is not None and (absent := missing(connector.extra)):
        return MissingExtraError(connector.extra, absent)
    if (down := await connector.unreachable(endpoint)) is not None:
        return BackendUnreachableError(connector.name, connector.base_url(endpoint), down)
    return None


async def refuse_unreachable(name: str, endpoint: str | None) -> None:
    if (down := await probe_backend(name, endpoint)) is not None:
        raise down


class _DefinitionRead(NamedTuple):
    """The connector a dataset's definition names, read as the mint read it, or why it reads as
    none (``connector`` empty then)."""

    connector: str
    unread: str | None


def _definition_read(stores: Stores, name: str) -> _DefinitionRead:
    """Derived on the definition's stats, so a served read parses it once per edit."""

    def read() -> _DefinitionRead:
        try:
            named = runnable_definition(resolve_dataset(stores, name)).connector
        except DEFINITION_READ_ERRORS as unread:
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


def admitted(stores: Stores, campaign: Campaign | None, run: RunState) -> RunAdmission:
    """*run*'s admission with its campaign's refusal joined: the acts every served read offers."""
    refused = None if campaign is None else campaign_refusal(stores, campaign)
    return RunAdmission.of(run, campaign_refusal="" if refused is None else str(refused))


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
            refuse_taken_cycle(stores, hop)
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
        if hop != UNRESOLVED_HOP:
            claim_cycle(stores, hop, jobs[0].job_id)
        return jobs


def claim_cycle(stores: Stores, hop: CycleHop, job_id: str) -> None:
    """The job's hold on *hop*, on the cycle's ledger, filed where the job first names the cycle:
    it lives and dies with the job (`holders.py::holder_of`)."""
    stores.campaigns.append(hop, Hold.of_job(job_id, Budget(), parent=None))


def land_job(
    stores: Stores, job_registry: JobRegistry, job_id: str, hop: CycleHop, allotment: Budget
) -> None:
    """THE LAND of a launch: onto the cycle its mint named, or a running one onto its fork; its
    share, its job's hop and its claim of the cycle together under the gate."""
    with job_registry.admission_gate():
        hold_moved(stores, hop, allotment)
        job_registry.move(job_id, LaunchMove.LAND, hop=hop)
        claim_cycle(stores, hop, job_id)


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
            refuse_taken_cycle(stores, CycleHop(campaign_id=campaign_id, cycle_id=one))
        yield


def deletable_stubs(stores: Stores, hop: CycleHop) -> list[CycleHop]:
    """What `cleanup-empty-cycles` at *hop* deletes now, the count `/hierarchy` serves and the list
    the command walks alike: its root's stub forks (`CampaignStore.stub_forks`) that no run or pass
    holds (`refuse_taken_cycle`). The act asks again, under the gate (`destroying`)."""
    free: list[CycleHop] = []
    for stub in stores.campaigns.stub_forks(hop):
        try:
            refuse_taken_cycle(stores, stub)
        except CycleBusyError:
            continue
        free.append(stub)
    return free


def refuse_taken_cycle(stores: Stores, hop: CycleHop) -> None:
    """ONE check for launches, passes and destroyers alike, asked under the admission gate: the
    cycle's holder (`holders.py::holder_of`)."""
    if (held := stores.campaigns.holder(hop)) is None:
        return
    holder = held.job
    status = (
        RunPhase.SCORING.value
        if holder is not None and isinstance(holder.holder, ScoringPass)
        else stores.run_state(hop).run_phase.value
    )
    raise CycleBusyError(
        job_id="unregistered" if holder is None else holder.job_id,
        status=status,
        holder_user="" if holder is None else holder.user_id,
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        started_at=None if holder is None else holder.started_at,
    )


def refuse_as_busy(job_registry: JobRegistry, job: Job) -> NoReturn:
    # A REFUSAL takes its queue entry with it: one left behind starts the run later.
    job_registry.cancel_queued(job.job_id)
    raise _machine_busy(job_registry)


def _machine_busy(job_registry: JobRegistry) -> MachineBusyError:
    oldest = next(iter(job_registry.list_running()), None)
    return MachineBusyError(
        holding="" if oldest is None else oldest.what,
        holder_user="" if oldest is None else oldest.user_id,
        started_at=None if oldest is None else oldest.started_at,
    )


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

    await refuse_unreachable(request.connector(stores), request.endpoint(stores))
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
            room = enclosing.money_left().bound_over(spent)
            admitted, reserve = declared.over(_within(declared, room)), Budget()
            ceiling = admitted
        allotment = share_campaign_ceiling(
            ceiling, stores=stores, job_registry=job_registry, job=job, hop=hop, meter=meter
        )
        hold_run(
            stores, job.job_id, reserve, parent=None if enclosing is None else draws_on(enclosing)
        )
        job_registry.move(job.job_id, LaunchMove.ADMIT)
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
                refuse_as_busy(job_registry, jobs[0])
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
    """On a cycle its share is read live off the cycle's record, which a reshare moves, and meters
    the cycle's ledger; off one, as admitted, metering its job's hold. Its reserve is its hold's on
    the account, which a set-limits restates. A *live* book reads the cycle's standing axes too."""
    ledger = None if hop is None else stores.campaigns.ledger(hop)
    book = SpendBook(
        declared=held.admitted,
        share=held.share if hop is None else LiveCycle(stores.campaigns, hop),
        charges=stores.charges,
        hold_id=held.job_id,
        headroom=Budget(),
        metered_on=ledger,
        meters=meters,
        opened=opened,
        cycle=LiveCycle(stores.campaigns, hop) if live and hop is not None else None,
    )
    if ledger is None:
        return book
    left = unreported_under(stores.charges, ledger).total
    if left.unreported_usd or left.unreported_tokens:
        logger.warning(
            "paid sends on %s reported no bill; the ceiling holds up to $%.4f and %d tokens for "
            "them as unreported.",
            hop,
            left.unreported_usd,
            left.unreported_tokens,
        )
    return book


def admit_slotless(
    config: CampaignConfig,
    *,
    stores: Stores,
    holder: EmbeddedRun | InnerRun,
    hop: CycleHop | None,
    requested: Budget,
    step_rounds: int | None,
    sends: tuple[Send, ...],
) -> tuple[RunStart, HeldBudget]:
    """THE admission with no slot, taken BEFORE anything paid: the job ASKED, then ADMITTED under
    its hold (HELD on *hop*); a refusal releases it. An inner run's share is of what the
    enclosing run's book has left at its spawn. It waits on the gate: an async door offloads it."""
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
    if hop is not None:
        claim_cycle(stores, hop, start.job_id)
    try:
        declared, asked = declare_run_budget(
            config.optimization.budget, stores=stores, hop=hop, requested=requested
        )
        held = admit_and_share(
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
    except BaseException as exc:
        start.release(exc)
        raise
    return start, held


async def slotless_share[S](
    config: CampaignConfig,
    *,
    stores: Stores,
    holder: InnerRun,
    hop: CycleHop,
    sends: tuple[Send, ...],
    run: SharedRun[S],
) -> tuple[S, CycleResult]:
    """An inner run on the cycle its spawn minted: admitted (`admit_slotless`), then *run* under
    its hold, whose result the job's release records."""
    start, held = await asyncio.to_thread(
        admit_slotless,
        config,
        stores=stores,
        holder=holder,
        hop=hop,
        requested=Budget(),
        step_rounds=None,
        sends=sends,
    )
    try:
        await asyncio.to_thread(start.job_registry.move, start.job_id, LaunchMove.START)
        ran = await run(held)
    except BaseException as exc:
        await asyncio.to_thread(start.release, exc)
        raise
    await asyncio.to_thread(start.release, ran[1])
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
            job_registry.move(start.job_id, LaunchMove.ADMIT)
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
        await asyncio.to_thread(start.release, exc)
        raise
    await asyncio.to_thread(start.release)


class PassStoppedError(ConflictError):
    """A pause reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


@asynccontextmanager
async def paid_pass(
    *,
    stores: Stores,
    pass_kind: MeasurementRole,
    hop: CycleHop | None,
    declared: Budget,
    sends: tuple[Send, ...],
    acting: str | None = None,
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer. On a cycle it holds it, so the one pause stops
    it; *acting* is the command it acts for, whose `cancel` stops it too. Opened and closed off
    the loop: both wait on the gate."""
    start, holding, book = await asyncio.to_thread(
        _open_pass,
        stores=stores,
        pass_kind=pass_kind,
        hop=hop,
        declared=declared,
        sends=sends,
        acting=acting,
    )
    try:
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
        taken = await asyncio.to_thread(_close_pass, start, holding, exc)
        if taken is not None:
            raise PassStoppedError(
                f"The {pass_kind} pass stopped: {taken}. Every cell it measured is banked, and a "
                "rerun replays them."
            ) from None
        raise
    await asyncio.to_thread(_close_pass, start, holding, None)


def _open_pass(
    *,
    stores: Stores,
    pass_kind: MeasurementRole,
    hop: CycleHop | None,
    declared: Budget,
    sends: tuple[Send, ...],
    acting: str | None,
) -> tuple[RunStart, CycleHold | None, SpendBook]:
    job_registry = JobRegistry.attach(stores.jobs_dir)
    (job,) = request_jobs(
        stores=stores,
        job_registry=job_registry,
        holder=ScoringPass(pass_kind),
        hop=UNRESOLVED_HOP if hop is None else hop,
    )
    if job.queued:
        refuse_as_busy(job_registry, job)
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
            close_dead_bench(stores, hop)
            declared = _campaign_ceiling(stores, hop).over(declared)
        held = admit_and_share(
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
        job_registry.move(job.job_id, LaunchMove.START)
    except BaseException as exc:
        _close_pass(start, holding, exc)
        raise
    return start, holding, book


def _close_pass(
    start: RunStart, holding: CycleHold | None, exc: BaseException | None
) -> str | None:
    """The pass's job released and its cycle let go; the pause it stopped on, if it was one."""
    if holding is None:
        start.release(exc)
        return None
    try:
        # Taken only on the stop it made on it, while its job still holds; the release voids any other.
        taken = (
            holding.control.take_pause() if exc is not None and launch_interrupted(exc) else None
        )
    finally:
        start.release(exc)
        holding.release()
    return None if taken is None else taken[1]


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
