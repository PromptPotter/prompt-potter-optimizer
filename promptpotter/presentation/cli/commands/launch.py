from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, get_args

from promptpotter import connectors
from promptpotter.application.bench.resume_and_fork.replayers import replayers
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.reaper import sweep_dead_cycles
from promptpotter.application.views.render.phase import render_completion
from promptpotter.config.first_run import ensure_api_key
from promptpotter.config.paths import default_projects_root
from promptpotter.domain.command_kinds import (
    CyclePayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
)
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.phases import GateDecision, stop_reason_outcome
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.run_mode import RunMode
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


def prepare_launch() -> None:
    """Runs BEFORE the loop exists: a prompt or refusal here never meets a run's Ctrl+C handler."""
    # A CLI-only install has no periodic sweep: a launch is where a dead producer's cycle is recorded.
    sweep_dead_cycles(default_projects_root())
    ensure_api_key()


def pipeline_summary(session: Session, pipeline_params: dict[str, Any] | None) -> str:
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


def log_startup_summary(
    session: Session,
    pipeline_params: dict[str, Any] | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info(
        "%s · backend %s · dataset %s", pipeline_summary(session, pipeline_params), backend_url, ds
    )


def backend_reach_line(backend_type: str, backend_url: str) -> str:
    """An ``in_process`` connector was never contacted: claim no reachable URL for it."""
    if connectors.CONNECTORS.get(backend_type).execution == "in_process":
        return f"in-process ({backend_type}) — no wire"
    return f"reachable at {backend_url}"


def backend_unreachable_result(exc: BackendUnreachableError) -> CommandResult:
    """The connector's own ``detail`` IS the message: a cure hardcoded here fits one backend only."""
    return CommandResult(
        data={
            "error": "backend_unreachable",
            "backend_url": exc.backend_url,
            "backend_type": exc.backend_type,
            "detail": exc.detail,
        },
        human=(
            f"Backend '{exc.backend_type}' is not ready.\n\n{exc.detail}"
            if exc.detail
            else f"Backend '{exc.backend_type}' at {exc.backend_url} is not reachable."
        ),
    )


def inline_launch(args: argparse.Namespace) -> Inline:
    return Inline(no_wait=args.no_wait)


def held_run(launched: object) -> HeldRun:
    assert isinstance(launched, Launched), "a launching command answers its launch"
    assert launched.held is not None, "an inline launch is held, never detached"
    return launched.held


@contextlib.contextmanager
def ctrl_c_pauses(
    stores: Stores, pauses: Sequence[PauseCyclePayload]
) -> Iterator[Callable[[CyclePayload], None]]:
    """The first Ctrl+C sends *pauses* as the browser's press does, then cancels this task; the second force-quits."""
    loop, run = asyncio.get_running_loop(), asyncio.current_task()
    assert run is not None
    asked: set[asyncio.Task[None]] = set()

    async def send(payloads: Sequence[CyclePayload], *, then_cancel: bool) -> None:
        try:
            for payload in payloads:
                try:
                    await CommandDispatcher(stores).dispatch(CommandCall(payload))
                except PotterError as exc:
                    if not then_cancel:
                        print(f"  {exc}", flush=True)
        finally:
            if then_cancel:
                run.cancel()

    def dispatch(payload: CyclePayload) -> None:
        _send([payload], then_cancel=False)

    def _send(payloads: Sequence[CyclePayload], *, then_cancel: bool) -> None:
        task = loop.create_task(send(payloads, then_cancel=then_cancel))
        asked.add(task)
        task.add_done_callback(asked.discard)

    interrupted = False

    def on_sigint(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        loop.call_soon_threadsafe(functools.partial(_send, pauses, then_cancel=True))

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        yield dispatch
    finally:
        signal.signal(signal.SIGINT, previous)


@contextlib.contextmanager
def _terminal_inputs(session: Session) -> Iterator[None]:
    loop, hop = asyncio.get_running_loop(), session.hop
    pause = PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="Ctrl+C")
    with ctrl_c_pauses(session.store, [pause]) as dispatch:

        def read_decisions() -> None:
            spelled: dict[str, GateDecision] = {
                key: d for d in get_args(GateDecision) for key in (d, d[0])
            }
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                for line in sys.stdin:
                    decision = spelled.get(line.strip().lower())
                    if decision is not None:
                        loop.call_soon_threadsafe(
                            dispatch,
                            OriginGateDecisionPayload(
                                campaign_id=hop.campaign_id,
                                cycle_id=hop.cycle_id,
                                decision=decision,
                            ),
                        )

        if sys.stdin is not None and sys.stdin.isatty():
            threading.Thread(target=read_decisions, daemon=True, name="terminal-gate-input").start()
        yield


async def run_inline(held: HeldRun, *, mode: RunMode) -> CycleResult:
    with _terminal_inputs(held.session):
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


@functools.cache
def divergence_hint() -> str:
    hint = (
        f"Checked decisions: {', '.join(sorted(replayers()))}.\n"
        "(Every other recorded decision is archival, not divergence-gated.)\n\n"
        "Options:\n"
        "  • `python -m promptpotter new <dataset>` — start a fresh "
        "campaign (most common: you wanted a new run, not a resume).\n"
        "  • `python -m promptpotter resume --fork-on-divergence` — branch "
        "a sibling cycle here under the current scorer.\n"
        "  • Revert `campaign.json::scoring` — continue the original trajectory.\n"
        "  • `python -m promptpotter resume --no-check` — accept the divergence."
    )
    return hint


def confirm_tty(prompt: str, *, default_no: bool = True) -> bool | None:
    """``None`` = stdin is no TTY: the caller picks its non-interactive default."""
    if not sys.stdin.isatty():
        return None
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        raw = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not raw:
        return not default_no
    return raw in {"y", "yes"}
