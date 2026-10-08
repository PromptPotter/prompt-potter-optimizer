"""``spawn_job`` is the server's half of a run in its OWN process, ``run_job`` the process's. The
``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.wiring import bind_cycle_session, complete_registries
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import (
    job_status_for,
    launch_interrupted,
    release_slot,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import HeldLimits
from promptpotter.domain.spend import BudgetChange, SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import AccessState, IdentityContext, Issuer, TenantId, UserId

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

# Out of the server's process group, so its Ctrl+C or a service stop signal is the server's alone.
# The `pause` verb stays the one way to stop a run.
_DETACHED: dict[str, Any]
if sys.platform == "win32":
    _DETACHED = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
else:
    _DETACHED = {"start_new_session": True}
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
    email: str | None
    provider: str | None
    access_state: AccessState
    claims: dict[str, Any]
    capabilities: list[str]
    halt_at_accuracy: float | None
    ceiling_usd: float | None
    ceiling_tokens: int | None
    operator_usd: float | None
    operator_tokens: int | None
    reserve_usd: float | None
    reserve_tokens: int | None

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
            email=identity.email,
            provider=identity.provider,
            access_state=identity.access_state,
            claims=dict(identity.claims),
            capabilities=sorted(identity.capabilities),
            halt_at_accuracy=limits.halt_at_accuracy,
            ceiling_usd=limits.ceiling.usd,
            ceiling_tokens=limits.ceiling.tokens,
            operator_usd=limits.operator.usd,
            operator_tokens=limits.operator.tokens,
            reserve_usd=limits.reserve.usd,
            reserve_tokens=limits.reserve.tokens,
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
            email=self.email,
            provider=self.provider,
            access_state=self.access_state,
            claims=self.claims,
            capabilities=frozenset(self.capabilities),
        )

    @property
    def limits(self) -> HeldLimits:
        return HeldLimits(
            halt_at_accuracy=self.halt_at_accuracy,
            ceiling=SpendCeilings(self.ceiling_usd, self.ceiling_tokens),
            operator=BudgetChange(self.operator_usd, self.operator_tokens),
            reserve=SpendCeilings(self.reserve_usd, self.reserve_tokens),
        )


def spawn_job(job_registry: JobRegistry, spec: JobSpec, *, stores: Stores) -> None:
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
        # No process took the job, so nothing else will ever hand its slot back or stamp its cycle.
        release_slot(
            job_registry,
            spec.job_id,
            exc,
            stores=stores,
            hop=spec.hop,
            session_id=spec.session_id or "",
        )
        raise

    async def watch() -> None:
        # Polled, never `to_thread(child.wait)`: a thread parked on a run that outlives the
        # server would hold the server's own exit until that run ended.
        while child.poll() is None:
            await asyncio.sleep(_WATCH_POLL_S)

    job_registry.attach_task(spec.job_id, asyncio.create_task(watch(), name=f"job-{spec.job_id}"))
    logger.info("job %s runs in process %s (log %s)", spec.job_id, child.pid, log_path)


async def run_held_job(
    job_registry: JobRegistry,
    job_id: str,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    *,
    mode: RunMode,
    limits: HeldLimits,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[CycleResult, RunObservers]:
    """Run *session*'s cycle on the slot *job_id* holds, and finish the job with it — the one tail
    of every launch that holds a machine slot. *limits* is what admission HELD."""
    job_registry.mark_started(job_id)
    try:
        observers = build_run_observers(
            session=session, campaign_config=campaign_config, readout_sink=readout_sink
        )
        result = await run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            mode=mode,
            limits=limits,
        )
    except BaseException as exc:
        # An interrupt is the pause flag's SYNTHETIC one (`scoring/search_point_scorer.py`), its
        # pause already declared. Anything else fired OUTSIDE the runner's try, so this stamps it.
        release_slot(
            job_registry,
            job_id,
            exc,
            stores=session.store,
            hop=None if launch_interrupted(exc) else session.hop,
            session_id=session.session_id,
        )
        raise
    job_registry.mark_finished(
        job_id, status=job_status_for(result.stop_reason), stop_reason=result.stop_reason
    )
    return result, observers


async def _bound_session(spec: JobSpec, stores: Stores) -> tuple[Session, CampaignConfig]:
    hop = spec.hop
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {hop.campaign_id}")
    session, campaign_config = await bind_cycle_session(
        stores, campaign, hop, backend_url=spec.backend_url
    )
    configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
    session.session_id = spec.session_id or stores.campaigns.session_id_of(hop)
    return session, campaign_config


async def run_job(spec: JobSpec) -> None:
    """The run's own process: take the job over, rebuild the session, run to its stop."""
    projects_root = Path(spec.projects_root)
    stores = build_stores(
        spec.identity,
        projects_root=projects_root,
        benchmarks_root=Path(spec.benchmarks_root),
        shared_root=Path(spec.shared_root),
    )
    job_registry = JobRegistry(
        Path(spec.jobs_dir), capacity=resolve_run_capacity, projects_root=projects_root
    )
    if not job_registry.adopt(spec.job_id):
        logger.warning("job %s was cleared before its process started — not running", spec.job_id)
        return
    try:
        session, campaign_config = await _bound_session(spec, stores)
    except BaseException as exc:
        logger.exception("job %s could not bind its session", spec.job_id)
        release_slot(
            job_registry,
            spec.job_id,
            exc,
            stores=stores,
            hop=spec.hop,
            session_id=spec.session_id or "",
        )
        return

    try:
        await run_held_job(
            job_registry,
            spec.job_id,
            session,
            campaign_config,
            session.samples,
            mode=RunMode(stop_after_rounds=spec.stop_after_rounds),
            limits=spec.limits,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        # A pause: `run_held_job` answered for the job, and the process exits clean.
        pass
    except Exception:
        logger.exception("job %s failed", spec.job_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    )
    spec = JobSpec.model_validate_json(sys.stdin.buffer.read())
    complete_registries(every_treatment=False)
    asyncio.run(run_job(spec))


if __name__ == "__main__":
    main()

__all__ = ["JobSpec", "run_held_job", "spawn_job"]
