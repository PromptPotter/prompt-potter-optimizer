from __future__ import annotations

import argparse

from promptpotter.application.caller import Caller
from promptpotter.application.cycle_addressing import latest_launch
from promptpotter.application.read_params import HierarchyParams
from promptpotter.domain.cycle_paths import Subject
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse, HierarchyResponse
from promptpotter.presentation.cli.commands.result import CommandResult, said_text


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
        + ("  <- latest launch" if active else "")
    )


def cycles_human(
    params: HierarchyParams,
    listing: HierarchyResponse,
    _args: argparse.Namespace,
    caller: Caller,
) -> CommandResult:
    inside = params.inside
    hop = latest_launch(caller.stores, inside=inside)
    active = None if hop is None else Subject.of((*inside, hop)).address
    lines: list[str] = []
    for campaign in listing.entries:
        if not isinstance(campaign, HierarchyCampaign):
            continue
        for course in campaign.children:
            lines.append(_line(course, active=course.address == active))
            if (climb := course.climb) is not None:
                lines.append(f"    {said_text(climb)}")
            lines.append("    " + " · ".join(said_text(r) for r in course.figures))
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
