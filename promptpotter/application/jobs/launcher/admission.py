from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Protocol, assert_never

from promptpotter import connectors
from promptpotter.application.account import account_of
from promptpotter.application.initialization.loop_start import verb_ledger
from promptpotter.application.jobs.quota import (
    RateAsk,
    admit_launch,
    check_launch_quotas,
    declare_run_budget,
    hold_moved,
    move_campaign_money,
    next_launch_budget,
    share_campaign_ceiling,
    verb_rate,
)
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    InnerRun,
    Job,
    JobRegistry,
    ScoringPass,
    ScoringVerb,
    SlotHolder,
)
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.config.settings import settings
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
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
from promptpotter.infrastructure.llm.send_pacing import set_abort_check, set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    Hold,
    LiveHold,
    SpendBook,
    bound_spend_book,
    spending_under,
)
from promptpotter.infrastructure.producer_lock import hold_pass, release_pass
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    ConflictError,
    CycleBusyError,
    MachineBusyError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.domain.campaign_config import CampaignConfig

logger = logging.getLogger(__name__)


def launch_interrupted(exc: BaseException) -> bool:
    """Someone ASKED the launch to stop; neither type is visible to ``except Exception``."""
    return isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)


def release_slot(
    job_registry: JobRegistry,
    job_id: str,
    exc: BaseException,
    *,
    stores: Stores,
    hop: CycleHop | None,
) -> None:
    """A launch that never reached its admission (``Job.admitted``) was REFUSED, whatever raised."""
    job = job_registry.get(job_id)
    if (job is not None and job.admitted) or launch_interrupted(exc):
        stop_reason = run_stop_reason(exc)
    else:
        stop_reason = StopReason.NOT_ADMITTED
    job_registry.release(job_id, refusal=str(exc) if stop_reason is StopReason.NOT_ADMITTED else "")
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
                else ErrorRecord(
                    kind="launch_failed",
                    message=str(exc) or type(exc).__name__,
                    stop_reason=stop_reason,
                )
            ),
        )
    except Exception:
        logger.exception("failed to record launch stop for job %s", job_id)


async def probe_backend(backend_type: str) -> BackendUnreachableError | MissingExtraError | None:
    # A campaign that outlived its dataset dir: ``connectors.get`` is strict and would raise.
    if not backend_type:
        return None
    connector = connectors.CONNECTORS.get(backend_type)
    if connector.extra is not None and (absent := missing(connector.extra)):
        return MissingExtraError(connector.extra, absent)
    if connector.preflight is None:
        return None
    base_url = connector.base_url()
    if (down := await connector.preflight(base_url)) is not None:
        return BackendUnreachableError(connector.name, base_url, down)
    return None


def request_job(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    holder: SlotHolder | EmbeddedRun,
    hop: CycleHop = UNRESOLVED_HOP,
    launches: int = 1,
    may_queue: bool = False,
) -> list[Job]:
    """THE door of every launch's and pass's jobs: *launches* of them asked together (a
    head-to-head's arms) are all written or none. Only one launch whose caller can wait
    (*may_queue*) queues, so no arm searches while a sibling waits. An embedded host holds no slot
    and answers to no launch quota."""
    assert launches == 1 or not may_queue, "arms asked together never queue"
    if launches > settings.MACHINE_RUN_CAPACITY and not isinstance(holder, EmbeddedRun):
        raise PayloadInvalidError(
            f"This act starts {launches} runs at once, and this machine runs at most "
            f"{settings.MACHINE_RUN_CAPACITY} (MACHINE_RUN_CAPACITY in .env): start at most that "
            "many arms in one act, and the rest in a later act on the same head-to-head id.",
            code="arms_above_machine",
            details={"requested": launches, "machine_ceiling": settings.MACHINE_RUN_CAPACITY},
        )
    user = account_of(stores)
    # ONE gate: the quota count and the slot count come off the same on-disk jobs.
    with job_registry.admission_gate():
        rate: RateAsk | None
        match holder:
            case CampaignLaunch():
                rate = check_launch_quotas(
                    user=user, stores=stores, job_registry=job_registry, hop=hop, launches=launches
                )
            case ScoringPass(verb=verb):
                rate = verb_rate(stores, user, verb)
            case EmbeddedRun():
                rate = None
            case _:
                assert_never(holder)
        if hop != UNRESOLVED_HOP:
            _refuse_taken_cycle(stores, job_registry, hop)
        jobs: list[Job] = []
        try:
            for _ in range(launches):
                jobs.append(_new_job(stores, job_registry, holder, hop))
            if not may_queue and any(job.queued for job in jobs):
                raise _machine_busy(job_registry)
            # A pass takes no claim: it never queues, and holds the cycle itself once admitted (`paid_pass`).
            if hop != UNRESOLVED_HOP and isinstance(holder, CampaignLaunch):
                claim_cycle(stores, job_registry, jobs[0], hop)
            if rate is not None:
                rate.debit()
        except BaseException as exc:
            # Inside the gate, so no admission between reads the jobs a refusal takes back.
            for job in jobs:
                release_slot(job_registry, job.job_id, exc, stores=stores, hop=None)
            raise
        return jobs


def _new_job(
    stores: Stores, job_registry: JobRegistry, holder: SlotHolder | EmbeddedRun, hop: CycleHop
) -> Job:
    user_id, workspace = str(stores.identity.user_id), stores.campaigns.workspace
    principal_id = acting_principal(stores.identity).id
    if isinstance(holder, EmbeddedRun):
        return job_registry.create(
            user_id=user_id, principal_id=principal_id, workspace=workspace, hop=hop, holder=holder
        )
    return job_registry.request_slot(
        user_id=user_id, principal_id=principal_id, workspace=workspace, holder=holder, hop=hop
    )


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


class AdmittedRequest(Protocol):
    """What ``admit_and_hold`` reads of a launch: a ``LaunchRequest``, or one head-to-head arm."""

    @property
    def verb(self) -> str: ...
    @property
    def dataset_name(self) -> str: ...
    @property
    def hop(self) -> CycleHop: ...
    @property
    def budget(self) -> Budget: ...
    @property
    def step_rounds(self) -> int | None: ...
    def backend_type(self, stores: Stores, /) -> str: ...
    def campaign_config(self, stores: Stores, /) -> CampaignConfig: ...


async def admit_and_hold(
    request: AdmittedRequest, *, stores: Stores, job_registry: JobRegistry, job: Job
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
        request.campaign_config(stores), stores=stores, hop=hop, requested=request.budget
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
    enclosing: Budget | None = None,
) -> HeldBudget:
    """THE admission of every run and pass; an inner run is admitted against its *enclosing* book."""
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
            )
            # The campaign's ceiling as composed, never as the account clamped it: the account
            # binds through the reservation. An axis it leaves open is the headroom admitted on it.
            ceiling = Budget(
                usd=admitted.usd if declared.usd is None else declared.usd,
                tokens=admitted.tokens if declared.tokens is None else declared.tokens,
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


@asynccontextmanager
async def embedded_share(
    config: CampaignConfig,
    *,
    stores: Stores,
    dataset_name: str,
    hop: CycleHop | None,
    requested: Budget,
    step_rounds: int | None,
) -> AsyncIterator[tuple[str, HeldBudget]]:
    """A launch's admission with no slot, entered BEFORE anything paid: the job, then its hold."""
    job_registry = JobRegistry.attach(stores.jobs_dir)
    (job,) = request_job(
        stores=stores,
        job_registry=job_registry,
        holder=EmbeddedRun(dataset_name),
        hop=UNRESOLVED_HOP if hop is None else hop,
    )
    admitted = False
    try:
        declared, asked = declare_run_budget(config, stores=stores, hop=hop, requested=requested)
        held = await asyncio.to_thread(
            admit_and_share,
            declared,
            asked,
            stores=stores,
            job_registry=job_registry,
            job=job,
            hop=hop,
            step_rounds=step_rounds,
        )
        admitted = True
        job_registry.mark_started(job.job_id)
        yield job.job_id, held
    except BaseException as exc:
        job_registry.release(job.job_id, refusal="" if admitted else str(exc))
        raise
    job_registry.release(job.job_id)


@contextmanager
def inner_share(
    config: CampaignConfig, *, stores: Stores, dataset_name: str, hop: CycleHop
) -> Iterator[HeldBudget]:
    """An L4 inner campaign's share, of what the enclosing run's book has left at its spawn."""
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
    admitted = False
    try:
        declared, asked = declare_run_budget(config, stores=stores, hop=hop, requested=Budget())
        held = admit_and_share(
            declared,
            asked,
            stores=stores,
            job_registry=job_registry,
            job=job,
            hop=hop,
            step_rounds=None,
            enclosing=enclosing.money_left(),
        )
        admitted = True
        job_registry.mark_started(job.job_id)
        yield held
    except BaseException as exc:
        job_registry.release(job.job_id, refusal="" if admitted else str(exc))
        raise
    job_registry.release(job.job_id)


class PassStoppedError(ConflictError):
    """A pause reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


@asynccontextmanager
async def paid_pass(
    *, stores: Stores, verb: ScoringVerb, hop: CycleHop | None, declared: Budget
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer. On a cycle it holds it, so the one pause stops it."""
    job_registry = JobRegistry.attach(stores.jobs_dir)
    (job,) = await asyncio.to_thread(
        request_job,
        stores=stores,
        job_registry=job_registry,
        holder=ScoringPass(verb),
        hop=UNRESOLVED_HOP if hop is None else hop,
    )
    admitted = False
    holding: CycleHop | None = None
    try:
        meters: CeilingMeter = (
            "bill" if hop is None else stores.campaigns.ceiling_meter_of(hop.campaign_id)
        )
        if hop is not None:
            cycle_dir = stores.campaigns.cycle_dir(hop)
            hold_pass(cycle_dir)
            holding = hop
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
        admitted = True
        job_registry.mark_started(job.job_id)
        set_rate_tenant(str(stores.identity.tenant_id))
        with spending_under(book):
            yield book
    except BaseException as exc:
        job_registry.release(job.job_id, refusal="" if admitted else str(exc))
        stopped_by = _let_go(stores, holding)
        if stopped_by is not None and launch_interrupted(exc):
            raise PassStoppedError(
                f"The {verb} pass stopped: {stopped_by}. Every cell it measured is banked, and a "
                "rerun replays them."
            ) from None
        raise
    job_registry.release(job.job_id)
    _let_go(stores, holding)


def _let_go(stores: Stores, holding: CycleHop | None) -> str | None:
    """Applies a pause asked of the pass, however it ended, before the cycle is let go; who asked."""
    if holding is None:
        return None
    cycle_dir = stores.campaigns.cycle_dir(holding)
    control = RunControl(cycle_dir=cycle_dir)
    try:
        if not control.pause_requested():
            return None
        with verb_ledger(stores, holding):
            taken = control.take_pause()
        return None if taken is None else taken[1]
    finally:
        release_pass(cycle_dir)


def _campaign_ceiling(stores: Stores, hop: CycleHop) -> Budget:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    config = resolve_campaign_config(stores, campaign, hop)
    return next_launch_budget(config, stores=stores, hop=hop).only(MONEY_AXES)
