"""CLI entry-point facade — COMMANDS dispatch + ``main()``. Per-command bodies in ``commands/``.

Two write verbs: ``new [DATASET|FILE]`` mints a fresh campaign — from an authored
``datasets/<name>/`` *or* from a raw file (which it ingests → resolves the origin
check-in → commits as a tenant dataset → runs, the headless twin of the web
onboarding); ``resume`` continues the active session. Reads = open the on-disk
artifact tree (``dashboard.json`` / ``log.md`` / ``cycles/{cycle_id}/index.json``).
Ctrl+C is a resumable pause that exits 130, not a stop; ``pause`` asks a running cycle
to stop at its next checkpoint, through the same dispatcher the webapp's pause control
fires.
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
from promptpotter.presentation.cli.commands.lifecycle import (
    cmd_archive,
    cmd_delete,
    cmd_pause,
    cmd_unarchive,
)
from promptpotter.presentation.cli.commands.new import cmd_new
from promptpotter.presentation.cli.commands.noise_floor import cmd_noise_floor
from promptpotter.presentation.cli.commands.rank_optimizer_prompts import cmd_rank_optimizer_prompts
from promptpotter.presentation.cli.commands.reindex import cmd_reindex
from promptpotter.presentation.cli.commands.reset import cmd_reset
from promptpotter.presentation.cli.commands.restamp import cmd_restamp
from promptpotter.presentation.cli.commands.resume_command import cmd_resume
from promptpotter.presentation.cli.commands.seed_screen import cmd_seed_screen
from promptpotter.presentation.cli.commands.verify import cmd_verify
from promptpotter.presentation.cli.parsers import build_parser, parser_verbs

__all__ = ["_DIVERGENCE_HINT", "main", "set_verbose"]


COMMANDS = {
    "new": cmd_new,
    "resume": cmd_resume,
    "ab": cmd_ab,
    "reset": cmd_reset,
    "reindex": cmd_reindex,
    "restamp": cmd_restamp,
    "verify": cmd_verify,
    "noise-floor": cmd_noise_floor,
    "seed-screen": cmd_seed_screen,
    "rank-optimizer-prompts": cmd_rank_optimizer_prompts,
    "archive": cmd_archive,
    "delete": cmd_delete,
    "unarchive": cmd_unarchive,
    "pause": cmd_pause,
}

# A verb is one row here plus one `sub.add_parser` in `parsers.py`, and nothing made the two
# agree. Both halves fail QUIETLY: a parser row with no handler raises `KeyError` from the
# dispatch below — the operator's verb parsed, then crashed on a bare key — and a handler with
# no parser row is unreachable, reported by argparse as an unknown verb rather than a missing
# one. An import-time assert beside the table (`tests/CLAUDE.md`: structural invariants live in
# production, not tests) costs nothing to maintain and fails before `main()` can dispatch.
_declared = parser_verbs(build_parser())
assert _declared == COMMANDS.keys(), (
    "CLI verb drift between COMMANDS and parsers.py — "
    f"parser-only: {sorted(_declared - COMMANDS.keys())}, "
    f"handler-only: {sorted(COMMANDS.keys() - _declared)}"
)


def main() -> None:
    from promptpotter.shared.errors import PotterError, RequestTooLargeError

    parser = build_parser()
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    # Bare invocation defaults to `resume`. Re-parse with the verb appended to the ORIGINAL
    # argv (not alone) so `resume`'s own defaults populate (--from, --fork-on-divergence,
    # halt/spend, etc.) WITHOUT dropping the globals — `--tenant`/`--json` sit before the verb.
    # First-run guard: if no active session exists, print a friendly landing
    # instead of letting resume fail with a confusing error.
    if args.command is None:
        from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
        from promptpotter.infrastructure.store.layout import tenant_workspace
        from promptpotter.infrastructure.store.session_pointer import active_pointer_exists
        from promptpotter.presentation.cli.commands._shared import identity_from_args

        identity = identity_from_args(args)
        if not active_pointer_exists(tenant_workspace(DEFAULT_PROJECTS_ROOT, identity.tenant_id)):
            print(
                "Welcome to PromptPotter.\n\n"
                "Pick a verb to get started:\n"
                "  promptpotter new <dataset>   mint a fresh campaign on the named dataset\n"
                "  promptpotter new <file.csv>  ingest a raw file → resolve origin → mint + run\n"
                "  promptpotter resume          continue the active campaign\n"
                "  promptpotter verify          re-score a candidate on more samples\n"
                "  promptpotter ab              re-derive the active cycle's decisions under the current engine\n\n"
                "Run `promptpotter <verb> --help` for per-verb options.\n"
                "Docs: https://github.com/PromptPotter/prompt-potter-optimizer"
            )
            return
        args = parser.parse_args([*sys.argv[1:], "resume"])

    # Reconcile liveness before dispatch. The reaper had exactly two call sites, both bound
    # to the API server's lifespan — so on a CLI-only install nothing ever ran it, and a
    # cycle whose process died hard (SIGKILL, laptop lid, power) kept `status: active`
    # forever: the dock, the pickers and `resume` all read a corpse as a live unit. The
    # operator's own next command is the honest moment to notice, and it costs one glob.
    #
    # This is the whole answer, not half of it: no `atexit`/signal handler in this process
    # can stamp a cycle its own SIGKILL just ended, and a second mechanism that only covers
    # the graceful case would answer the same question twice. Ctrl+C already saves through
    # the loop's own checkpoint.
    from promptpotter.application.jobs.reaper import sweep_dead_cycles
    from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT

    sweep_dead_cycles(DEFAULT_PROJECTS_ROOT)

    if args.command in ("new", "resume"):
        from promptpotter.config.first_run import ensure_api_key

        ensure_api_key()

    try:
        result = asyncio.run(COMMANDS[args.command](args))
    except (RequestTooLargeError, PotterError) as exc:
        # Operator-facing input errors (e.g. `resume --from N` past the last
        # completed round → BadRequestError) surface as a clean message, not a
        # traceback. PotterError is the one typed-error family the seams raise.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Both kinds land here: `asyncio.Runner` cancels the main task on the first SIGINT and
        # raises KeyboardInterrupt on the second. The cycle already finalized itself as PAUSED,
        # so this writes nothing — it is the process's half of that pause.
        reason = f" — {exc}" if str(exc) else ""
        print(f"\nPaused{reason}. Completed work is saved; continue with:", file=sys.stderr)
        print("  python -m promptpotter resume", file=sys.stderr)
        sys.exit(130)
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)


if __name__ == "__main__":
    main()
