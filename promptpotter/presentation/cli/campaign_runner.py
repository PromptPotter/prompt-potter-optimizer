"""CLI entry-point facade — COMMANDS dispatch table + ``main()``.

The per-command bodies live in ``commands/``:

* ``commands/optimize.py`` — ``cmd_optimize`` (the single write verb)
* ``commands/sweep.py`` — ``cmd_sweep`` (sweep toolkit verbs)
* ``commands/compare.py`` — ``cmd_compare``
* ``commands/init.py`` — fresh-init body invoked by ``cmd_optimize``
* ``commands/_shared.py`` — ``CommandResult``, ``set_verbose``,
  ``init_services_cli``, ``log_startup_summary``, and the shared
  cycle-preparation helpers.

``optimize`` is the single write verb. With ``--config`` (or
``--dataset-name``) it mints a fresh session+cycle and runs from
round 0; without, it resumes the active session. Reads happen by
opening the on-disk artifact tree (``sessions/{id}/``,
``campaigns/{cycle_id}/``) — ``dashboard.json`` for live state,
``log.md`` for the digest, ``index.json`` for the final summary
including ``stop_reason``. Stop with Ctrl+C — there is no mid-run
pause/resume.

``session.py`` carries ``SessionCtx``/``load_session``/``load_campaign_config``.
"""

from __future__ import annotations

import asyncio
import json
import sys

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.presentation.cli.commands import (
    cmd_compare,
    cmd_optimize,
    cmd_reset,
    cmd_sweep,
)
from promptpotter.presentation.cli.commands._shared import _DIVERGENCE_HINT, set_verbose
from promptpotter.presentation.cli.parsers import build_parser

__all__ = ["_DIVERGENCE_HINT", "main", "set_verbose"]


COMMANDS = {
    "optimize": cmd_optimize,
    "compare": cmd_compare,
    "sweep": cmd_sweep,
    "reset": cmd_reset,
}


def main() -> None:
    from promptpotter.shared.errors import RequestTooLargeError

    args = build_parser().parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

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
