from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.loop_commands import read_payload_file
from promptpotter.application.cycle_listing import resolve_campaign_id_hint
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CampaignPayload,
    CommandKind,
    CyclePayload,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import (
    cycle_target,
    open_stores,
    refused_by,
)
from promptpotter.presentation.cli.parsers import budget_from_args
from promptpotter.shared.errors import PayloadInvalidError

_ADDRESS = ("campaign_id", "cycle_id")


async def cmd_command(args: argparse.Namespace) -> CommandResult:
    """Every verb `verbs.py::command_verb` derives from a payload model runs here."""
    kind = next(kind for kind in CommandKind if kind.cli_verb == args.command)
    model = PAYLOAD_MODEL_FOR_KIND[kind]
    stores = open_stores(args)
    fields: dict[str, Any] = {
        name: read_payload_file(value) if isinstance(value, Path) else value
        for name, value in vars(args).items()
        if name in model.model_fields
    }
    if "budget" in model.model_fields:
        fields["budget"] = budget_from_args(args)
    if issubclass(model, CyclePayload):
        target = cycle_target(stores, args, kind.cli_verb or kind.value)
        if isinstance(target, CommandResult):
            return target
        fields["campaign_id"], fields["cycle_id"] = target
    elif issubclass(model, CampaignPayload):
        fields["campaign_id"] = resolve_campaign_id_hint(stores, args.campaign_id)
    payload = PayloadInvalidError.validating(model, fields)
    # The address, else the one field a workspace command names its subject by.
    named = [name for name in _ADDRESS if name in model.model_fields] or [next(iter(fields))]
    ids = {name: str(fields[name]) for name in named}
    sent = await refused_by(CommandDispatcher(stores).dispatch(CommandCall(payload)), ids)
    if isinstance(sent, CommandResult):
        return sent
    return CommandResult(
        data={
            **payload.model_dump(mode="json"),
            "command_id": sent.accepted.command_id,
            "ack": sent.ack,
        },
        human=" ".join(
            filter(None, (f"{'/'.join(ids.values())} -> {kind} {sent.ack}.", model.follow_up))
        ),
    )
