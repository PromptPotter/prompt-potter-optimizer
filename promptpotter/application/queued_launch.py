"""A queued launch's own process, started by the slot that freed for it (`JobRegistry.promote`), its
asker gone or not: its ask (`command_events.py::QueuedLaunch`) rides stdin, and its command's act
runs here on the claimed job, past the money admission, its run held in this process."""

from __future__ import annotations

import asyncio
import sys

from promptpotter.application.commands.dispatcher import CommandDispatcher
from promptpotter.application.commands.launching import launched
from promptpotter.application.jobs.launcher.held import stores_of
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.spawning import tell_spawner
from promptpotter.application.runner.entry import run_held
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_events import QUEUED_LAUNCH, QueuedLaunch
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.shared.errors import NotFoundError


def adopt(ask: QueuedLaunch) -> CommandDispatcher | None:
    """This process's dispatcher on *ask*'s job, taken over; ``None`` where it was released first."""
    job_registry = JobRegistry.attach(ask.jobs_dir)
    if (job := job_registry.adopt(ask.job_id)) is None:
        return None
    return CommandDispatcher(stores_of(ask), job_registry, inline=Inline(claimed=job))


async def act_queued(ask: QueuedLaunch, dispatcher: CommandDispatcher) -> StopOutcome:
    """Its command's act and its run, on the adopted job. A raise before its opening holds the job
    releases it with that raise, which files the refusal."""
    try:
        book = await asyncio.to_thread(dispatcher.stores.commands.book)
        if (record := book.filing(ask.command_id)) is None:
            raise NotFoundError(f"the command {ask.command_id} this launch queued for is gone")
        acted = await dispatcher.act_claimed(record, ask.door)
        launch = await launched(acted, record.payload, dispatcher, ask.door)
        assert isinstance(launch, Launched) and launch.held is not None, "held here, alone"
        _, result = await run_held(await launch.held, readout_sink=None)
    except BaseException as exc:
        await asyncio.to_thread(dispatcher.job_registry.release, ask.job_id, exc)
        if isinstance(exc, KeyboardInterrupt | asyncio.CancelledError):
            return StopOutcome.PAUSED
        if isinstance(exc, Exception):
            return StopOutcome.FAILED
        raise
    return stop_reason_outcome(result.stop_reason)


async def _main(ask: QueuedLaunch) -> StopOutcome | None:
    if (dispatcher := await asyncio.to_thread(adopt, ask)) is None:
        return None
    tell_spawner()
    return await act_queued(ask, dispatcher)


if __name__ == "__main__":
    from promptpotter.application.maintenance.landing import land_at_entry

    setup_logging()
    _ask = QUEUED_LAUNCH.validate_json(sys.stdin.buffer.read())
    land_at_entry(every_treatment=False)
    if (_outcome := asyncio.run(_main(_ask))) is not None and (_code := _outcome.exit_code):
        sys.exit(_code)
