"""The prologue every launch runs before anything irreversible, in two steps that different
callers wait in different places for: ``request_launch`` accepts (per-user quotas, then a machine
slot or a place in the queue) and ``admit_and_hold`` holds it through the backend probe and the
account wallet. One owner, so a capacity or wallet rule taught here reaches every way in rather
than the one being edited; the slot-release discipline that failing any of it owes lives here too."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

from promptpotter import connectors
from promptpotter.application.jobs.quota import (
    SpendCeilings,
    admit_launch,
    check_launch_quotas,
)
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    Job,
    JobRegistry,
    JobStatus,
)
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import ConflictError, MachineBusyError
from promptpotter.shared.identity import claim_email

logger = logging.getLogger(__name__)

# How often a waiting launch re-asks. Short enough that a freed slot is taken while the operator
# is still looking at the screen, long enough that a queue of them is not a busy-loop on the
# machine-wide admission lock every run has to take.
_QUEUE_POLL_S = 2.0

# Detached queued launches, held so the event loop cannot collect one mid-wait.
_DETACHED: set[asyncio.Task[Any]] = set()

# Sole bridge from the StopReason outcome table to JobRegistry's lifecycle vocabulary; there is no
# per-reason reconciler, and every surface that ends a run reads it here — a private copy is how a
# cycle comes to read "failed" on one surface and "completed" on another.
_JOB_STATUS_BY_OUTCOME: dict[StopOutcome, JobStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "stopped",
    StopOutcome.FAILED: "failed",
    # A pause exits the worker but the cycle stays resumable — a fresh start-run mints a new
    # job to continue it.
    StopOutcome.PAUSED: "stopped",
}


def job_status_for(stop_reason: StopReason | str) -> JobStatus:
    """What a finished run's job is stamped, given why the run stopped."""
    return _JOB_STATUS_BY_OUTCOME[stop_reason_outcome(stop_reason)]


def launch_interrupted(exc: BaseException) -> bool:
    """True when the launch stopped because someone ASKED — the pause flag's synthetic
    ``KeyboardInterrupt``, a Ctrl+C's ``CancelledError``, a host cancel. None is visible to ``except Exception``."""
    return isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)


def release_slot(
    job_registry: JobRegistry, job_id: str, exc: BaseException, *, admitted: bool = True
) -> None:
    """Hand the machine slot back so a failed launch never wedges the box at capacity. EVERY launch
    failure answers for the job; only one that already bound the cycle answers for the cycle too.

    A launch that never got past ADMISSION is ``stopped``, not ``failed`` — the account's ceiling, a
    dark backend and a busy machine each REFUSE it before anything runs or spends."""
    if launch_interrupted(exc):
        job_registry.mark_finished(job_id, status="stopped", stop_reason="launch_interrupted")
    elif not admitted:
        job_registry.mark_finished(job_id, status="stopped", stop_reason="launch_not_admitted")
    else:
        job_registry.mark_finished(job_id, status="failed", stop_reason="launch_aborted")


async def probe_backend(backend_type: str, backend_url: str) -> None:
    """Resolve the connector and run its reachability probe. A connector opts out by leaving
    ``Connector.preflight = None``; a raised ``BackendUnreachableError`` becomes a 503.

    **Every launch ingress asks the CONNECTOR, never a bare wire probe.** An `in_process`
    connector has no wire, so a bare probe refuses a campaign over a backend it never touches —
    `promptpotter-self` is the one that cannot survive it.

    An empty ``backend_type`` is the tolerant answer ``wiring.backend_type_of_dataset`` gives when
    a campaign has outlived its dataset dir. There is no declared connector to ask, so there is no
    probe — and ``connectors.get`` is strict, so resolving it would raise past every caller's
    ``BackendUnreachableError`` handler."""
    if not backend_type:
        return
    connector = connectors.get(backend_type)
    if connector.preflight is None:
        return
    await connector.preflight(backend_url)


def _user_of(stores: Stores) -> User:
    return stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )


def request_launch(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    dataset_name: str,
    hop: CycleHop = UNRESOLVED_HOP,
    rate_limited: bool = True,
) -> Job:
    """Accept one launch: check what the CALLER may start, then take a machine slot or join the
    queue for one. Returns the job either way — ``status`` says which.

    Both counts come off the same on-disk jobs, so they are read under ONE gate. Split across two,
    a pair of simultaneous launches each read the user's last free slot and both take it; inside
    it the reservation is written before either can look again, and before any ``await``.

    The per-user ceilings still REFUSE rather than queue, and that asymmetry is the point: the
    machine being full is temporary and nobody's fault, while an account at its own limit is a
    fact about that account which waiting cannot change.

    ``hop`` stays :data:`UNRESOLVED_HOP` where the ids do not exist yet (a fresh mint); the caller
    binds them with ``update_target`` once the mint resolves them."""
    user = _user_of(stores)
    with job_registry.admission_gate():
        check_launch_quotas(user=user, job_registry=job_registry, rate_limited=rate_limited)
        return job_registry.request_slot(
            user_id=str(stores.identity.user_id), dataset_name=dataset_name, hop=hop
        )


def refuse_as_busy(job_registry: JobRegistry, job: Job) -> NoReturn:
    """Answer a full box with a 409 instead of a place in line, for the one caller that asked not
    to wait. It is a REFUSAL, so the queue entry goes with it: leaving one behind starts the run
    later, which is what ``--no-wait`` says not to do."""
    job_registry.cancel_queued(job.job_id, user_id=job.user_id)
    holder = job_registry.holder()
    raise MachineBusyError(
        holder_user="" if holder is None else holder.user_id,
        campaign_id="" if holder is None else holder.campaign_id,
        cycle_id="" if holder is None else holder.cycle_id,
        started_at=None if holder is None else holder.started_at,
    )


async def await_slot(job_registry: JobRegistry, job: Job) -> None:
    """Block until this queued launch is first in line and a slot is free.

    A poll, not a signal, because the freeing event happens in whichever process owned the run
    that ended — possibly a terminal one — and the only thing both sides share is the jobs dir.
    ``claim_next`` is the atomic half; this is just how often we ask.

    ``QUEUE_MAX_WAIT_S`` bounds it: an unbounded queue is a promise the box may never keep, and a
    launch that waited all night is not one the operator still wants."""
    deadline = time.monotonic() + settings.QUEUE_MAX_WAIT_S
    while not await asyncio.to_thread(job_registry.claim_next, job.job_id):
        if time.monotonic() >= deadline:
            job_registry.mark_finished(job.job_id, status="stopped", stop_reason="queue_expired")
            raise ConflictError(
                f"This launch waited {settings.QUEUE_MAX_WAIT_S / 3600:.0f}h for a free slot and "
                f"was withdrawn. Nothing ran and nothing was spent; start it again when the "
                f"machine is quieter.",
                code="queue_expired",
            )
        await asyncio.sleep(_QUEUE_POLL_S)


async def launch(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    dataset_name: str,
    run: Callable[[Job], Awaitable[Any]],
    hop: CycleHop = UNRESOLVED_HOP,
    rate_limited: bool = True,
) -> Job:
    """Accept a launch and run it — **inline when a slot was free, detached when it had to queue**.

    The ONE seam every non-terminal launch takes, because the branch is identical everywhere and
    getting it wrong is invisible from the call site: an applier that simply awaited a queued
    launch would hold its HTTP request open until the box drained, and the operator would read a
    hung browser rather than "queued, position 2".

    The terminal does not come through here — it has somewhere to wait (a person is watching it),
    so it calls :func:`request_launch` and :func:`admit_and_hold` itself and blocks."""
    job = request_launch(
        stores=stores,
        job_registry=job_registry,
        dataset_name=dataset_name,
        hop=hop,
        rate_limited=rate_limited,
    )
    if job.status == "queued":
        _detach(run(job), what=f"queued launch {job.job_id} ({dataset_name})")
    else:
        await run(job)
    return job


def _detach(coro: Awaitable[Any], *, what: str) -> None:
    """Run a queued launch's remainder in the background. The reference is held because the loop
    collects a task nobody keeps, and the result is READ because nobody awaits it — an exception
    here would otherwise surface as a warning at interpreter shutdown, hours after the launch the
    operator is still waiting for silently died."""
    task = asyncio.ensure_future(coro)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    task.add_done_callback(lambda t: _report_detached(t, what))


def _report_detached(task: asyncio.Task[Any], what: str) -> None:
    if task.cancelled():
        logger.info("%s was cancelled", what)
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s failed: %s", what, exc, exc_info=exc)


async def admit_and_hold(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job,
    verb: str,
    dataset_name: str,
    backend_type: str,
    backend_url: str,
    requested_cap_usd: float | None = None,
    requested_cap_tokens: int | None = None,
) -> SpendCeilings:
    """Hold *job*'s slot through the irreversible half of a launch, and return the ceilings it was
    admitted at. A queued job waits here for its turn first.

    Nothing here touches a cycle, so a failure answers for the machine slot alone and leaves the
    campaign re-startable once the account has room again — which is why the whole prologue runs
    BEFORE the mint, and why its own failure path releases the slot rather than stamping a cycle."""
    user = _user_of(stores)
    if job.status == "queued":
        await await_slot(job_registry, job)

    try:
        t0 = time.perf_counter()
        await probe_backend(backend_type, backend_url)
        t_probe = time.perf_counter()
        # The wallet read globs + reads every cycle ledger — offload so the scan never blocks the
        # single event loop on the launch path.
        ceilings = await asyncio.to_thread(
            admit_launch,
            requested_cap_usd=requested_cap_usd,
            requested_cap_tokens=requested_cap_tokens,
            user=user,
            stores=stores,
            job_registry=job_registry,
            job_id=job.job_id,
        )
        # Before the caller's first await, so a concurrent launch on this account reads a stamped
        # reservation rather than an unquotable one.
        job_registry.set_caps(job.job_id, cap_usd=ceilings.usd, cap_tokens=ceilings.tokens)
        t_caps = time.perf_counter()
    except BaseException as exc:
        release_slot(job_registry, job.job_id, exc, admitted=False)
        raise

    # Pre-202 phase timing — the operator waits on the synchronous part of a launch, so where it
    # goes lands on disk. The caller times its own `init_services`, the third of the three.
    logger.info(
        "admission[%s %s]: probe=%.2fs wallet=%.2fs (job %s)",
        verb,
        dataset_name,
        t_probe - t0,
        t_caps - t_probe,
        job.job_id,
    )
    return ceilings


__all__ = [
    "admit_and_hold",
    "await_slot",
    "job_status_for",
    "launch",
    "launch_interrupted",
    "probe_backend",
    "refuse_as_busy",
    "release_slot",
    "request_launch",
]
