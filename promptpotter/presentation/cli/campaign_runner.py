"""CLI entry-point facade — COMMANDS dispatch + ``main()``; bodies in ``commands/``. Ctrl+C is a resumable pause that exits
130, not a stop; ``pause`` asks a RUNNING cycle to stop through the dispatcher the webapp's control also fires."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.presentation.cli.parsers import build_parser, parser_verbs

__all__ = ["main"]


# Verb -> "module:attr", resolved on dispatch rather than bound at module scope. Importing every
# command body up front made each invocation pay for every other verb's dependency tree before
# argparse had looked at argv — `ab` alone dragged in numpy, `new` dragged in httpx — so `pause`
# cost the same second of imports as a full campaign launch.
COMMANDS = {
    "new": "promptpotter.presentation.cli.commands.new:cmd_new",
    "resume": "promptpotter.presentation.cli.commands.resume_command:cmd_resume",
    "ab": "promptpotter.presentation.cli.commands.ab:cmd_ab",
    "reset": "promptpotter.presentation.cli.commands.reset:cmd_reset",
    "reindex": "promptpotter.presentation.cli.commands.reindex:cmd_reindex",
    "restamp": "promptpotter.presentation.cli.commands.restamp:cmd_restamp",
    "compact-archive": "promptpotter.presentation.cli.commands.maintenance:cmd_compact_archive",
    "verify": "promptpotter.presentation.cli.commands.verify:cmd_verify",
    "noise-floor": "promptpotter.presentation.cli.commands.noise_floor:cmd_noise_floor",
    "seed-screen": "promptpotter.presentation.cli.commands.seed_screen:cmd_seed_screen",
    "evidence": "promptpotter.presentation.cli.commands.evidence:cmd_evidence",
    "archive": "promptpotter.presentation.cli.commands.lifecycle:cmd_archive",
    "delete": "promptpotter.presentation.cli.commands.lifecycle:cmd_delete",
    "unarchive": "promptpotter.presentation.cli.commands.lifecycle:cmd_unarchive",
    "pause": "promptpotter.presentation.cli.commands.lifecycle:cmd_pause",
    "rename": "promptpotter.presentation.cli.commands.lifecycle:cmd_rename",
    "set-budget": "promptpotter.presentation.cli.commands.lifecycle:cmd_set_budget",
    "skip-searchpoint": "promptpotter.presentation.cli.commands.lifecycle:cmd_skip_searchpoint",
    "step-cycle": "promptpotter.presentation.cli.commands.lifecycle:cmd_step_cycle",
    "delete-cycle": "promptpotter.presentation.cli.commands.lifecycle:cmd_delete_cycle",
    "cleanup-empty-cycles": (
        "promptpotter.presentation.cli.commands.lifecycle:cmd_cleanup_empty_cycles"
    ),
    "set-allowed-models": (
        "promptpotter.presentation.cli.commands.lifecycle:cmd_set_allowed_models"
    ),
    "replace-dataset": "promptpotter.presentation.cli.commands.lifecycle:cmd_replace_dataset",
}

# A verb is one row here plus one `sub.add_parser` in `parsers.py`, and nothing made the two
# agree. Both halves fail QUIETLY: a parser row with no handler raises `KeyError` from the
# dispatch below — the operator's verb parsed, then crashed on a bare key — and a handler with
# no parser row is unreachable, reported by argparse as an unknown verb rather than a missing
# one. An import-time assert beside the table (`tests/CLAUDE.md`: structural invariants live in
# production, not tests) costs nothing to maintain and fails before `main()` can dispatch.
_PARSER = build_parser()
_declared = parser_verbs(_PARSER)
assert _declared == COMMANDS.keys(), (
    "CLI verb drift between COMMANDS and parsers.py — "
    f"parser-only: {sorted(_declared - COMMANDS.keys())}, "
    f"handler-only: {sorted(COMMANDS.keys() - _declared)}"
)

# Which CLI verb reaches each server command kind. The assert below makes it TOTAL over the
# dispatched set, so a new kind cannot land browser-only in silence — its author names a verb or
# declares the gap with its reason. `CAP_FOR_KIND` and `PAYLOAD_MODEL_FOR_KIND` already bind the
# same vocabulary to authorization and payload shape; the terminal was the one consumer nothing
# checked, which is why five verbs had to be found one at a time before this existed.
CLI_VERB_FOR_KIND: dict[str, str | None] = {
    # Verbs that POST the kind itself — one command, one ledger record, either surface.
    "archive-campaign": "archive",
    "delete-campaign": "delete",
    "unarchive-campaign": "unarchive",
    "delete-cycle": "delete-cycle",
    "cleanup-empty-cycles": "cleanup-empty-cycles",
    "skip-searchpoint": "skip-searchpoint",
    "step-cycle": "step-cycle",
    "pause-cycle": "pause",
    "change-spend-budget": "set-budget",
    "set-campaign-label": "rename",
    "set-allowed-models": "set-allowed-models",
    "replace-dataset": "replace-dataset",
    "edit-draft-campaign": "new",
    "resolve-origin": "new",
    # Reached by the verb named, but through an IN-PROCESS path rather than the command — the
    # terminal changes the same state and writes no `CommandRecord` naming who asked. Each is its
    # own standing finding; they are named here so the next reader inherits them instead of
    # rediscovering them. `new`/`resume` mint and run inline (`--steer-model` is the fork),
    # `register-backend` is written by init wiring, `origin-gate-decision` is answered by the
    # in-run stdin prompt, and `compact-archive` calls the maintenance pass direct.
    "mint-campaign": "new",
    "start-checkin": "new",
    "register-backend": "new",
    "start-run": "resume",
    "fork-cycle": "resume",
    "origin-gate-decision": "resume",
    "compact-archive": "compact-archive",
    # Browser-only ON PURPOSE, and the absence IS the boundary: look-ahead spends the box's shared
    # provider rate bucket, so an assistant may recommend the control but never press it. Root
    # `CLAUDE.md` § Conventions; `docs/operations/access-model.md` § host-admin ↔ user.
    "set-sample-lookahead": None,
}
_named_verbs = {v for v in CLI_VERB_FOR_KIND.values() if v is not None}
assert set(CLI_VERB_FOR_KIND) == ALL_DISPATCHED_KINDS, (
    "command kind unclassified for the terminal — name the verb that reaches it, or declare the "
    f"gap: {sorted(ALL_DISPATCHED_KINDS.symmetric_difference(CLI_VERB_FOR_KIND))}"
)
assert _named_verbs <= COMMANDS.keys(), (
    f"CLI_VERB_FOR_KIND names verbs that do not exist: {sorted(_named_verbs - COMMANDS.keys())}"
)


def _validate_run_limits(args: argparse.Namespace) -> None:
    """Refuse a launch ceiling the wire would refuse, before anything is minted."""
    from pydantic import ValidationError

    from promptpotter.presentation.api.middleware.command_dispatcher import RunLimitsPayload

    try:
        RunLimitsPayload(
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
            spend_budget_usd=getattr(args, "spend_budget_usd", None),
            token_budget=getattr(args, "token_budget", None),
        )
    except ValidationError as exc:
        bad = ", ".join(f"--{str(e['loc'][0]).replace('_', '-')}: {e['msg']}" for e in exc.errors())
        raise SystemExit(f"invalid run limit — {bad}") from None


def main() -> None:
    from promptpotter.presentation.cli.commands._shared import set_verbose
    from promptpotter.shared.errors import PotterError, RequestTooLargeError

    parser = _PARSER
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    # Bare invocation defaults to `resume`. Re-parse with the verb appended to the ORIGINAL
    # argv (not alone) so `resume`'s own defaults populate (--from, --fork-on-divergence,
    # halt/spend, etc.) WITHOUT dropping the globals — `--tenant`/`--json` sit before the verb.
    # First-run guard: if no active session exists, print a friendly landing
    # instead of letting resume fail with a confusing error.
    if args.command is None:
        from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
        from promptpotter.config.settings import settings
        from promptpotter.infrastructure.store.layout import tenant_workspace
        from promptpotter.infrastructure.store.session_pointer import active_pointer_exists
        from promptpotter.presentation.cli.commands._shared import identity_from_args

        identity = identity_from_args(args)
        if not active_pointer_exists(tenant_workspace(DEFAULT_PROJECTS_ROOT, identity.tenant_id)):
            print(
                f"Welcome to {settings.BRAND_SHORT_NAME}.\n\n"
                "Pick a verb to get started:\n"
                "  promptpotter new <dataset>   mint a fresh campaign on the named dataset\n"
                "  promptpotter new <file.csv>  ingest a raw file → resolve origin → mint + run\n"
                "  promptpotter resume          continue the active campaign\n"
                "  promptpotter verify          re-score a candidate on more samples\n"
                "  promptpotter ab              re-derive the active cycle's decisions under the current engine\n\n"
                "Run `promptpotter <verb> --help` for per-verb options.\n"
                f"Docs: {settings.BRAND_DOCS_URL}"
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

        # The launch ceilings through the SAME bounds the wire enforces. argparse types them and
        # bounds neither, so `--halt-at 1.5` was accepted and then never fired.
        _validate_run_limits(args)
        ensure_api_key()

    module_path, _, attr = COMMANDS[args.command].partition(":")
    handler = getattr(importlib.import_module(module_path), attr)

    try:
        result = asyncio.run(handler(args))
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
