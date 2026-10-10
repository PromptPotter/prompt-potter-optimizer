from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, NoReturn

from promptpotter import connectors
from promptpotter.application.jobs.quota import (
    admit_launch,
    check_launch_quotas,
    declare_run_ceiling,
)
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    Job,
    JobRegistry,
)
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.config.settings import settings
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import HeldLimits
from promptpotter.domain.phases import (
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import ErrorRecord, RunPhaseRecord
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import ConflictError, CycleBusyError, MachineBusyError
from promptpotter.shared.identity import acting_principal_id

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import LaunchRequest

logger = logging.getLogger(__name__)

# Each ask takes the machine-wide admission lock.
_QUEUE_POLL_S = 2.0


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
    """A launch that never reached its admission (``Job.admitted_at``) was REFUSED, whatever raised."""
    job = job_registry.get(job_id)
    if (job is not None and job.admitted_at is not None) or launch_interrupted(exc):
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


async def probe_backend(backend_type: str, backend_url: str) -> BackendUnreachableError | None:
    # A campaign that outlived its dataset dir: ``connectors.get`` is strict and would raise.
    if not backend_type:
        return None
    connector = connectors.get(backend_type)
    if connector.preflight is None:
        return None
    if (down := await connector.preflight(backend_url)) is not None:
        return BackendUnreachableError(connector.name, backend_url, down)
    return None


def _user_of(stores: Stores) -> User:
    return stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=stores.identity.email,
    )


def request_launch(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    dataset_name: str,
    hop: CycleHop = UNRESOLVED_HOP,
) -> Job:
    user = _user_of(stores)
    # ONE gate: the quota count and the slot count come off the same on-disk jobs.
    with job_registry.admission_gate():
        check_launch_quotas(user=user, stores=stores, job_registry=job_registry, hop=hop)
        if hop != UNRESOLVED_HOP:
            _refuse_taken_cycle(stores, job_registry, hop)
        job = job_registry.request_slot(
            user_id=str(stores.identity.user_id),
            principal_id=acting_principal_id(stores.identity),
            dataset_name=dataset_name,
            hop=hop,
        )
        if hop != UNRESOLVED_HOP:
            claim_cycle(stores, job_registry, job, hop)
        return job


def _refuse_taken_cycle(stores: Stores, job_registry: JobRegistry, hop: CycleHop) -> None:
    run = derive_run_state(stores.campaigns.cycle_dir(hop))
    if not run.producer.attached:
        return
    claim = stores.campaigns.launch_claim(hop)
    holder = job_registry.running_job_for(hop) if claim is None else job_registry.get(claim.job_id)
    raise CycleBusyError(
        job_id="unregistered" if holder is None else holder.job_id,
        status=run.run_phase.value,
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
    holder = job_registry.holder()
    raise MachineBusyError(
        holder_user="" if holder is None else holder.user_id,
        campaign_id="" if holder is None else holder.campaign_id,
        cycle_id="" if holder is None else holder.cycle_id,
        started_at=None if holder is None else holder.started_at,
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


async def await_slot(job_registry: JobRegistry, job: Job) -> None:
    """A poll, not a signal: the slot frees in another process, and only the jobs dir is shared."""
    deadline = time.monotonic() + settings.QUEUE_MAX_WAIT_S
    while not await asyncio.to_thread(job_registry.claim_next, job.job_id):
        if time.monotonic() >= deadline:
            raise ConflictError(
                f"This launch waited {settings.QUEUE_MAX_WAIT_S / 3600:.0f}h for a free slot and "
                f"was withdrawn. Nothing ran and nothing was spent; start it again when the "
                f"machine is quieter.",
                code="queue_expired",
            )
        await asyncio.sleep(_QUEUE_POLL_S)


async def admit_and_hold(
    request: LaunchRequest, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldLimits:
    """The request's config is resolved IN here, after the queue wait, so the ceiling admitted is the one the run starts under."""
    user = _user_of(stores)
    # A fresh mint has no cycle yet: nothing to claim, and no seed or standing ceiling to read.
    hop = None if request.hop == UNRESOLVED_HOP else request.hop
    if job.queued:
        await await_slot(job_registry, job)
        promoted = job_registry.get(job.job_id)
        if hop is not None and promoted is not None:
            claim_cycle(stores, job_registry, promoted, hop)

    t0 = time.perf_counter()
    if (down := await probe_backend(*request.backend(stores))) is not None:
        raise down
    t_probe = time.perf_counter()
    declared, operator = declare_run_ceiling(
        request.campaign_config(stores), stores=stores, hop=hop, requested=request.limits.ceiling
    )
    # Offloaded: the wallet read scans every cycle ledger and must not block the event loop.
    ceiling, reserve = await asyncio.to_thread(
        admit_launch,
        declared=declared,
        user=user,
        stores=stores,
        job_registry=job_registry,
        job_id=job.job_id,
        hop=hop,
    )
    held = HeldLimits.admitted(request.limits, ceiling, operator, reserve=reserve)
    # Before the caller's first await: a concurrent launch on this account reads a stamped reservation.
    job_registry.mark_admitted(job.job_id, reserve)
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


__all__ = [
    "admit_and_hold",
    "await_slot",
    "claim_cycle",
    "launch_interrupted",
    "probe_backend",
    "refuse_as_busy",
    "release_slot",
    "request_launch",
    "withdraw_queued",
]
