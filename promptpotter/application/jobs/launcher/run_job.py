"""The ``JobSpec`` rides stdin, never a file: an identity rebuilt from a tenant name is the HOST's."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import TypeAdapter, model_validator

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import refuse_campaign
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.run_init import enter_cycle
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import READOUT_POINTER, user_data_root
from promptpotter.domain.campaign_config import CycleConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
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
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.mint import OpenedCycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer

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
    stores, hop = held.session.store, held.session.hop
    log_path = CycleLayout(stores.campaigns.cycle_dir(hop)).job_log
    loop = asyncio.get_running_loop()
    # Until a process takes the job (its spec unbuilt among them), nothing else will ever hand its
    # slot back or stamp its cycle: the opening's exit does, on a raise.
    held.opening.owns(hop)
    spec = held.job_spec()
    child = _start(spec, log_path)
    if not await _adoption(loop, child, spec.job_id):
        raise RuntimeError(
            f"job {spec.job_id}'s process exited before taking the job over (log {log_path})"
        )
    held.opening.hand_off()

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
    return exited_task


def _start(spec: JobSpec, log_path: Path) -> subprocess.Popen[bytes]:
    """The run's process, its spec on stdin; stdout is `_adoption`'s, stderr the job's log."""
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


@dataclass(frozen=True, slots=True)
class HeldRun:
    """A launch past everything that can refuse it: its machine slot held and its ceiling admitted
    (``launcher.admission``), its cycle on disk, a session bound to that cycle. A launch arrives
    here by ``launch.py::hold`` and an arm by its head-to-head's mint, and what is left is the
    run-invocation, the ONE thing an entry point owns: :meth:`detach` or :meth:`run`, each closing
    what opened its session and its opening's exit (*closing*) once the run is handed off or ended."""

    opening: Opening
    session: Session
    cycle: CycleConfig
    budget: HeldBudget
    optimizer: SelectedOptimizer
    closing: AsyncExitStack
    mode: RunMode  # `launch.py::Admitted.mode`'s: no door re-derives it

    @classmethod
    def of(
        cls,
        opening: Opening,
        opened: OpenedCycle,
        budget: HeldBudget,
        closing: AsyncExitStack,
        mode: RunMode,
    ) -> HeldRun:
        """The opened cycle is what it reads back as, which a detached run rebuilds: a mint lays
        the one span it writes, a resume reads its line."""
        return cls(
            opening=opening,
            session=opened.session,
            cycle=opened.cycle,
            budget=budget,
            optimizer=opened.optimizer,
            closing=closing,
            mode=mode,
        )

    def job_spec(self) -> JobSpec:
        stores = self.session.store
        return JobSpec(
            job_id=self.opening.job_id,
            hop=self.session.hop,
            mode=self.mode,
            identity=stores.identity,
            budget=self.budget,
            jobs_dir=self.opening.job_registry.jobs_dir,
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
        """A person's run: the readout pointer names its cycle first."""
        session = self.session
        readout = CycleLayout(session.store.campaigns.cycle_dir(session.hop).absolute()).readout
        # The PATH, never a copy: parallel runs each own their cycle's readout, and one copy interleaves.
        with contextlib.suppress(OSError):
            write_text(user_data_root() / READOUT_POINTER, f"{readout}\n")
        return await self.run(readout_sink=readout_sink)

    async def run(
        self, *, readout_sink: Callable[[str], None] | None
    ) -> tuple[Session, CycleResult]:
        async with self.closing:
            return await run_held_job(
                self.opening,
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
    if (adopted := await asyncio.to_thread(job_registry.adopt, spec.job_id)) is None:
        return None
    opening = Opening.of(stores, job_registry, adopted)
    _tell_spawner()
    try:
        async with AsyncExitStack() as closing:
            closing.push_async_exit(opening)
            # Asked again here, under the claim: the admitting process's answer is a handoff old.
            # A refusal is the job's, never the cycle's stop: the cycle keeps the one it had.
            refuse_campaign(stores, (at := stores.campaigns.campaign_cycle(spec.hop)).campaign)
            opening.owns(spec.hop)
            opened = await closing.enter_async_context(ExistingCycle(at, Budget()).open(stores))
            _, result = await run_held_job(
                opening,
                opened.session,
                opened.cycle,
                mode=spec.mode,
                budget=spec.budget,
                optimizer=opened.optimizer,
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        return StopOutcome.PAUSED
    except Exception:  # noqa: BLE001 -- the opening's exit released the job with it
        return StopOutcome.FAILED
    return stop_reason_outcome(result.stop_reason)


async def run_held_job(
    opening: Opening,
    session: Session,
    cycle: CycleConfig,
    *,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[Session, CycleResult]:
    """THE run of a held launch at every door, an inner seat's too: START, the run, its END, the
    END being *opening*'s exit, which its door's stack holds. Answers the session the run ENDED on
    (`run_optimization`). Its START and its hold wait on gates, off the loop."""
    # Unstamped until its observers stand: nothing of this launch reached the cycle before, whose
    # own producer may still be running it.
    opening.owns(None)
    await asyncio.to_thread(opening.job_registry.move, opening.job_id, LaunchMove.START)
    observers = await asyncio.to_thread(enter_cycle, session, readout_sink=readout_sink)
    # Anything raised from here fired OUTSIDE the runner's try, an interrupt aside: it stamps.
    opening.running_on(session.hop)
    ended, result = await run_optimization(
        list(session.bank.samples),
        cycle,
        session=session,
        observers=observers,
        mode=mode,
        budget=budget,
        optimizer=optimizer,
    )
    opening.ended = result
    return ended, result


if __name__ == "__main__":
    # The detached run's entry alone lands: the landing completes the inner door's registry, which
    # imports `run_held_job` from this module, so the module itself never imports the landing.
    from promptpotter.application.maintenance.landing import land_at_entry

    setup_logging()
    _spec = JobSpec.model_validate_json(sys.stdin.buffer.read())
    land_at_entry(every_treatment=False)
    if (_outcome := asyncio.run(run_job(_spec))) is not None and (_code := _outcome.exit_code):
        sys.exit(_code)
