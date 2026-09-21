"""Ask ONE model which reasoning rungs it honours, and print the `_MODEL_PROFILES` row it supports.

A fenced debug diagnostic like `noise-floor` — no config field, no L1 injection. It spends a
handful of cheap calls, billed on the workspace's ledger, and writes nothing else: the profile it
prints is committed by a human, because that table is evidence and evidence with no author is a
cache.
"""

from __future__ import annotations

import argparse

from promptpotter.application.diagnostics.probe_reasoning import (
    probe_reasoning,
    profile_suggestion,
)
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)


async def cmd_probe_reasoning(args: argparse.Namespace) -> CommandResult:

    setup_logging(style="full" if get_verbose() else "cli")
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)

    readings = await probe_reasoning(args.model, stores=stores, provider=args.provider)

    lines = [
        f"{args.model}  via {args.provider}",
        "",
        f"  {'rung':<10} {'reasoning':>10} {'output':>8}",
    ]
    for r in readings:
        if r.ok:
            # A provider that reports no usage breakdown leaves BOTH counts absent, so neither
            # may be formatted as a number — the answer arrived, the measurement did not.
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
