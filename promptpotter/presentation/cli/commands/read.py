"""Every read verb: its ``ReadKind`` row's params off argv, answered as every door answers it, then
rendered by the verb's own renderer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.caller import Caller
from promptpotter.application.commands.loop_commands import read_payload_file
from promptpotter.application.read_params import PARAMS_FOR_READ, ReadParams
from promptpotter.config.logging import setup_logging
from promptpotter.presentation.cli.commands.verbs import VERBS
from promptpotter.presentation.cli.commands.workspace import open_stores, resolve_target
from promptpotter.presentation.cli.parsers import Arg
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.named_rows import imported

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from promptpotter.presentation.cli.commands.result import CommandResult

    Render = Callable[[ReadParams, Any, argparse.Namespace, Caller], CommandResult]

_COMMANDS_PACKAGE = "promptpotter.presentation.cli.commands"
_CYCLE = frozenset({"campaign_id", "cycle_id"})
# The flags a per-cycle row's verb addresses its cycle by (`resolve_target`).
_ADDRESSING = frozenset({"campaign", "cycle"})


def _dest(arg: Arg) -> str:
    """Where argparse files the flag's value: its `dest`, else its first long flag's name."""
    if dest := arg.spec.get("dest"):
        return str(dest)
    longs = [flag for flag in arg.flags if flag.startswith("--")]
    return (longs or list(arg.flags))[0].lstrip("-").replace("-", "_")


def _unread_flags() -> list[str]:
    """Every read verb's flag that lands on no field of its row's model, no declared renderer
    arg and no cycle address: a value `cmd_read` would drop unsaid."""
    unread: list[str] = []
    for verb in VERBS:
        if verb.read is None:
            continue
        fields = PARAMS_FOR_READ[verb.read].model_fields.keys()
        taken = {*fields, *verb.render_args, *(_ADDRESSING if fields >= _CYCLE else ())}
        unread += [f"{verb.name} {_dest(a)}" for a in verb.args if _dest(a) not in taken]
    return unread


if unread := _unread_flags():
    raise TypeError(f"read verb flags no row field or renderer takes: {', '.join(unread)}")


async def cmd_read(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    verb = next(verb for verb in VERBS if verb.name == args.command)
    assert verb.read is not None and verb.render is not None
    model = PARAMS_FOR_READ[verb.read]
    stores = open_stores(args)
    # A flag left unset leaves the row's own default; a structured field is named by a YAML file.
    fields: dict[str, Any] = {
        name: read_payload_file(value) if isinstance(value, Path) else value
        for name, value in vars(args).items()
        if name in model.model_fields and value is not None
    }
    if model.model_fields.keys() >= _CYCLE:
        hop = resolve_target(args, stores)
        fields |= {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id}
    params = PayloadInvalidError.validating(model, fields)
    caller = Caller(stores)
    answer = await params.answered(caller)
    render = cast("Render", imported(_COMMANDS_PACKAGE, verb.render))
    return render(params, answer, args, caller)
