from __future__ import annotations

import argparse

from promptpotter.application.cycle_addressing import active_pointer, resolve_campaign_id_hint
from promptpotter.application.hierarchy import list_hierarchy
from promptpotter.domain.cycle_paths import hierarchy_address
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse
from promptpotter.presentation.cli.commands.result import CommandResult, said_text
from promptpotter.presentation.cli.commands.workspace import open_stores


def _line(course: HierarchyCourse, *, active: bool) -> str:
    state = course.state
    return (
        course.address
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
    lines: list[str] = []
    for campaign in listing.entries:
        if not isinstance(campaign, HierarchyCampaign):
            continue
        for course in campaign.children:
            lines.append(_line(course, active=course.address == active))
            lines.append("    " + " · ".join(said_text(r) for r in course.figures))
            if (climb := course.climb) is not None:
                lines.append("    " + " · ".join(said_text(r) for r in climb.rates))
                lines.append(f"    {said_text(climb.note)}")
            lines.extend(f"    {said_text(r)}" for r in course.lines)
            if (said := course.said) is not None:
                lines.append(f"    {said.tone}: {said_text(said)}")
    # Each arm campaign's `<h2h>:<key>`: the head-to-head ids already taken.
    arms = [
        f"  {tag.value}  {campaign.address}"
        for campaign in listing.entries
        if (tag := campaign.tag("arm")) is not None
    ]
    if arms:
        lines += ["", "head-to-head arms:", *arms]
    return CommandResult(
        data=listing.model_dump(mode="json"),
        human="\n".join(lines) or "No cycles match.",
    )
