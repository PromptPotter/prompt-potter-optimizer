"""CLI entry-point facade — COMMANDS dispatch table + ``main()``.

The per-command bodies live in ``commands/``:

* ``commands/new.py`` — ``cmd_new`` (mint a fresh campaign)
* ``commands/resume.py`` — ``cmd_resume`` (continue active campaign)
* ``commands/sweep.py`` — ``cmd_sweep`` (sweep toolkit verbs)
* ``commands/compare.py`` — ``cmd_compare`` (PoBB cross-cycle comparison)
* ``commands/reset.py`` — ``cmd_reset`` (escape hatch)
* ``commands/_shared.py`` — ``CommandResult``, ``set_verbose``,
  ``init_services_cli``, ``log_startup_summary``, the shared
  cycle-preparation helpers, and the divergence hint text.

Two write verbs: ``new [DATASET]`` mints a fresh campaign (always);
``resume`` continues the active session. Reads happen by opening the
on-disk artifact tree (``sessions/{id}/``, ``campaigns/{campaign_id}/``) —
``dashboard.json`` for live state, ``log.md`` for the digest,
``cycles/{cycle_id}/index.json`` for the final summary including
``stop_reason``. Stop with Ctrl+C — there is no mid-run pause/resume.

``session.py`` carries ``SessionCtx``/``load_session``/``load_campaign_config``.
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

    # Bare invocation defaults to `resume`. Re-parse with the verb injected
    # so `resume`'s own defaults populate (--from, --fork-on-divergence,
    # halt/spend, etc.) — otherwise the namespace is missing those attrs
    # and cmd_resume's getattr-with-default catches it but loses CLI parity.
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
