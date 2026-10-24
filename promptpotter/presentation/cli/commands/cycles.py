from __future__ import annotations

import argparse

from promptpotter.application.cycle_addressing import active_pointer, resolve_campaign_id_hint
from promptpotter.application.hierarchy import list_hierarchy
from promptpotter.domain.cycle_paths import hierarchy_address
from promptpotter.domain.hierarchy import HierarchyCourse
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


def _line(course: HierarchyCourse, *, active: bool) -> str:
    state = course.state
    rounds = next((s.value for s in course.stats if s.key == "rounds"), None)
    return (
        f"{course.address}  {state.run_phase.value}  {state.status.label}"
        + (f"  {rounds}" if rounds else "")
        + f"  {state.producer.label}"
        + (
            f"  paused by {state.pause.cause.value}"
            + (f": {state.pause.detail}" if state.pause.detail else "")
            if state.pause
            else ""
        )
        + (
            f"  next: {state.admission.offers}"
            if state.admission.offers
            else f"  ({state.admission.refusal})"
        )
        + ("  <- active pointer" if active else "")
    )


async def cmd_cycles(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    listing = list_hierarchy(
        stores,
        inside=args.inside,
        dataset=None,
        lifecycle="all",
        campaign_id=(
            resolve_campaign_id_hint(stores, args.campaign, inside=args.inside)
            if args.campaign
            else None
        ),
        attached_only=args.attached,
        depth=1,
    )
    pointer = active_pointer(stores, inside=args.inside)
    active = (
        hierarchy_address(args.inside, pointer.campaign_id, pointer.cycle_id)
        if pointer.campaign_id and pointer.cycle_id
        else None
    )
    lines = [
        _line(course, active=course.address == active)
        for campaign in listing.entries
        for course in campaign.children
        if isinstance(course, HierarchyCourse)
    ]
    return CommandResult(
        data=listing.model_dump(mode="json"),
        human="\n".join(lines) or "No cycles match.",
    )
