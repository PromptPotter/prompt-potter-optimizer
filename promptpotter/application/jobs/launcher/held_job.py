"""THE run of a held launch, apart from `admission.py`, whose readers the run's observers import,
and from `run_job.py`, which imports the landing that completes the inner door's registry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.admission import RunStart
from promptpotter.application.jobs.registry import launch_interrupted
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.domain.campaign_config import CycleConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.job import LaunchMove
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import HeldBudget


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
    """THE run of a held launch at every door, an inner seat's too: START, the run, its END.
    Answers the session the run ENDED on (`run_optimization`). Its START, its hold and its END
    wait on gates, off the loop."""
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
