from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import signal
import sys
from collections.abc import Callable, Coroutine, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.views.render.phase import render_completion
from promptpotter.config.first_run import ensure_api_key
from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.command_kinds import (
    CancelPayload,
    CyclePayload,
    PauseCyclePayload,
)
from promptpotter.domain.connector import BACKEND_REACHABILITY_INFO
from promptpotter.domain.phases import stop_reason_outcome
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.domain.pipeline_params import PipelineParams
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.run_mode import RunMode
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


def prepare_launch() -> None:
    """Runs BEFORE the loop exists: a prompt or refusal here never meets a run's Ctrl+C handler."""
    ensure_api_key()


def pipeline_summary(session: Session, pipeline_params: PipelineParams) -> str:
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}"
    active = list(pipeline_params.steps or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


def log_startup_summary(
    session: Session,
    pipeline_params: PipelineParams,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info(
        "%s · backend %s · dataset %s", pipeline_summary(session, pipeline_params), backend_url, ds
    )


def backend_reach_line(backend_type: str, backend_url: str) -> str:
    """Said of a held run, whose admission probed the backend live; an ``in_process`` connector was
    never contacted, so no URL is claimed for it."""
    reached = BACKEND_REACHABILITY_INFO["live"].label
    if connectors.CONNECTORS.get(backend_type).execution == "in_process":
        return f"{reached} in-process ({backend_type}) — no wire"
    return f"{reached} at {backend_url}"


def inline_launch(args: argparse.Namespace) -> Inline:
    return Inline(no_wait=args.no_wait)


def held_run(launched: object) -> HeldRun:
    assert isinstance(launched, Launched), "a launching command answers its launch"
    assert launched.held is not None, "an inline launch is held, never detached"
    return launched.held


def ctrl_c_pauses(
    stores: Stores, pauses: Sequence[PauseCyclePayload]
) -> contextlib.AbstractContextManager[Callable[[CyclePayload], None]]:
    """The first Ctrl+C sends *pauses* as the browser's press does, and nothing more; the second force-quits."""

    async def _pause_each() -> None:
        for pause in pauses:
            with contextlib.suppress(PotterError):
                await CommandDispatcher(stores).dispatch(CommandCall(pause))

    return _ctrl_c(stores, _pause_each)


def ctrl_c_stops_pass(
    stores: Stores, call: CommandCall[Any]
) -> contextlib.AbstractContextManager[Callable[[CyclePayload], None]]:
    """A paid pass's Ctrl+C: the `cancel` of *call*'s command, as every other door's; the second
    press force-quits."""

    async def _stop() -> None:
        with contextlib.suppress(PotterError):
            cancel = CancelPayload(command_id=call.command_id)
            await CommandDispatcher(stores).dispatch(CommandCall(cancel))

    return _ctrl_c(stores, _stop)


@contextlib.contextmanager
def _ctrl_c(
    stores: Stores, stop: Callable[[], Coroutine[Any, Any, None]]
) -> Iterator[Callable[[CyclePayload], None]]:
    loop = asyncio.get_running_loop()
    asked: set[asyncio.Task[None]] = set()

    async def send(payload: CyclePayload) -> None:
        try:
            await CommandDispatcher(stores).dispatch(CommandCall(payload))
        except PotterError as exc:
            print(f"  {refusal_words(exc)}", flush=True)

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        task = loop.create_task(coro)
        asked.add(task)
        task.add_done_callback(asked.discard)

    def dispatch(payload: CyclePayload) -> None:
        _spawn(send(payload))

    interrupted = False

    def on_sigint(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        loop.call_soon_threadsafe(lambda: _spawn(stop()))

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        yield dispatch
    finally:
        signal.signal(signal.SIGINT, previous)


async def run_inline(held: HeldRun, *, mode: RunMode) -> CycleResult:
    hop = held.session.hop
    pause = PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="Ctrl+C")
    with ctrl_c_pauses(held.session.store, [pause]):
        result, _observers = await held.run_inline(
            mode=mode, readout_sink=functools.partial(print, flush=True)
        )
    return result


def cycle_result_command(session: Session, cycle_result: CycleResult) -> CommandResult:
    return CommandResult(
        data=cycle_result.model_dump(),
        human=render_completion(
            cycle_result,
            pipeline_schema=session.pipeline_schema,
            dataset_name=session.dataset_name,
            campaign_dir=session.store.campaigns.campaign_root_dir(session.campaign_id),
        ),
        exit_code=stop_reason_outcome(cycle_result.stop_reason).exit_code,
    )


def confirm_tty(prompt: str, *, default_no: bool = True, context: str = "") -> bool | None:
    """``None`` = stdin is no TTY: the caller picks its non-interactive default. *context* is
    said only to the person the prompt asks."""
    if not sys.stdin.isatty():
        return None
    if context:
        print(f"\n{context}\n")
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        raw = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not raw:
        return not default_no
    return raw in {"y", "yes"}
