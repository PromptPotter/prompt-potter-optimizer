"""No verb's module is imported here: ``main()`` imports the one it dispatches, so ``--help`` loads no verb."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import sys
from typing import TYPE_CHECKING, Any

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import settings
from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.domain.phases import StopOutcome
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.session_pointer import active_pointer_exists
from promptpotter.presentation.cli.parsers import build_parser, identity_from_args, parser_verbs
from promptpotter.shared.errors import (
    PotterError,
    RequestTooLargeError,
    SendRefusedError,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Coroutine

    from promptpotter.presentation.cli.commands.result import CommandResult

__all__ = ["main"]


_COMMANDS_PACKAGE = "promptpotter.presentation.cli.commands"

COMMANDS: dict[str, str] = {
    "new": "new:cmd_new",
    "resume": "resume_command:cmd_resume",
    "ab": "ab:cmd_ab",
    "reset": "reset:cmd_reset",
    "reindex": "reindex:cmd_reindex",
    "restamp": "restamp:cmd_restamp",
    "compact-archive": "maintenance:cmd_compact_archive",
    "verify": "verify:cmd_verify",
    "noise-floor": "noise_floor:cmd_noise_floor",
    "seed-screen": "seed_screen:cmd_seed_screen",
    "decision-bank": "decision_bank:cmd_decision_bank",
    "evidence": "evidence:cmd_evidence",
    "cycles": "cycles:cmd_cycles",
    "machine-status": "machine_status:cmd_machine_status",
    "probe-reasoning": "probe_reasoning:cmd_probe_reasoning",
    "archive": "lifecycle:cmd_archive",
    "delete": "lifecycle:cmd_delete",
    "unarchive": "lifecycle:cmd_unarchive",
    "pause": "lifecycle:cmd_pause",
    "rename": "lifecycle:cmd_rename",
    "set-limits": "lifecycle:cmd_set_limits",
    "skip-searchpoint": "lifecycle:cmd_cycle_verb",
    "origin-gate": "lifecycle:cmd_origin_gate",
    "step-cycle": "lifecycle:cmd_step_cycle",
    "bench": "bench:cmd_bench",
    "delete-cycle": "lifecycle:cmd_cycle_verb",
    "cleanup-empty-cycles": "lifecycle:cmd_cycle_verb",
    "replace-dataset": "lifecycle:cmd_replace_dataset",
    "cancel-queued": "lifecycle:cmd_cancel_queued",
    "set-concurrent-cycles": "lifecycle:cmd_set_concurrent_cycles",
}


def _handler(verb: str) -> Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]:
    module, _, name = COMMANDS[verb].partition(":")
    handler: Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]] = getattr(
        importlib.import_module(f"{_COMMANDS_PACKAGE}.{module}"), name
    )
    return handler


# Each half fails QUIETLY alone: a handler-less parser row is a bare `KeyError`, a parser-less handler an unknown verb.
_PARSER = build_parser()
_declared = parser_verbs(_PARSER)
if _declared != COMMANDS.keys():
    raise RuntimeError(
        "CLI verb drift between COMMANDS and parsers.py — "
        f"parser-only: {sorted(_declared - COMMANDS.keys())}, "
        f"handler-only: {sorted(COMMANDS.keys() - _declared)}"
    )

# TOTAL over the dispatched set: a new kind names its verb or declares the gap, so none lands browser-only in silence.
CLI_VERB_FOR_KIND: dict[str, str | None] = {
    "archive-campaign": "archive",
    "delete-campaign": "delete",
    "unarchive-campaign": "unarchive",
    "delete-cycle": "delete-cycle",
    "cleanup-empty-cycles": "cleanup-empty-cycles",
    "skip-searchpoint": "skip-searchpoint",
    "origin-gate-decision": "origin-gate",
    "step-cycle": "step-cycle",
    "grade-bench": "bench",
    "pause-cycle": "pause",
    "change-run-limits": "set-limits",
    "set-campaign-label": "rename",
    "replace-dataset": "replace-dataset",
    "edit-draft-campaign": "new",
    "resolve-origin": "new",
    "start-checkin": "new",
    "cancel-queued-run": "cancel-queued",
    "set-concurrent-cycles": "set-concurrent-cycles",
    "verify-candidate": "verify",
    "compact-archive": "compact-archive",
    "fork-cycle": "resume",
    "mint-campaign": "new",
    "start-run": "resume",
    # Reached by the verb named, but written by init wiring rather than through the command.
    "register-backend": "new",
    # Browser-only ON PURPOSE: the absence IS the boundary (root `CLAUDE.md` § Conventions).
    "set-sample-lookahead": None,
}
_named_verbs = {v for v in CLI_VERB_FOR_KIND.values() if v is not None}
if set(CLI_VERB_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "command kind unclassified for the terminal — name the verb that reaches it, or declare "
        f"the gap: {sorted(ALL_DISPATCHED_KINDS.symmetric_difference(CLI_VERB_FOR_KIND))}"
    )
if not _named_verbs <= COMMANDS.keys():
    raise RuntimeError(
        f"CLI_VERB_FOR_KIND names verbs that do not exist: {sorted(_named_verbs - COMMANDS.keys())}"
    )


def main() -> None:

    parser = _PARSER
    args = parser.parse_args()

    if args.command is None:
        identity = identity_from_args(args)
        if not active_pointer_exists(tenant_workspace(DEFAULT_PROJECTS_ROOT, identity.tenant_id)):
            print(
                f"Welcome to {settings.BRAND_SHORT_NAME}.\n\n"
                "Pick a verb to get started:\n"
                "  promptpotter new <dataset>   mint a fresh campaign on the named dataset\n"
                "  promptpotter new <file.csv>  ingest a raw file → resolve origin → mint + run\n"
                "  promptpotter resume          continue the active campaign\n"
                "  promptpotter verify          re-score a candidate on more samples\n"
                "  promptpotter bench           grade the origin and the selection on held-out rows\n"
                "  promptpotter ab              re-derive the active cycle's decisions under the current engine\n\n"
                "Run `promptpotter <verb> --help` for per-verb options.\n"
                f"Docs: {settings.BRAND_DOCS_URL}"
            )
            return
        # Appended to the ORIGINAL argv, so `resume`'s defaults populate without dropping the globals before the verb.
        args = parser.parse_args([*sys.argv[1:], "resume"])

    # A launch verb's handler runs its preamble and THEN answers the coroutine: all of it happens here, outside the runner.
    run = _handler(args.command)(args)

    runner = asyncio.Runner()
    try:
        result = runner.run(run)
    except (RequestTooLargeError, SendRefusedError, PotterError) as exc:
        runner.close()
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # The cycle already finalized itself as PAUSED; closing a loop a second SIGINT stopped mid-unwind raises.
        with contextlib.suppress(RuntimeError, KeyboardInterrupt):
            runner.close()
        reason = f" — {exc}" if str(exc) else ""
        print(f"\nPaused{reason}. Completed work is saved; continue with:", file=sys.stderr)
        print("  python -m promptpotter resume", file=sys.stderr)
        sys.exit(StopOutcome.PAUSED.exit_code)
    runner.close()
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)
    if result.outcome is not None and (code := result.outcome.exit_code):
        sys.exit(code)


if __name__ == "__main__":
    main()
