"""``spawn_job`` is the server's half of a run in its OWN process, ``run_job`` the process's. The
``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import complete_registries, init_services
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import (
    job_status_for,
    launch_interrupted,
    release_slot,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolve_campaign_config,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.launch_limits import HeldLimits
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.spend import BudgetChange, SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import IdentityContext, Issuer, TenantId, UserId

logger = logging.getLogger(__name__)

# Out of the server's process group, so its Ctrl+C or a service stop signal is the server's alone.
# The `pause` verb stays the one way to stop a run.
_DETACHED: dict[str, Any] = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32"
    else {"start_new_session": True}
)
_WATCH_POLL_S = 1.0


class JobSpec(StrictModel):
    """Everything the run's process cannot read off disk: who admitted it, at what ceiling, and
    which roots the admitting stores addressed."""

    job_id: str
    campaign_id: str
    cycle_id: str
    # The session a mint or a check-in start already bound; a plain start reads the cycle's own.
    session_id: str | None
    backend_url: str
    stop_after_rounds: int | None
    jobs_dir: str
    projects_root: str
    shared_root: str
    benchmarks_root: str
    user_id: str
    tenant_id: str
    issuer: str | None
    claims: dict[str, Any]
    capabilities: list[str]
    halt_at_accuracy: float | None
    ceiling_usd: float | None
    ceiling_tokens: int | None
    operator_usd: float | None
    operator_tokens: int | None

    @classmethod
    def of(
        cls,
        *,
        stores: Stores,
        job_registry: JobRegistry,
        job_id: str,
        hop: CycleHop,
        session_id: str | None,
        limits: HeldLimits,
        backend_url: str,
        stop_after_rounds: int | None = None,
    ) -> JobSpec:
        identity = stores.identity
        return cls(
            job_id=job_id,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            session_id=session_id,
            backend_url=backend_url,
            stop_after_rounds=stop_after_rounds,
            jobs_dir=str(job_registry.jobs_dir),
            projects_root=str(stores.projects_root),
            shared_root=str(stores.shared_root),
            benchmarks_root=str(stores.benchmarks_root),
            user_id=str(identity.user_id),
            tenant_id=str(identity.tenant_id),
            issuer=None if identity.issuer is None else str(identity.issuer),
            claims=dict(identity.claims),
            capabilities=sorted(identity.capabilities),
            halt_at_accuracy=limits.halt_at_accuracy,
            ceiling_usd=limits.ceiling.usd,
            ceiling_tokens=limits.ceiling.tokens,
            operator_usd=limits.operator.usd,
            operator_tokens=limits.operator.tokens,
        )

    @property
    def hop(self) -> CycleHop:
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    @property
    def identity(self) -> IdentityContext:
        return IdentityContext(
            user_id=UserId(self.user_id),
            tenant_id=TenantId(self.tenant_id),
            issuer=None if self.issuer is None else Issuer(self.issuer),
            claims=self.claims,
            capabilities=frozenset(self.capabilities),
        )

    @property
    def limits(self) -> HeldLimits:
        return HeldLimits(
            halt_at_accuracy=self.halt_at_accuracy,
            ceiling=SpendCeilings(self.ceiling_usd, self.ceiling_tokens),
            operator=BudgetChange(self.operator_usd, self.operator_tokens),
        )


def record_launch_stop(
    *,
    stores: Stores,
    hop: CycleHop,
    session_id: str,
    exc: BaseException,
) -> None:
    """Stamp a launch that ended before its projection pipeline bound. A crash gets ``finished_at``,
    an interrupt gets the paused declaration and none. Best-effort — must never mask *exc*."""
    interrupted = launch_interrupted(exc)
    try:
        cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
        LiveDashboardProjection.write_launch_stop(
            cycle_dir,
            hop=hop,
            session_id=session_id,
            exc=exc,
            interrupted=interrupted,
        )
        if not interrupted:
            stores.campaigns.mark_finished(
                hop,
                status="failed",
                stop_reason=f"{type(exc).__name__}: {exc}",
                finished_at=utcnow_iso(),
                crash_traceback=traceback.format_exc(),
            )
    except Exception:
        logger.exception("failed to record launch stop for %s/%s", hop.campaign_id, hop.cycle_id)


def spawn_job(job_registry: JobRegistry, spec: JobSpec) -> None:
    """Start *spec*'s run in its own process and leave a watcher as the job's task, so a child
    that dies before it finishes its job is reaped by the next read like any torn task."""
    log_path = Path(spec.jobs_dir) / "logs" / f"{spec.job_id}.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log:
            child = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-m", __spec__.name],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                **_DETACHED,
            )
        assert child.stdin is not None
        child.stdin.write(spec.model_dump_json().encode())
        child.stdin.close()
    except BaseException as exc:
        # No process took the job, so nothing else will ever hand its slot back.
        release_slot(job_registry, spec.job_id, exc)
        raise

    async def watch() -> None:
        # Polled, never `to_thread(child.wait)`: a thread parked on a run that outlives the
        # server would hold the server's own exit until that run ended.
        while child.poll() is None:
            await asyncio.sleep(_WATCH_POLL_S)

    job_registry.attach_task(spec.job_id, asyncio.create_task(watch(), name=f"job-{spec.job_id}"))
    logger.info("job %s runs in process %s (log %s)", spec.job_id, child.pid, log_path)


async def _bind_session(spec: JobSpec, stores: Stores) -> tuple[Session, Any, list[Any]]:
    """The session a run of an EXISTING cycle needs, rebuilt from disk — what CLI ``resume`` does."""
    hop = spec.hop
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise LookupError(f"campaign not found: {hop.campaign_id}")
    session = await init_services(
        backend_url=spec.backend_url,
        dataset_name=campaign.dataset_name,
        identity=stores.identity,
    )
    campaign_config = resolve_campaign_config(stores, campaign, hop)
    configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
    # The runner mints on an empty `session.campaign_id`: unbound, it mints a fresh campaign and
    # steals the active pointer, stranding an operator-steered fork in its real campaign.
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id
    index = stores.campaigns.load(hop) or {}
    session_id = spec.session_id or str(index.get("parent_session_id") or "")
    if not session_id:
        raise LookupError(f"cycle {hop.cycle_id} in {hop.campaign_id} has no parent_session_id")
    session.session_id = session_id
    return session, campaign_config, session.samples


async def run_job(spec: JobSpec) -> None:
    """The run's own process: take the job over, rebuild the session, run to its stop."""
    stores = build_stores(
        spec.identity,
        projects_root=Path(spec.projects_root),
        benchmarks_root=Path(spec.benchmarks_root),
        shared_root=Path(spec.shared_root),
    )
    # No `on_reap`: this process attaches to the machine-global jobs dir, it does not own it.
    job_registry = JobRegistry(Path(spec.jobs_dir), capacity=resolve_run_capacity)
    if not job_registry.adopt(spec.job_id):
        logger.warning("job %s was cleared before its process started — not running", spec.job_id)
        return
    try:
        session, campaign_config, train_data = await _bind_session(spec, stores)
    except BaseException as exc:
        logger.exception("job %s could not bind its session", spec.job_id)
        release_slot(job_registry, spec.job_id, exc)
        record_launch_stop(stores=stores, hop=spec.hop, session_id=spec.session_id or "", exc=exc)
        return

    job_id = spec.job_id
    job_registry.mark_started(job_id)
    try:
        observers = build_run_observers(
            session=session,
            campaign_config=campaign_config,
            resumed_from_round=None,
            origin_accuracy=0.0,
        )
        result = await run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            mode=RunMode(stop_after_rounds=spec.stop_after_rounds),
            limits=spec.limits,
        )
        stop_reason = result.stop_reason
        # The SAME classification index.json / dashboard.json / the webapp read.
        outcome = stop_reason_outcome(stop_reason)
        if outcome is StopOutcome.FAILED and result.error is not None:
            persisted_reason: str | None = result.error.message
        else:
            persisted_reason = stop_reason
        job_registry.mark_finished(
            job_id, status=job_status_for(stop_reason), stop_reason=persisted_reason
        )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # The pause flag's SYNTHETIC interrupt, raised by origin scoring to unwind (`scoring/
        # search_point_scorer.py`) outside the round loop's own arm; the process exits clean.
        job_registry.mark_finished(job_id, status="stopped", stop_reason=str(exc) or "paused")
    except Exception as exc:
        # Fired OUTSIDE the runner's own try/except (e.g. ``build_run_observers``), so no
        # ``ErrorRecord`` exists and ``ClassName: message`` is the most the audit trail can have.
        logger.exception("job %s failed", job_id)
        job_registry.mark_finished(
            job_id, status="failed", stop_reason=f"{type(exc).__name__}: {exc}"
        )
        # Nothing wrote the cycle terminal either — stamp it so the fork does not sit frozen
        # at `init` in the file tree and the webapp.
        record_launch_stop(
            stores=session.store, hop=session.hop, session_id=session.session_id, exc=exc
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    )
    spec = JobSpec.model_validate_json(sys.stdin.buffer.read())
    complete_registries(every_treatment=False)
    asyncio.run(run_job(spec))


if __name__ == "__main__":
    main()

__all__ = ["JobSpec", "record_launch_stop", "spawn_job"]
