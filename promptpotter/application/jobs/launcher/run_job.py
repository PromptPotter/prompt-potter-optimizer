"""The ``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import TypeAdapter, model_validator

from promptpotter.application.initialization.wiring import bind_cycle_session, complete_registries
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import claim_cycle, release_slot
from promptpotter.application.jobs.mint import resolve_resume
from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.config.paths import READOUT_POINTER, user_data_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import HeldBudget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import open_append, write_text
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.mint import MintedCycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.campaign_config import CycleConfig
    from promptpotter.domain.results import CycleResult

logger = logging.getLogger(__name__)

# Out of the server's process group, so its Ctrl+C or a service stop signal is the server's alone.
_DETACHED: dict[str, Any]
if sys.platform == "win32":
    _DETACHED = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
else:
    _DETACHED = {"start_new_session": True}


class JobSpec(StrictModel):
    """Everything the run's process cannot read off disk: who admitted it, at what ceiling, and
    which roots the admitting stores addressed."""

    job_id: str
    hop: CycleHop
    mode: RunMode
    identity: IdentityContext
    budget: HeldBudget
    jobs_dir: Path
    projects_root: Path
    shared_root: Path
    install_datasets_root: Path
    research_datasets_root: Path | None

    @model_validator(mode="after")
    def _reads_back_as_written(self) -> Self:
        # The run's process rebuilds every field off this JSON: one reading back otherwise is another launch.
        wire = self.model_dump(mode="json")
        for name, field in type(self).model_fields.items():
            held = getattr(self, name)
            if TypeAdapter(field.annotation).validate_python(wire[name]) != held:
                raise ValueError(f"JobSpec.{name} does not read back as written: {held!r}")
        return self


async def spawn_job(
    job_registry: JobRegistry, spec: JobSpec, *, stores: Stores
) -> asyncio.Task[None]:
    """Returns only once the run's process holds the job and its claim (`run_job`): a spawner
    gone before that, its producer lock with it, would leave a job a reconciling read releases
    under its child. Answers the task that ends when the run's process exits."""
    log_path = CycleLayout(stores.campaigns.cycle_dir(spec.hop)).job_log
    try:
        child = _start(spec, log_path)
    except BaseException as exc:
        # No process took the job, so nothing else will ever hand its slot back or stamp its cycle.
        release_slot(job_registry, spec.job_id, exc, stores=stores, hop=spec.hop)
        raise

    loop = asyncio.get_running_loop()
    if not await _adoption(loop, child, spec.job_id):
        refused = RuntimeError(
            f"job {spec.job_id}'s process exited before taking the job over (log {log_path})"
        )
        release_slot(job_registry, spec.job_id, refused, stores=stores, hop=spec.hop)
        raise refused

    exited = asyncio.Event()

    def _await_exit() -> None:
        child.wait()
        # A loop closed first is a server gone: its shutdown cancelled `watch`, which has nothing to release.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(exited.set)

    # A daemon thread, never `to_thread(child.wait)`: an executor thread holds the server's exit until the run ends.
    threading.Thread(target=_await_exit, name=f"job-{spec.job_id}-exit", daemon=True).start()

    async def watch() -> None:
        await exited.wait()
        # Releases only the claim of a child that died before its run declared itself; one that ran has consumed it.
        stores.campaigns.release_claim(
            spec.hop, job_id=spec.job_id, detail="its process exited before the run began"
        )

    exited_task = asyncio.create_task(watch(), name=f"job-{spec.job_id}")
    job_registry.attach_task(spec.job_id, exited_task)
    logger.info("job %s runs in process %s (log %s)", spec.job_id, child.pid, log_path)
    return exited_task


def _start(spec: JobSpec, log_path: Path) -> subprocess.Popen[bytes]:
    """The run's process, its spec on stdin; stdout is `_adoption`'s, stderr the job's log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open_append(log_path) as log:
        child = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", __spec__.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            **_DETACHED,
        )
    assert child.stdin is not None
    child.stdin.write(spec.model_dump_json().encode())
    child.stdin.close()
    return child


# The one line a run's process says on its stdout: it holds the job and its claim.
_ADOPTED = b"adopted\n"


def _adoption(
    loop: asyncio.AbstractEventLoop, child: subprocess.Popen[bytes], job_id: str
) -> asyncio.Future[bool]:
    """Whether *child* adopted its job; ``False`` once it exits without saying so. Read on a
    daemon thread, for the reason `_await_exit` is."""
    said: asyncio.Future[bool] = loop.create_future()

    def settle(adopted: bool) -> None:
        if not said.done():
            said.set_result(adopted)

    def read() -> None:
        assert child.stdout is not None
        with child.stdout:
            adopted = any(line == _ADOPTED for line in child.stdout)
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(settle, adopted)

    threading.Thread(target=read, name=f"job-{job_id}-adopt", daemon=True).start()
    return said


def _tell_spawner() -> None:
    """`spawn_job`'s handshake; stdout then joins the log, which closes the pipe."""
    sys.stdout.flush()
    sys.stdout.buffer.write(_ADOPTED)
    sys.stdout.buffer.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


async def run_held_job(
    job_registry: JobRegistry,
    job_id: str,
    session: Session,
    cycle: CycleConfig,
    *,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[CycleResult, RunObservers]:
    job_registry.mark_started(job_id)
    try:
        observers = build_run_observers(
            session=session, campaign_config=cycle.config, readout_sink=readout_sink
        )
    except BaseException as exc:
        # `hop=None`: nothing of this launch reached the cycle, whose own producer may still be running it.
        release_slot(job_registry, job_id, exc, stores=session.store, hop=None)
        raise
    try:
        _, result = await run_optimization(
            list(session.bank.samples),
            cycle,
            session=session,
            observers=observers,
            mode=mode,
            budget=budget,
            optimizer=optimizer,
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
    job_registry.release(job_id, result.stop_reason, stepped=result.stepped)
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
    cycle: CycleConfig
    budget: HeldBudget
    optimizer: SelectedOptimizer

    @classmethod
    def of(
        cls,
        job_registry: JobRegistry,
        job_id: str,
        minted: MintedCycle,
        budget: HeldBudget,
    ) -> HeldRun:
        """For a preamble that MINTS the manifest: the config it reads back as, which a detached run rebuilds, not the one it was made from."""
        session = minted.session
        stores, hop = session.store, session.hop
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        assert campaign is not None, "a preamble minted or loaded it"
        return cls(
            job_registry=job_registry,
            job_id=job_id,
            session=session,
            cycle=cycle_config(stores, campaign, hop),
            budget=budget,
            optimizer=minted.optimizer,
        )

    def job_spec(self, mode: RunMode) -> JobSpec:
        stores = self.session.store
        return JobSpec(
            job_id=self.job_id,
            hop=self.session.hop,
            mode=mode,
            identity=stores.identity,
            budget=self.budget,
            jobs_dir=self.job_registry.jobs_dir,
            projects_root=stores.projects_root,
            shared_root=stores.shared_root,
            install_datasets_root=stores.install_datasets_root,
            research_datasets_root=stores.research_datasets_root,
        )

    async def detach(self, *, mode: RunMode) -> asyncio.Task[None]:
        return await spawn_job(self.job_registry, self.job_spec(mode), stores=self.session.store)

    async def run_inline(
        self, *, mode: RunMode, readout_sink: Callable[[str], None] | None
    ) -> tuple[CycleResult, RunObservers]:
        session = self.session
        readout = CycleLayout(session.store.campaigns.cycle_dir(session.hop).absolute()).readout
        # The PATH, never a copy: parallel runs each own their cycle's readout, and one copy interleaves.
        with contextlib.suppress(OSError):
            write_text(user_data_root() / READOUT_POINTER, f"{readout}\n")
        return await run_held_job(
            self.job_registry,
            self.job_id,
            self.session,
            self.cycle,
            mode=mode,
            budget=self.budget,
            optimizer=self.optimizer,
            readout_sink=readout_sink,
        )


async def open_held_cycle(
    stores: Stores, campaign: Campaign, hop: CycleHop
) -> tuple[Session, CycleConfig, SelectedOptimizer]:
    bound = await bind_cycle_session(stores, campaign, hop)
    plan = resolve_resume(bound, campaign, bound.bank.samples)
    return plan.session, plan.cycle, plan.optimizer


async def run_job(spec: JobSpec) -> StopOutcome | None:
    stores = build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        install_datasets_root=spec.install_datasets_root,
        research_datasets_root=spec.research_datasets_root,
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
    _tell_spawner()
    try:
        campaign = stores.campaigns.load_campaign(spec.hop.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {spec.hop.campaign_id}")
        session, cycle, optimizer = await open_held_cycle(stores, campaign, spec.hop)
    except BaseException as exc:
        logger.exception("job %s could not bind its session", spec.job_id)
        release_slot(job_registry, spec.job_id, exc, stores=stores, hop=spec.hop)
        return StopOutcome.FAILED

    try:
        result, _observers = await run_held_job(
            job_registry,
            spec.job_id,
            session,
            cycle,
            mode=spec.mode,
            budget=spec.budget,
            optimizer=optimizer,
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
