from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic import ValidationError

from promptpotter.application.account import local_stores
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_listing import (
    active_pointer,
    resolve_campaign_id_hint,
    resolve_cycle_id,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.shared.errors import ConflictError, NotFoundError, PayloadInvalidError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable

    from promptpotter.domain.command_kinds import CyclePayload
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "cycle_scoped",
    "cycle_target",
    "open_stores",
    "refused_by",
    "resolve_target",
    "send_to_cycle",
]


def open_stores(args: argparse.Namespace) -> Stores:
    return local_stores(tenant=args.tenant)


def resolve_target(args: argparse.Namespace, store: Stores) -> tuple[str, str]:
    """The named pair, else the active pointer's — never half of each."""
    campaign_id: str = args.campaign
    cycle_id: str = args.cycle
    if not campaign_id:
        pointer = active_pointer(store)
        return pointer.campaign_id or "", cycle_id or pointer.cycle_id or ""
    campaign_id = resolve_campaign_id_hint(store, campaign_id)
    return campaign_id, cycle_id or resolve_cycle_id(store, campaign_id, None)


async def refused_by(awaitable: Awaitable[object], ids: dict[str, str]) -> CommandResult | None:
    """``not_found`` covers absent and not-yours alike: the terminal must not widen the 404 gate."""
    target = "/".join(ids.values())
    noun = list(ids)[-1].removesuffix("_id")
    try:
        await awaitable
    except NotFoundError:
        return CommandResult(
            data={**ids, "status": "not_found"}, human=f"{noun} not found: {target}"
        )
    except ConflictError as exc:
        return CommandResult(data={**ids, "status": "conflict"}, human=str(exc))
    return None


def cycle_target(
    store: Stores, args: argparse.Namespace, noun: str
) -> tuple[str, str] | CommandResult:
    campaign_id, cycle_id = resolve_target(args, store)
    if campaign_id and cycle_id:
        return campaign_id, cycle_id
    return CommandResult(
        data={"status": "no_target"},
        human=f"No active cycle to {noun} — name one with --campaign/--cycle.",
    )


async def send_to_cycle[P: CyclePayload](store: Stores, payload: P) -> CommandResult | P:
    refused = await refused_by(
        CommandDispatcher(store).dispatch_cycle_command(
            CommandCall(payload, uuid.uuid4().hex), expected_version=None
        ),
        {"campaign_id": payload.campaign_id, "cycle_id": payload.cycle_id},
    )
    return payload if refused is None else refused


async def cycle_scoped[P: CyclePayload](
    store: Stores,
    args: argparse.Namespace,
    payload_type: type[P],
    noun: str,
    **fields: object,
) -> CommandResult | P:
    target = cycle_target(store, args, noun)
    if isinstance(target, CommandResult):
        return target
    campaign_id, cycle_id = target
    try:
        payload = payload_type.model_validate(
            {"campaign_id": campaign_id, "cycle_id": cycle_id, **fields}
        )
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise PayloadInvalidError(reason) from exc
    return await send_to_cycle(store, payload)
