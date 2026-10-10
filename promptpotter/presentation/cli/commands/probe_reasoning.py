from __future__ import annotations

import argparse

from promptpotter.application.diagnostics.probe_reasoning import (
    probe_reasoning,
    profile_suggestion,
)
from promptpotter.config.logging import setup_logging
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


async def cmd_probe_reasoning(args: argparse.Namespace) -> CommandResult:

    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)

    readings = await probe_reasoning(args.model, stores=stores, provider=args.provider)

    lines = [
        f"{args.model}  via {args.provider}",
        "",
        f"  {'rung':<10} {'reasoning':>10} {'output':>8}",
    ]
    for r in readings:
        if r.ok:
            # No usage breakdown leaves both counts absent: the answer arrived, the measurement did not.
            thought = "-" if r.reasoning is None else str(r.reasoning)
            emitted = "-" if r.output is None else str(r.output)
            lines.append(f"  {r.rung:<10} {thought:>10} {emitted:>8}")
        else:
            lines.append(f"  {r.rung:<10} {'REFUSED':>10} {'-':>8}   {r.refused}")
    lines += [
        "",
        "Suggested `registry._MODEL_PROFILES` row — read the numbers before pasting:",
        "",
        profile_suggestion(args.model, readings),
    ]
    return CommandResult(human="\n".join(lines))
