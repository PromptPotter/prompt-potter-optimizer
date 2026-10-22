from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, NoReturn, assert_never

from promptpotter import connectors
from promptpotter.application.account import account_of
from promptpotter.application.jobs.quota import (
    admit_launch,
    check_launch_quotas,
    declare_run_budget,
    move_campaign_money,
    next_launch_budget,
    refuse_verb_rate,
    share_campaign_ceiling,
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
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import (
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import ErrorRecord, RunPhaseRecord
from promptpotter.domain.spend import (
    MONEY_AXES,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    HeldBudget,
)
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    JobHold,
    SpendBook,
    bound_spend_book,
    spending_under,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import CycleBusyError, MachineBusyError, NotFoundError
from promptpotter.shared.extras import MissingExtraError, missing
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.jobs.launcher.launch import LaunchRequest

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
    connector = connectors.get(backend_type)
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
    holder: SlotHolder,
    hop: CycleHop = UNRESOLVED_HOP,
) -> Job:
    user = account_of(stores)
    # ONE gate: the quota count and the slot count come off the same on-disk jobs.
    with job_registry.admission_gate():
        match holder:
            case CampaignLaunch():
                check_launch_quotas(user=user, stores=stores, job_registry=job_registry, hop=hop)
            case ScoringPass(verb=verb):
                refuse_verb_rate(stores, user, verb)
            case _:
                assert_never(holder)
        if hop != UNRESOLVED_HOP:
            _refuse_taken_cycle(stores, job_registry, hop)
        job = job_registry.request_slot(
            user_id=str(stores.identity.user_id),
            principal_id=acting_principal(stores.identity).id,
            workspace=stores.campaigns.workspace,
            holder=holder,
            hop=hop,
        )
        # A pass takes no claim: a claim reads as an attached producer, and scoring needs a still cycle.
        if hop != UNRESOLVED_HOP and isinstance(holder, CampaignLaunch):
            claim_cycle(stores, job_registry, job, hop)
        return job


def _refuse_taken_cycle(stores: Stores, job_registry: JobRegistry, hop: CycleHop) -> None:
    """ONE check for launches and passes alike: a run attached, or a pass scoring it."""
    run = derive_run_state(stores.campaigns.cycle_dir(hop))
    if run.producer.attached:
        claim = stores.campaigns.launch_claim(hop)
        holder = (
            job_registry.running_job_for(stores.campaigns.workspace, hop)
            if claim is None
            else job_registry.get(claim.job_id)
        )
        status = run.run_phase.value
    elif (holder := job_registry.pass_on(stores.campaigns.workspace, hop)) is not None:
        status = "scoring"
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
    oldest = next(iter(job_registry.list_running()), None)
    raise MachineBusyError(
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
        job_registry.mark_admitted(job.job_id, reserve, allotment)
        if hop is not None:
            move_campaign_money(
                asked, stores=stores, job_registry=job_registry, campaign_id=hop.campaign_id
            )
    return HeldBudget(admitted=admitted, standing=asked, job_id=job.job_id, step_rounds=step_rounds)


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
    """Its money is read off the job, which a reshare moves; a *live* book reads the cycle's standing axes too."""
    return SpendBook(
        declared=held.admitted,
        hold=JobHold(job_registry.job_file(held.job_id)),
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
    job_registry = JobRegistry.attach()
    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=UNRESOLVED_HOP if hop is None else hop,
        holder=EmbeddedRun(dataset_name),
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
    config: CampaignConfig, *, stores: Stores, dataset_name: str, hop: CycleHop | None
) -> Iterator[HeldBudget]:
    """An L4 inner campaign's share, of what the enclosing run's book has left at its spawn."""
    enclosing = bound_spend_book()
    if enclosing is None:
        raise RuntimeError("an inner run spawns only under its enclosing run's book")
    job_registry = JobRegistry.attach()
    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=UNRESOLVED_HOP if hop is None else hop,
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


@asynccontextmanager
async def paid_pass(
    *, stores: Stores, verb: ScoringVerb, hop: CycleHop | None, declared: Budget
) -> AsyncIterator[SpendBook]:
    """Never queues: its caller waits on the answer."""
    job_registry = JobRegistry.attach()
    job = await asyncio.to_thread(
        request_job,
        stores=stores,
        job_registry=job_registry,
        holder=ScoringPass(verb),
        hop=UNRESOLVED_HOP if hop is None else hop,
    )
    if job.queued:
        refuse_as_busy(stores, job_registry, job)
    admitted = False
    try:
        meters: CeilingMeter = (
            "bill" if hop is None else stores.campaigns.ceiling_meter_of(hop.campaign_id)
        )
        if hop is not None:
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
        raise
    job_registry.release(job.job_id)


def _campaign_ceiling(stores: Stores, hop: CycleHop) -> Budget:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    config = resolve_campaign_config(stores, campaign, hop)
    return next_launch_budget(config, stores=stores, hop=hop).only(MONEY_AXES)


__all__ = [
    "admit_and_hold",
    "admit_and_share",
    "claim_cycle",
    "embedded_share",
    "inner_share",
    "launch_interrupted",
    "open_spend_book",
    "paid_pass",
    "probe_backend",
    "refuse_as_busy",
    "release_slot",
    "request_job",
    "withdraw_queued",
]
