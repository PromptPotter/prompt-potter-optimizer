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
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.phases import StopOutcome
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.session_pointer import active_pointer_exists
from promptpotter.presentation.cli.commands.verbs import VERBS
from promptpotter.presentation.cli.parsers import build_parser, identity_from_args
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

COMMANDS: dict[str, str] = {verb.name: verb.handler for verb in VERBS}
_PARSER = build_parser(VERBS)


def _handler(verb: str) -> Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]:
    module, _, name = COMMANDS[verb].partition(":")
    handler: Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]] = getattr(
        importlib.import_module(f"{_COMMANDS_PACKAGE}.{module}"), name
    )
    return handler


# TOTAL by construction: the verb is a column of the kind's own row, so none lands browser-only in silence.
CLI_VERB_FOR_KIND: dict[CommandKind, str | None] = {kind: kind.cli_verb for kind in CommandKind}
_named_verbs = {v for v in CLI_VERB_FOR_KIND.values() if v is not None}
if not _named_verbs <= COMMANDS.keys():
    raise RuntimeError(
        f"CommandKind names verbs that do not exist: {sorted(_named_verbs - COMMANDS.keys())}"
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

    # Named as a string like the handlers: `--help` loads no use case.
    wiring = importlib.import_module("promptpotter.application.initialization.wiring")
    wiring.complete_registries(every_treatment=False)
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
