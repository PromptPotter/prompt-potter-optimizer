"""The ``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import TypeAdapter, model_validator

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import RunStart, campaign_at, refuse_campaign
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.config.paths import READOUT_POINTER, user_data_root
from promptpotter.domain.campaign import CampaignRefusedError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import LaunchMove
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import open_append, write_text
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.mint import OpenedCycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
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


async def spawn_job(held: HeldRun) -> asyncio.Task[None]:
    """Returns only once the run's process holds the job (`run_job`): a spawner
    gone before that, its producer lock with it, would leave a job a reconciling read releases
    under its child. Answers the task that ends when the run's process exits."""
    start, stores, hop = held.start, held.session.store, held.session.hop
    log_path = CycleLayout(stores.campaigns.cycle_dir(hop)).job_log
    loop = asyncio.get_running_loop()
    try:
        spec = held.job_spec()
        child = _start(spec, log_path)
        if not await _adoption(loop, child, spec.job_id):
            raise RuntimeError(
                f"job {spec.job_id}'s process exited before taking the job over (log {log_path})"
            )
    except BaseException as exc:
        # No process took the job (its spec unbuilt among them), so nothing else will ever hand its
        # slot back or stamp its cycle.
        start.release(exc, stamps=hop)
        raise

    exited = asyncio.Event()

    def _await_exit() -> None:
        child.wait()
        # A loop closed first is a server gone: its shutdown cancelled the exit's task.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(exited.set)

    # A daemon thread, never `to_thread(child.wait)`: an executor thread holds the server's exit until the run ends.
    threading.Thread(target=_await_exit, name=f"job-{spec.job_id}-exit", daemon=True).start()

    async def watch() -> None:
        await exited.wait()

    exited_task = asyncio.create_task(watch(), name=f"job-{spec.job_id}")
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


# The one line a run's process says on its stdout: it holds the job.
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
    start: RunStart,
    session: Session,
    cycle: CycleConfig,
    *,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[Session, CycleResult]:
    """THE run of a held launch at every door: START, the run, its END. Answers the session the
    run ENDED on (`run_optimization`). Its START, its hold and its END wait on gates, off the loop."""
    await asyncio.to_thread(start.job_registry.move, start.job_id, LaunchMove.START)
    # Unstamped until its observers stand: nothing of this launch reached the cycle before, whose
    # own producer may still be running it.
    stamps: CycleHop | None = None
    try:
        observers = await asyncio.to_thread(
            build_run_observers,
            session=session,
            campaign_config=cycle.config,
            readout_sink=readout_sink,
        )
        stamps = session.hop
        ended, result = await run_optimization(
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
        await asyncio.to_thread(
            start.release, exc, stamps=None if launch_interrupted(exc) else stamps
        )
        raise
    await asyncio.to_thread(start.release, result)
    return ended, result


@dataclass(frozen=True, slots=True)
class HeldRun:
    """A launch past everything that can refuse it: its machine slot held and its ceiling admitted
    (``launcher.admission``), its cycle on disk, a session bound to that cycle. A launch arrives
    here by ``launch.py::hold`` and an arm by its head-to-head's mint, and what is left is the
    run-invocation, the ONE thing an entry point owns: :meth:`detach` or :meth:`run_inline`, each
    closing what opened its session (*closing*) once the run is handed off or ended."""

    start: RunStart
    session: Session
    cycle: CycleConfig
    budget: HeldBudget
    optimizer: SelectedOptimizer
    closing: AsyncExitStack
    mode: RunMode  # `launch.py::Admitted.mode`'s: no door re-derives it

    @classmethod
    def of(
        cls,
        start: RunStart,
        opened: OpenedCycle,
        budget: HeldBudget,
        closing: AsyncExitStack,
        mode: RunMode,
    ) -> HeldRun:
        """The config the cycle reads back as, which a detached run rebuilds, never the one a mint
        was made from."""
        session = opened.session
        stores, hop = session.store, session.hop
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        assert campaign is not None, "the hold minted or loaded it"
        return cls(
            start=start,
            session=session,
            cycle=cycle_config(stores, campaign, hop),
            budget=budget,
            optimizer=opened.optimizer,
            closing=closing,
            mode=mode,
        )

    def job_spec(self) -> JobSpec:
        stores = self.session.store
        return JobSpec(
            job_id=self.start.job_id,
            hop=self.session.hop,
            mode=self.mode,
            identity=stores.identity,
            budget=self.budget,
            jobs_dir=self.start.job_registry.jobs_dir,
            projects_root=stores.projects_root,
            shared_root=stores.shared_root,
            install_datasets_root=stores.install_datasets_root,
            research_datasets_root=stores.research_datasets_root,
        )

    async def detach(self) -> asyncio.Task[None]:
        async with self.closing:
            return await spawn_job(self)

    async def run_inline(
        self, *, readout_sink: Callable[[str], None] | None
    ) -> tuple[Session, CycleResult]:
        session = self.session
        readout = CycleLayout(session.store.campaigns.cycle_dir(session.hop).absolute()).readout
        # The PATH, never a copy: parallel runs each own their cycle's readout, and one copy interleaves.
        with contextlib.suppress(OSError):
            write_text(user_data_root() / READOUT_POINTER, f"{readout}\n")
        async with self.closing:
            return await run_held_job(
                self.start,
                self.session,
                self.cycle,
                mode=self.mode,
                budget=self.budget,
                optimizer=self.optimizer,
                readout_sink=readout_sink,
            )


async def run_job(spec: JobSpec) -> StopOutcome | None:
    stores = build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        install_datasets_root=spec.install_datasets_root,
        research_datasets_root=spec.research_datasets_root,
        shared_root=spec.shared_root,
    )
    job_registry = JobRegistry(spec.jobs_dir, capacity=resolve_run_capacity)
    # The job, its claim with it, now lives and dies with THIS process: a run killed before it
    # declares itself leaves the cycle free.
    if (adopted := job_registry.adopt(spec.job_id)) is None:
        logger.warning("job %s was cleared before its process started — not running", spec.job_id)
        return None
    start = RunStart(stores, job_registry, adopted)
    _tell_spawner()
    try:
        # Asked again here, under the claim: the admitting process's answer is a handoff old.
        refuse_campaign(stores, campaign := campaign_at(stores, spec.hop))
    except CampaignRefusedError as refused:
        # The job's refusal, never the cycle's stop: the cycle keeps the one it had.
        logger.warning("job %s refused past its handoff: %s", spec.job_id, refused)
        start.release(refused)
        return StopOutcome.FAILED
    async with AsyncExitStack() as closing:
        try:
            opened = await closing.enter_async_context(
                ExistingCycle(spec.hop, campaign, Budget()).open(stores)
            )
        except BaseException as exc:
            logger.exception("job %s could not bind its session", spec.job_id)
            start.release(exc, stamps=spec.hop)
            return StopOutcome.FAILED
        try:
            _, result = await run_held_job(
                start,
                opened.session,
                opened.cycle,
                mode=spec.mode,
                budget=spec.budget,
                optimizer=opened.optimizer,
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
