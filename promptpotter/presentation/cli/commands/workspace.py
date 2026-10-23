from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.cycle_listing import (
    active_pointer,
    resolve_campaign_id_hint,
    resolve_cycle_id,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.shared.errors import ConflictError, NotFoundError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable

    from promptpotter.infrastructure.store.stores import Stores


def open_stores(args: argparse.Namespace) -> Stores:
    return local_stores(tenant=args.tenant)


def resolve_target(args: argparse.Namespace, store: Stores) -> tuple[str, str]:
    """A named campaign never takes the pointer's cycle; a cycle named alone is the active campaign's."""
    campaign_id: str = args.campaign
    cycle_id: str = args.cycle
    if not campaign_id:
        pointer = active_pointer(store)
        return pointer.campaign_id or "", cycle_id or pointer.cycle_id or ""
    campaign_id = resolve_campaign_id_hint(store, campaign_id)
    return campaign_id, cycle_id or resolve_cycle_id(store, campaign_id, None)


async def refused_by[T](awaitable: Awaitable[T], ids: dict[str, str]) -> CommandResult | T:
    """``not_found`` covers absent and not-yours alike: the terminal must not widen the 404 gate."""
    target = "/".join(ids.values())
    noun = list(ids)[-1].removesuffix("_id")
    try:
        return await awaitable
    except NotFoundError:
        return CommandResult(
            data={**ids, "status": "not_found"}, human=f"{noun} not found: {target}"
        )
    except ConflictError as exc:
        return CommandResult(data={**ids, "status": "conflict"}, human=str(exc))


def cycle_target(
    store: Stores, args: argparse.Namespace, verb: str
) -> tuple[str, str] | CommandResult:
    campaign_id, cycle_id = resolve_target(args, store)
    if campaign_id and cycle_id:
        return campaign_id, cycle_id
    return CommandResult(
        data={"status": "no_target"},
        human=f"No active cycle to {verb} — name one with --campaign/--cycle.",
    )
