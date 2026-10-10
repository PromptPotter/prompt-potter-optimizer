from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_listing import active_pointer
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.parsers import identity_from_args
from promptpotter.shared.errors import ConflictError, NotFoundError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable

    from promptpotter.application.commands.payloads import CyclePayload

__all__ = [
    "cycle_scoped",
    "cycle_target",
    "open_stores",
    "refused_by",
    "resolve_campaign",
    "resolve_campaign_hint",
    "resolve_cycle",
    "resolve_target",
    "send_to_cycle",
]


def open_stores(args: argparse.Namespace) -> Stores:
    return build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)


def _campaign_matches(stores: Stores, needle: str) -> list[str]:
    """Exits on ambiguity: picking one of several is worse than not resolving at all."""
    candidates = stores.campaigns.match_campaign_ids(needle)
    if len(candidates) > 1:
        raise SystemExit(
            f"ERROR: {needle!r} matches {len(candidates)} campaigns: "
            f"{', '.join(candidates[:5])}{'…' if len(candidates) > 5 else ''}. "
            "Pass the full id."
        )
    return candidates


def resolve_campaign(stores: Stores, needle: str) -> str:
    matches = _campaign_matches(stores, needle)
    if not matches:
        raise SystemExit(f"ERROR: no campaign matches {needle!r}.")
    return matches[0]


def resolve_campaign_hint(stores: Stores, needle: str) -> str:
    """Returned UNCHANGED where nothing matches: the dispatcher already answers ``not_found``."""
    return next(iter(_campaign_matches(stores, needle)), needle)


def resolve_cycle(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """``hint=None`` names every cycle, so it resolves only where the campaign has ONE."""
    matches = stores.campaigns.match_cycle_ids(campaign_id, hint or "")
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(
            f"ERROR: no cycle in {campaign_id!r} matches {hint!r}."
            if hint
            else f"ERROR: campaign {campaign_id!r} has no cycles on disk."
        )
    listed = f"{', '.join(matches[:5])}{'…' if len(matches) > 5 else ''}"
    raise SystemExit(
        f"ERROR: {hint!r} matches {len(matches)} cycles in {campaign_id!r}: {listed}."
        if hint
        else f"ERROR: campaign {campaign_id!r} has {len(matches)} cycles; pass --cycle <prefix>. "
        f"Available: {listed}."
    )


def resolve_target(args: argparse.Namespace, store: Stores) -> tuple[str, str]:
    """The named pair, else the active pointer's — never half of each."""
    campaign_id: str = args.campaign
    cycle_id: str = args.cycle
    if not campaign_id:
        pointer = active_pointer(store)
        return pointer.campaign_id or "", cycle_id or pointer.cycle_id or ""
    campaign_id = resolve_campaign_hint(store, campaign_id)
    return campaign_id, cycle_id or resolve_cycle(store, campaign_id, None)


async def refused_by(awaitable: Awaitable[object], ids: dict[str, str]) -> CommandResult | None:
    """``not_found`` covers absent and not-yours alike: the terminal must not widen the 404 gate."""
    target = "/".join(ids.values())
    noun = "cycle" if "cycle_id" in ids else "campaign"
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
    return await send_to_cycle(
        store,
        payload_type.model_validate({"campaign_id": campaign_id, "cycle_id": cycle_id, **fields}),
    )
