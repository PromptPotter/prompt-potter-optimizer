"""A held launch, handed to its run (`runner/entry.py::run_held`) here or to its own process,
whose ``JobSpec`` rides stdin (`domain/job.py::LaunchedAs`)."""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.spawning import adopted, start
from promptpotter.domain.campaign_config import CycleConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import LaunchedAs
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.store.io import open_append
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import Stores, build_stores

# The module a detached run's process runs (`runner/entry.py::run_job`): a name, never an import,
# since the run sits above its launch.
RUN_PROCESS = "promptpotter.application.runner.entry"

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.mint import OpenedCycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer


class JobSpec(LaunchedAs):
    """Admitted, held and its cycle on disk: the run its process opens (`run_job`)."""

    hop: CycleHop
    mode: RunMode
    budget: HeldBudget


def launched_as(stores: Stores, jobs_dir: Path, job_id: str) -> LaunchedAs:
    """*job_id* as *stores* would act it in a process of its own."""
    return LaunchedAs(
        job_id=job_id,
        identity=stores.identity,
        jobs_dir=jobs_dir,
        projects_root=stores.projects_root,
        shared_root=stores.shared_root,
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=stores.research_datasets_root,
    )


def stores_of(spec: LaunchedAs) -> Stores:
    """The stores *spec*'s admitting process addressed, rebuilt in the job's own."""
    return build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        install_datasets_root=spec.install_datasets_root,
        research_datasets_root=spec.research_datasets_root,
        shared_root=spec.shared_root,
    )


async def spawn_job(held: HeldLaunch) -> asyncio.Task[None]:
    """Returns only once the run's process holds the job (`run_job`): a spawner
    gone before that, its producer lock with it, would leave a job a reconciling read releases
    under its child. Answers the task that ends when the run's process exits."""
    stores, hop = held.opening.stores, held.hop
    log_path = CycleLayout(stores.campaigns.cycle_dir(hop)).job_log
    loop = asyncio.get_running_loop()
    # Until a process takes the job (its spec unbuilt among them), nothing else will ever hand its
    # slot back or stamp its cycle: the opening's exit does, on a raise.
    held.opening.owns(hop)
    spec = held.job_spec()
    with open_append(log_path) as log:
        child = start(RUN_PROCESS, spec.model_dump_json().encode(), log)
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


def _adoption(
    loop: asyncio.AbstractEventLoop, child: subprocess.Popen[bytes], job_id: str
) -> asyncio.Future[bool]:
    """`spawning.py::adopted`, read on a daemon thread for the reason `_await_exit` is."""
    said: asyncio.Future[bool] = loop.create_future()

    def settle(adopted: bool) -> None:
        if not said.done():
            said.set_result(adopted)

    def read() -> None:
        took = adopted(child)
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(settle, took)

    threading.Thread(target=read, name=f"job-{job_id}-adopt", daemon=True).start()
    return said


@dataclass(frozen=True, slots=True)
class HeldLaunch:
    """A launch past its admission (``launcher.admission``), its cycle on disk: what is left is the
    run-invocation, an entry point's ONE act. :meth:`detach` hands it to its own process, which
    opens the cycle's session (`run_job`); each act closes its opening's exit (*closing*)."""

    opening: Opening
    hop: CycleHop
    budget: HeldBudget
    closing: AsyncExitStack
    mode: RunMode  # `launch.py::Admitted.mode`'s: no door re-derives it

    def job_spec(self) -> JobSpec:
        opening = self.opening
        launched = launched_as(opening.stores, opening.job_registry.jobs_dir, opening.job_id)
        return JobSpec(**dict(launched), hop=self.hop, mode=self.mode, budget=self.budget)

    async def detach(self) -> asyncio.Task[None]:
        async with self.closing:
            return await spawn_job(self)


@dataclass(frozen=True, slots=True)
class HeldRun(HeldLaunch):
    """Held with its session bound to its cycle, here: by ``launch.py::hold``, or a head-to-head's
    mint for an arm."""

    session: Session
    cycle: CycleConfig
    optimizer: SelectedOptimizer

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
        held = (opened.hop, budget, closing, mode, opened.session, opened.cycle, opened.optimizer)
        return cls(opening, *held)
