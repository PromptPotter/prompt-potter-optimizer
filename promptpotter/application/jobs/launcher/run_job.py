"""The ``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.wiring import bind_cycle_session, complete_registries
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import (
    claim_cycle,
    launch_interrupted,
    release_slot,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolve_campaign_config,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import HeldLimits, RunMode
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.results import CycleResult

logger = logging.getLogger(__name__)

# Out of the server's process group, so its Ctrl+C or a service stop signal is the server's alone.
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
    hop: CycleHop
    mode: RunMode
    identity: IdentityContext
    limits: HeldLimits
    jobs_dir: Path
    projects_root: Path
    shared_root: Path
    benchmarks_root: Path


def spawn_job(job_registry: JobRegistry, spec: JobSpec, *, stores: Stores) -> None:
    log_path = spec.jobs_dir / "logs" / f"{spec.job_id}.log"
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
        release_slot(job_registry, spec.job_id, exc, stores=stores, hop=spec.hop)
        raise

    async def watch() -> None:
        # Polled, never `to_thread(child.wait)`: a parked thread would hold the server's exit until the run ended.
        while child.poll() is None:
            await asyncio.sleep(_WATCH_POLL_S)
        # Releases only the claim of a child that died before taking the job over; one that ran has consumed it.
        stores.campaigns.release_claim(
            spec.hop, job_id=spec.job_id, detail="its process exited before the run began"
        )

    job_registry.attach_task(spec.job_id, asyncio.create_task(watch(), name=f"job-{spec.job_id}"))
    logger.info("job %s runs in process %s (log %s)", spec.job_id, child.pid, log_path)


async def run_held_job(
    job_registry: JobRegistry,
    job_id: str,
    session: Session,
    campaign_config: CampaignConfig,
    *,
    mode: RunMode,
    limits: HeldLimits,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[CycleResult, RunObservers]:
    job_registry.mark_started(job_id)
    try:
        observers = build_run_observers(
            session=session, campaign_config=campaign_config, readout_sink=readout_sink
        )
    except BaseException as exc:
        # `hop=None`: nothing of this launch reached the cycle, whose own producer may still be running it.
        release_slot(job_registry, job_id, exc, stores=session.store, hop=None)
        raise
    try:
        result = await run_optimization(
            session.samples,
            campaign_config,
            session=session,
            observers=observers,
            mode=mode,
            limits=limits,
        )
    except BaseException as exc:
        # An interrupt is the pause flag's SYNTHETIC one, its pause already declared; anything else
        # fired OUTSIDE the runner's try, so this stamps it.
        release_slot(
            job_registry,
            job_id,
            exc,
            stores=session.store,
            hop=None if launch_interrupted(exc) else session.hop,
        )
        raise
    job_registry.release(job_id)
    return result, observers


@dataclass(frozen=True, slots=True)
class HeldRun:
    """A launch past everything that can refuse it: its machine slot held and its ceiling admitted
    (``launcher.admission``), its cycle on disk, a session bound to that cycle. Every way in to a
    run arrives here by one of the three ``hold_*`` preambles, and what is left is the
    run-invocation — the ONE thing an entry point owns: :meth:`detach` or :meth:`run_inline`."""

    job_registry: JobRegistry
    job_id: str
    session: Session
    campaign_config: CampaignConfig
    limits: HeldLimits

    @classmethod
    def of(
        cls,
        job_registry: JobRegistry,
        job_id: str,
        session: Session,
        limits: HeldLimits,
    ) -> HeldRun:
        """For a preamble that MINTS the manifest: the config it reads back as, which a detached run rebuilds, not the one it was made from."""
        stores, hop = session.store, session.hop
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        assert campaign is not None, "a preamble minted or loaded it"
        return cls(
            job_registry=job_registry,
            job_id=job_id,
            session=session,
            campaign_config=resolve_campaign_config(stores, campaign, hop),
            limits=limits,
        )

    def job_spec(self, mode: RunMode) -> JobSpec:
        stores = self.session.store
        return JobSpec(
            job_id=self.job_id,
            hop=self.session.hop,
            mode=mode,
            identity=stores.identity,
            limits=self.limits,
            jobs_dir=self.job_registry.jobs_dir,
            projects_root=stores.projects_root,
            shared_root=stores.shared_root,
            benchmarks_root=stores.benchmarks_root,
        )

    def detach(self, *, mode: RunMode) -> None:
        spawn_job(self.job_registry, self.job_spec(mode), stores=self.session.store)

    async def run_inline(
        self, *, mode: RunMode, readout_sink: Callable[[str], None] | None
    ) -> tuple[CycleResult, RunObservers]:
        return await run_held_job(
            self.job_registry,
            self.job_id,
            self.session,
            self.campaign_config,
            mode=mode,
            limits=self.limits,
            readout_sink=readout_sink,
        )


async def _bound_session(spec: JobSpec, stores: Stores) -> tuple[Session, CampaignConfig]:
    campaign = stores.campaigns.load_campaign(spec.hop.campaign_id)
    if campaign is None:
        raise NotFoundError(f"campaign not found: {spec.hop.campaign_id}")
    session, campaign_config = await bind_cycle_session(stores, campaign, spec.hop)
    configure_and_apply_pipeline(session, campaign_config)
    return session, campaign_config


async def run_job(spec: JobSpec) -> StopOutcome | None:
    stores = build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        benchmarks_root=spec.benchmarks_root,
        shared_root=spec.shared_root,
    )
    job_registry = JobRegistry(spec.jobs_dir, capacity=resolve_run_capacity)
    if not job_registry.adopt(spec.job_id):
        logger.warning("job %s was cleared before its process started — not running", spec.job_id)
        return None
    adopted = job_registry.get(spec.job_id)
    if adopted is not None:
        # The claim now lives and dies with THIS process: a run killed before it declares itself leaves the cycle free.
        claim_cycle(stores, job_registry, adopted, spec.hop)
    try:
        session, campaign_config = await _bound_session(spec, stores)
    except BaseException as exc:
        logger.exception("job %s could not bind its session", spec.job_id)
        release_slot(job_registry, spec.job_id, exc, stores=stores, hop=spec.hop)
        return StopOutcome.FAILED

    try:
        result, _observers = await run_held_job(
            job_registry,
            spec.job_id,
            session,
            campaign_config,
            mode=spec.mode,
            limits=spec.limits,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        return StopOutcome.PAUSED
    except Exception:
        logger.exception("job %s failed", spec.job_id)
        return StopOutcome.FAILED
    return stop_reason_outcome(result.stop_reason)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    )
    spec = JobSpec.model_validate_json(sys.stdin.buffer.read())
    complete_registries(every_treatment=False)
    if (outcome := asyncio.run(run_job(spec))) is not None and (code := outcome.exit_code):
        sys.exit(code)


if __name__ == "__main__":
    main()

__all__ = ["HeldRun", "JobSpec", "run_held_job", "spawn_job"]
