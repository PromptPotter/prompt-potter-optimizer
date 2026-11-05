from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.cycle_addressing import active_pointer
from promptpotter.domain.cycle_paths import Subject
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse
from promptpotter.presentation.cli.commands.result import CommandResult, said_text

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.caller import Caller
    from promptpotter.application.read_params import HierarchyParams
    from promptpotter.domain.hierarchy import HierarchyResponse


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


def cycles_human(
    params: HierarchyParams,
    listing: HierarchyResponse,
    _args: argparse.Namespace,
    caller: Caller,
) -> CommandResult:
    inside = params.inside
    hop = active_pointer(caller.stores, inside=inside)
    active = None if hop is None else Subject.of((*inside, hop)).address
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
