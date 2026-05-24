"""CLI entry-point facade — COMMANDS dispatch + ``main()``. Per-command bodies in ``commands/``.

Two write verbs: ``new [DATASET]`` mints a fresh campaign; ``resume`` continues
the active session. Reads = open the on-disk artifact tree
(``dashboard.json`` / ``log.md`` / ``cycles/{cycle_id}/index.json``). Ctrl+C stops; no mid-run pause.
"""

from __future__ import annotations

import asyncio
import json
import sys

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.presentation.cli.commands import (
    cmd_compare,
    cmd_new,
    cmd_reset,
    cmd_resume,
    cmd_sweep,
    cmd_verify,
)
from promptpotter.presentation.cli.commands._shared import _DIVERGENCE_HINT, set_verbose
from promptpotter.presentation.cli.parsers import build_parser

__all__ = ["_DIVERGENCE_HINT", "main", "set_verbose"]


COMMANDS = {
    "new": cmd_new,
    "resume": cmd_resume,
    "compare": cmd_compare,
    "sweep": cmd_sweep,
    "reset": cmd_reset,
    "verify": cmd_verify,
}


def main() -> None:
    from promptpotter.shared.errors import RequestTooLargeError

    parser = build_parser()
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    # Bare invocation defaults to `resume`. Re-parse with the verb injected so
    # `resume`'s own defaults populate (--from, --fork-on-divergence, halt/spend, etc.).
    if args.command is None:
        args = parser.parse_args(["resume"])

    try:
        result = asyncio.run(COMMANDS[args.command](args))
    except RequestTooLargeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)


if __name__ == "__main__":
    main()
