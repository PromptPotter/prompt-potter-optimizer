from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from promptpotter.application.account import local_stores
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.loop_commands import read_payload_file
from promptpotter.application.cycle_addressing import cycle_target
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandKind,
    CyclePayload,
    Door,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.parsers import budget_from_args
from promptpotter.shared.errors import PayloadInvalidError

_ADDRESS = ("campaign_id", "cycle_id")


async def cmd_command(args: argparse.Namespace) -> CommandResult:
    """Every verb `verbs.py::command_verb` derives from a payload model runs here."""
    kind = next(kind for kind in CommandKind if kind.cli_verb == args.command)
    model = PAYLOAD_MODEL_FOR_KIND[kind]
    stores = local_stores(tenant=args.tenant)
    fields: dict[str, Any] = {
        name: read_payload_file(value) if isinstance(value, Path) else value
        for name, value in vars(args).items()
        if name in model.model_fields
    }
    if "budget" in model.model_fields:
        fields["budget"] = budget_from_args(args)
    if issubclass(model, CyclePayload):
        # Named prefixes are the dispatcher's to resolve; the pointer and a sole cycle are not.
        hop = (
            CycleHop(campaign_id=args.campaign, cycle_id=args.cycle)
            if args.campaign and args.cycle
            else cycle_target(stores, args.campaign, args.cycle)
        )
        fields["campaign_id"], fields["cycle_id"] = hop.campaign_id, hop.cycle_id
    payload = PayloadInvalidError.validating(model, fields)
    sent = await CommandDispatcher(stores).dispatch(CommandCall(payload, door=Door.TERMINAL))
    # The address acted on, else the one field a workspace command names its subject by.
    acted = sent.payload.model_dump(mode="json")
    named = [name for name in _ADDRESS if name in model.model_fields] or [next(iter(fields))]
    ids = {name: str(acted[name]) for name in named}
    # A `{field}` in the follow-up names a field of what the kind answers, as the route serves it.
    answered = sent.result.model_dump(mode="json") if isinstance(sent.result, BaseModel) else {}
    follow_up = model.follow_up and model.follow_up.format_map(answered)
    return CommandResult(
        data={
            **acted,
            "command_id": sent.accepted.command_id,
            "ack": sent.ack,
        },
        human=" ".join(
            filter(None, (f"{'/'.join(ids.values())} -> {kind} {sent.ack}.", follow_up))
        ),
    )
