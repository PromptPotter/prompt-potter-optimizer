from __future__ import annotations

import argparse

from promptpotter.application.cycle_listing import list_cycles
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores, resolve_campaign_hint

__all__ = ["cmd_cycles"]


async def cmd_cycles(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    listing = list_cycles(
        stores,
        inside=args.inside,
        campaign_id=resolve_campaign_hint(stores, args.campaign) if args.campaign else None,
        attached_only=args.attached,
    )
    cycles = listing.cycles
    lines = [
        f"{c.campaign_id}/{c.cycle_id}  {c.run_phase.value}"
        + (f" ({c.stop_reason.value})" if c.stop_reason else "")
        + f"  {c.status.label}"
        + f"  rounds={c.rounds_closed}"
        + ("  producer attached" if c.producer_attached else "")
        + (
            f"  paused by {c.pause.cause.value}"
            + (f" ({c.pause.detail})" if c.pause.detail else "")
            if c.pause
            else ""
        )
        + (
            f"  next: {c.run_admission.offers}"
            if c.run_admission.offers
            else f"  ({c.run_admission.refusal})"
        )
        + ("  <- active pointer" if c.cycle_id == listing.active_cycle_id else "")
        for c in cycles
    ]
    return CommandResult(
        data=listing.model_dump(mode="json"),
        human="\n".join(lines) or "No cycles match.",
    )
