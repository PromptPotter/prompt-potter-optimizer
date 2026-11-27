from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, NamedTuple, NoReturn, assert_never

from promptpotter import connectors
from promptpotter.application.account import account_of
from promptpotter.application.jobs.launcher.opening import (
    Opening,
    claim_cycle,
    open_spend_book,
)
from promptpotter.application.jobs.quota import (
    admit_launch,
    call_book,
    check_launch_quotas,
    declare_run_budget,
    next_launch_budget,
    sends_on,
    slot_rate,
)
from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.jobs.shares import (
    hold_moved,
    hold_run,
    move_campaign_money,
    share_campaign_ceiling,
)
from promptpotter.application.pipeline_resolve import cycle_config, experiment_outside_run
from promptpotter.application.run_phase_control import ProducerHold, close_dead_bench
from promptpotter.domain.bench import CampaignRefusal
from promptpotter.domain.campaign import (
    Campaign,
    CampaignCycle,
    CampaignRefusedError,
    ceiling_meter,
)
from promptpotter.domain.command_kinds import filable
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.hierarchy import RunAdmission
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
    RunPhase,
    RunState,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.spend import (
    MONEY_AXES,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    HeldBudget,
)
from promptpotter.infrastructure.llm.spend_book import SpendBook, draws_on
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_READ_ERRORS,
    DatasetDefinition,
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
)
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.mint_and_start import LaunchRequest


async def probe_backend(
    name: str, endpoint: str | None
) -> BackendUnreachableError | MissingExtraError | None:
    connector = connectors.CONNECTORS.get(name)
    if connector.extra is not None and (absent := missing(connector.extra)):
        return MissingExtraError(connector.extra, absent)
    if (down := await connector.unreachable(endpoint)) is not None:
        return BackendUnreachableError(connector.name, connector.base_url(endpoint), down)
    return None


async def refuse_unreachable(
    stores: Stores, dataset_name: str | None, name: str, endpoint: str | None
) -> None:
    """THE launch probe, asked at every door before anything mints: the backend *dataset_name*
    runs on, then each backend a dataset its connector's own declaration nests runs on (an L4
    panel's seats), each once. A check-in's draft names no dataset yet: its connector serves
    uploads and owns no panel."""
    probed = dict.fromkeys(
        [(name, endpoint)]
        + [(d.connector, d.endpoint) for d in _nested_definitions(stores, dataset_name, name)]
    )
    for backend, at in probed:
        if (down := await probe_backend(backend, at)) is not None:
            raise down


def _nested_definitions(
    stores: Stores, dataset_name: str | None, name: str
) -> list[DatasetDefinition]:
    """Only a panel the connector declares in-process nests a dataset (`nested_datasets`); a
    served backend's declaration is read past its probe."""
    connector = connectors.CONNECTORS.get(name)
    if dataset_name is None or not connector.owns_panel or connector.pipeline_declaration is None:
        return []
    dataset = resolve_dataset(stores, dataset_name)
    declared = parse_pipeline_response(
        connector.pipeline_declaration(stores, experiment_outside_run(stores, dataset))
    )
    return [
        runnable_definition(resolve_dataset(stores, nested))
        for node in declared.declared_nodes
        for nested in node.nested_datasets
    ]


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


def admitted(stores: Stores, campaign: Campaign, run: RunState) -> RunAdmission:
    """*run*'s launch half with its campaign's refusal and its reader's kinds joined (the read's own
    `stores.identity`, capability-less on the exhibit): the acts every served read offers."""
    joined = (
        None if (refused := campaign_refusal(stores, campaign)) is None else Refusal.of(refused)
    )
    return RunAdmission.of(run, campaign_refusal=joined, filable=filable(stores.identity))


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
    with job_registry.admission_gate(launching=True), slot_rate(stores, holder, hop, asked=count):
        # First, under the gate an archive or a delete takes too: a refused campaign's act is
        # queued, claimed and debited nothing.
        if hop != UNRESOLVED_HOP:
            refuse_campaign(stores, stores.campaigns.campaign(hop.campaign_id))
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


async def admit_and_hold(request: LaunchRequest, opening: Opening) -> HeldBudget:
    """The request's config is resolved IN here, after the queue wait, so the budget admitted is the one the run starts under."""
    stores, job_registry, job = opening.stores, opening.job_registry, opening.job
    # A fresh mint has no cycle yet: nothing to claim, and no seed or standing ceiling to read.
    at = request.at
    if job.queued:
        await job_registry.await_claim(job.job_id)
        if at is not None:
            # Asked again past the wait: the slot door's claim held off an archive or a delete,
            # never a definition moved while the job waited.
            refuse_campaign(stores, at.campaign)

    await refuse_unreachable(
        stores, request.dataset_name, request.connector(stores), request.endpoint(stores)
    )
    # An inner run's share is of what the enclosing run's book has left at its spawn.
    enclosing = run_scope().book if isinstance(request.holder, InnerRun) else None
    if isinstance(request.holder, InnerRun) and enclosing is None:
        raise RuntimeError("an inner run spawns only under its enclosing run's book")
    declared, asked = declare_run_budget(
        request.declared_budget(stores), stores=stores, at=at, requested=request.budget
    )
    # Offloaded: the wallet read scans every cycle ledger and must not block the event loop.
    held = await asyncio.to_thread(
        admit_and_share,
        declared,
        asked,
        stores=stores,
        job_registry=job_registry,
        job=job,
        at=at,
        step_rounds=request.step_rounds,
        sends=request.sends(stores),
        enclosing=enclosing,
    )
    return held


def admit_and_share(
    declared: Budget,
    asked: Budget,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job,
    at: CampaignCycle | None,
    step_rounds: int | None,
    sends: tuple[Send, ...],
    enclosing: SpendBook | None = None,
) -> HeldBudget:
    """THE admission of every run and pass, sending *sends*, and its hold on the account; an inner
    run is admitted against what its *enclosing* book has left, whose hold binds what it sends."""
    hop = None if at is None else at.hop
    meter: CeilingMeter = "bill" if at is None else ceiling_meter(at.campaign.declaration.purpose)
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
                if at is None
                else stores.campaigns.metered_spend(at.campaign.campaign_id, meter)
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
        if at is not None:
            hold_moved(stores, at.hop, allotment)
            move_campaign_money(
                asked, asked=asked, stores=stores, job_registry=job_registry, campaign=at.campaign
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
) -> list[tuple[Opening, HeldBudget]]:
    """Every arm's opening and share, each at its budget and sends, or none, under one gate. An
    embedded host holds no slot (`slotless_opening`)."""
    # Inside the gate, so no admission between reads the arms a refusal takes back.
    with job_registry.admission_gate(launching=True), ExitStack() as refused:
        openings = [
            Opening.of(stores, job_registry, job)
            for job in _arm_jobs(stores, job_registry, holder, count=len(declared))
        ]
        for opening in openings:
            refused.push(opening)
        admitted = [
            (
                opening,
                admit_and_share(
                    budget,
                    Budget(),
                    stores=stores,
                    job_registry=job_registry,
                    job=opening.job,
                    at=None,
                    step_rounds=None,
                    sends=sends,
                ),
            )
            for opening, (budget, sends) in zip(openings, declared, strict=True)
        ]
        refused.pop_all()
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
                _slotless_job(stores, job_registry, holder, UNRESOLVED_HOP) for _ in range(count)
            ]
        case _:
            assert_never(holder)


def _slotless_job(
    stores: Stores, job_registry: JobRegistry, holder: EmbeddedRun | InnerRun, hop: CycleHop
) -> Job:
    return job_registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=hop,
        holder=holder,
    )


def slotless_opening(
    stores: Stores, job_registry: JobRegistry, holder: EmbeddedRun | InnerRun, hop: CycleHop
) -> Opening:
    """THE door with no slot, the declared exemption (an embedded host's run, an inner seat): its
    job ASKED and entered, claiming *hop*'s cycle where it names one. Its admission is `hold`'s."""
    opening = Opening.of(stores, job_registry, _slotless_job(stores, job_registry, holder, hop))
    if hop != UNRESOLVED_HOP:
        with ExitStack() as refused:
            refused.push(opening)
            claim_cycle(stores, hop, opening.job_id)
            refused.pop_all()
    return opening


def _within(declared: Budget, enclosing: Budget) -> Budget:
    usd = [v for v in (declared.usd, enclosing.usd) if v is not None]
    tokens = [v for v in (declared.tokens, enclosing.tokens) if v is not None]
    return Budget(usd=min(usd, default=None), tokens=min(tokens, default=None))


def _admit_call(stores: Stores, holder: PaidCall, provider: str) -> tuple[Opening, SpendBook]:
    job_registry = JobRegistry.attach(stores.jobs_dir)
    with (
        job_registry.admission_gate(launching=True),
        slot_rate(stores, holder, UNRESOLVED_HOP, asked=1),
        ExitStack() as refused,
    ):
        opening = Opening.of(
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
        refused.push(opening)
        book = call_book(stores, provider, job_registry=job_registry, hold_id=opening.job_id)
        # Unbounded: it holds what its calls out hold (`Money.held_back`), and closes with its job.
        hold_run(stores, opening.job_id, Budget(), parent=None)
        job_registry.move(opening.job_id, LaunchMove.ADMIT)
        refused.pop_all()
    return opening, book


@asynccontextmanager
async def paid_call(
    *, stores: Stores, bucket: CallBucket, provider: str
) -> AsyncIterator[SpendBook]:
    """ONE LLM call on *provider*, on no cycle, slotless so a chat turn never queues behind
    campaigns: a job whose hold its sends draw on, closed with it or by its dead producer's reap."""
    # Offloaded: admission globs every cycle ledger.
    opening, book = await asyncio.to_thread(_admit_call, stores, PaidCall(bucket), provider)
    async with AsyncExitStack() as closing:
        closing.push_async_exit(opening)
        with scoped(account=stores, book=book):
            yield book


class PassStoppedError(ConflictError):
    """A pause reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


@asynccontextmanager
async def paid_pass(
    *,
    stores: Stores,
    pass_kind: MeasurementRole,
    at: CampaignCycle | None,
    declared: Budget,
    sends: tuple[Send, ...],
    acting: str | None = None,
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer. On a cycle it holds it, so the one pause stops
    it; *acting* is the command it acts for, whose `cancel` stops it too. Opened and closed off
    the loop: both wait on the gate."""
    opening, holding, book = await asyncio.to_thread(
        _open_pass,
        stores=stores,
        pass_kind=pass_kind,
        at=at,
        declared=declared,
        sends=sends,
        acting=acting,
    )
    taken: tuple[PauseCause, str] | None = None
    try:
        async with AsyncExitStack() as closing:
            # The cycle let go last, past its job's release.
            if holding is not None:
                closing.push_async_callback(asyncio.to_thread, holding.release)
            closing.push_async_exit(opening)
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
                # Taken only on the stop it made on it, while its job still holds.
                if holding is not None and launch_interrupted(exc):
                    taken = await asyncio.to_thread(holding.control.take_pause)
                raise
    except BaseException:
        if taken is not None:
            raise PassStoppedError(
                f"The {pass_kind} pass stopped: {taken[1]}. Every cell it measured is banked, and "
                "a rerun replays them."
            ) from None
        raise


def _open_pass(
    *,
    stores: Stores,
    pass_kind: MeasurementRole,
    at: CampaignCycle | None,
    declared: Budget,
    sends: tuple[Send, ...],
    acting: str | None,
) -> tuple[Opening, ProducerHold | None, SpendBook]:
    job_registry = JobRegistry.attach(stores.jobs_dir)
    (job,) = request_jobs(
        stores=stores,
        job_registry=job_registry,
        holder=ScoringPass(pass_kind),
        hop=UNRESOLVED_HOP if at is None else at.hop,
    )
    if job.queued:
        refuse_as_busy(job_registry, job)
    opening = Opening.of(stores, job_registry, job)
    holding: ProducerHold | None = None
    with ExitStack() as refused:
        refused.push(opening)
        meters: CeilingMeter = (
            "bill" if at is None else ceiling_meter(at.campaign.declaration.purpose)
        )
        if at is not None:
            holding = ProducerHold(
                stores, at.hop, scoring_pass=True, acting=() if acting is None else (acting,)
            )
            refused.callback(holding.release)
            # What a producer gone since was asked stands no more, as a launch's RUNNING record voids it.
            stores.void_inbox(at.hop)
            # The cycle's own file, never a bound ledger: the bracket is scanned there alone.
            close_dead_bench(stores, at.hop)
            config = cycle_config(stores, at).config
            ceiling = next_launch_budget(config, stores=stores, at=at).only(MONEY_AXES)
            declared = ceiling.over(declared)
        held = admit_and_share(
            declared,
            Budget(),
            stores=stores,
            job_registry=job_registry,
            job=job,
            at=at,
            step_rounds=None,
            sends=sends,
        )
        book = open_spend_book(
            held,
            stores=stores,
            hop=None if at is None else at.hop,
            meters=meters,
            opened=time.monotonic(),
            live=False,
        )
        job_registry.move(job.job_id, LaunchMove.START)
        refused.pop_all()
    return opening, holding, book


def campaign_sends(stores: Stores, at: CampaignCycle) -> tuple[Send, ...]:
    """What a run or pass on *at* sends, under its resolved config and connector."""
    return sends_on(stores, cycle_config(stores, at).config, at.campaign.connector)
