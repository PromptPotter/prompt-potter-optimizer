"""``cmd_sweep`` — sweep-toolkit subcommands (time-to / round1 / round2 / rank).

The verb surface is split across submodules: ``_common`` (variant
resolution + shared cycle setup/run plumbing), ``time_to`` (the
``time-to`` verb), ``panel`` (the ``round1`` / ``round2`` panel verbs +
their per-variant fork machinery), ``rank`` (the read-only ``rank`` verb).
"""

from __future__ import annotations

import argparse

from promptpotter.presentation.cli.commands._shared import CommandResult
from promptpotter.presentation.cli.commands.sweep.panel import (
    _cmd_sweep_round1,
    _cmd_sweep_round2,
)
from promptpotter.presentation.cli.commands.sweep.rank import _cmd_sweep_rank
from promptpotter.presentation.cli.commands.sweep.time_to import _cmd_sweep_time_to

__all__ = ["cmd_sweep"]


async def cmd_sweep(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``sweep <verb>`` — ``time-to`` / ``round1`` / ``round2`` /
    ``rank`` per ``docs/specs/roadmap.md``."""
    verb = args.sweep_verb
    if verb == "time-to":
        return await _cmd_sweep_time_to(args)
    if verb == "round1":
        return await _cmd_sweep_round1(args)
    if verb == "round2":
        return await _cmd_sweep_round2(args)
    if verb == "rank":
        return await _cmd_sweep_rank(args)
    raise SystemExit(f"sweep: unknown verb {verb!r}")
