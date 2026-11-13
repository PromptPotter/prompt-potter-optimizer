"""No verb's module is imported here: ``main()`` imports the one it dispatches, so ``--help`` loads no
verb and no use case."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import sys
from typing import TYPE_CHECKING, Any, cast

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.config.settings import settings
from promptpotter.domain.phases import StopOutcome
from promptpotter.domain.refusal import Refusal
from promptpotter.presentation.cli.commands.verbs import VERBS
from promptpotter.presentation.cli.parsers import build_parser
from promptpotter.shared.errors import PotterError
from promptpotter.shared.named_rows import imported

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Coroutine

    from promptpotter.presentation.cli.commands.result import CommandResult


_COMMANDS_PACKAGE = "promptpotter.presentation.cli.commands"

COMMANDS: dict[str, str] = {verb.name: verb.handler for verb in VERBS}
_COMPLETING = frozenset(verb.name for verb in VERBS if verb.needs_registries)
_PARSER = build_parser(VERBS)


def _handler(verb: str) -> Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]:
    return cast(
        "Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]",
        imported(_COMMANDS_PACKAGE, COMMANDS[verb]),
    )


def main() -> None:

    parser = _PARSER
    args = parser.parse_args()

    if args.command is None:
        # Named as a string like the handlers: `--help` loads no use case.
        listing = importlib.import_module("promptpotter.application.cycle_addressing")
        if not listing.holds_active_pointer(tenant=args.tenant):
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

    runner = asyncio.Runner()
    try:
        # The landing's refusal is this one catch's to say, as a verb's is.
        if args.command in _COMPLETING:
            # Named as a string like the handlers: `--help` loads no use case.
            landing = importlib.import_module("promptpotter.application.maintenance.landing")
            landing.land_at_entry(every_treatment=False)
        # A launch verb's handler runs its preamble and THEN answers the coroutine: a preamble's
        # refusal is this one catch's to say, as the run's is.
        result = runner.run(_handler(args.command)(args))
    except PotterError as exc:
        runner.close()
        refused = Refusal.of(exc)
        if args.json_output:
            print(json.dumps(refused.model_dump(mode="json")))
        # What this terminal alone can do about it rides the refusal as a note, said after it.
        said = [f"Error: {refused.message}", *getattr(exc, "__notes__", ())]
        print("\n\n".join(said), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # The cycle already finalized itself as PAUSED; closing a loop a second SIGINT stopped mid-unwind raises.
        with contextlib.suppress(RuntimeError, KeyboardInterrupt):
            runner.close()
        reason = f" — {exc}" if str(exc) else ""
        print(f"\nPaused{reason}. Completed work is saved.", file=sys.stderr)
        lines = cast(
            "Callable[[str], list[str]]",
            imported("promptpotter.application.commands", "dispatcher:interrupted_lines"),
        )
        print("\n".join(lines(args.command)), file=sys.stderr)
        sys.exit(StopOutcome.PAUSED.exit_code)
    runner.close()
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    elif result.human:
        # An empty one prints nothing: a verb whose stdout was a wire (`mcp`) leaves it clean.
        print(result.human)
    if result.exit_code:
        sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
