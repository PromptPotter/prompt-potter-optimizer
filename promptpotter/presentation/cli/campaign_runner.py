"""CLI entry-point facade — COMMANDS dispatch + ``main()``. Per-command bodies in ``commands/``.

Two write verbs: ``new [DATASET|FILE]`` mints a fresh campaign — from an authored
``datasets/<name>/`` *or* from a raw file (which it ingests → resolves the origin
check-in → commits as a tenant dataset → runs, the headless twin of the web
onboarding); ``resume`` continues the active session. Reads = open the on-disk
artifact tree (``dashboard.json`` / ``log.md`` / ``cycles/{cycle_id}/index.json``).
Ctrl+C stops; no mid-run pause.
"""

from __future__ import annotations

import asyncio
import json
import sys

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.presentation.cli.commands._shared import _DIVERGENCE_HINT, set_verbose
from promptpotter.presentation.cli.commands.ab import cmd_ab
from promptpotter.presentation.cli.commands.lifecycle import cmd_archive, cmd_delete, cmd_unarchive
from promptpotter.presentation.cli.commands.matrix import cmd_matrix
from promptpotter.presentation.cli.commands.new import cmd_new
from promptpotter.presentation.cli.commands.noise_floor import cmd_noise_floor
from promptpotter.presentation.cli.commands.reindex import cmd_reindex
from promptpotter.presentation.cli.commands.reset import cmd_reset
from promptpotter.presentation.cli.commands.resume_command import cmd_resume
from promptpotter.presentation.cli.commands.verify import cmd_verify
from promptpotter.presentation.cli.parsers import build_parser

__all__ = ["_DIVERGENCE_HINT", "main", "set_verbose"]


COMMANDS = {
    "new": cmd_new,
    "resume": cmd_resume,
    "ab": cmd_ab,
    "matrix": cmd_matrix,
    "reset": cmd_reset,
    "reindex": cmd_reindex,
    "verify": cmd_verify,
    "noise-floor": cmd_noise_floor,
    "archive": cmd_archive,
    "delete": cmd_delete,
    "unarchive": cmd_unarchive,
}


def main() -> None:
    from promptpotter.shared.errors import PotterError, RequestTooLargeError

    parser = build_parser()
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    # Bare invocation defaults to `resume`. Re-parse with the verb injected so
    # `resume`'s own defaults populate (--from, --fork-on-divergence, halt/spend, etc.).
    # First-run guard: if no active session exists, print a friendly landing
    # instead of letting resume fail with a confusing error.
    if args.command is None:
        from promptpotter.infrastructure.store.session_pointer import active_pointer_exists
        from promptpotter.presentation.cli.commands._shared import identity_from_args

        if not active_pointer_exists(identity_from_args(args).tenant_id):
            print(
                "Welcome to PromptPotter.\n\n"
                "Pick a verb to get started:\n"
                "  promptpotter new <dataset>   mint a fresh campaign on the named dataset\n"
                "  promptpotter new <file.csv>  ingest a raw file → resolve origin → mint + run\n"
                "  promptpotter resume          continue the active campaign\n"
                "  promptpotter verify          re-score a candidate on more samples\n"
                "  promptpotter ab              re-derive the active cycle's decisions under the current engine\n\n"
                "Run `promptpotter <verb> --help` for per-verb options.\n"
                "Docs: https://github.com/runfish5/prompt-potter-optimizer"
            )
            return
        args = parser.parse_args(["resume"])

    if args.command in ("new", "resume"):
        from promptpotter.config.env_bootstrap import ensure_api_key

        ensure_api_key()

    try:
        result = asyncio.run(COMMANDS[args.command](args))
    except (RequestTooLargeError, PotterError) as exc:
        # Operator-facing input errors (e.g. `resume --from N` past the last
        # completed round → BadRequestError) surface as a clean message, not a
        # traceback. PotterError is the one typed-error family the seams raise.
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
