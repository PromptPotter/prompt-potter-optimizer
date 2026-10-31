from __future__ import annotations

import argparse
from pathlib import Path

from promptpotter.application.head_to_head_price import (
    ArmPrice,
    HeadToHeadPrice,
    price_head_to_head,
    read_head_to_head_plan,
)
from promptpotter.config.logging import setup_logging
from promptpotter.presentation.cli.commands.new import head_to_head_command
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.errors import PotterError


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def _arm_line(arm: ArmPrice) -> str:
    address = f"{arm.head_to_head_id}:{arm.arm_key}"
    refused = "" if arm.refused is None else f"  REFUSED: {arm.refused}"
    return (
        f"  {address:<34} {arm.optimizer:<8} {arm.rounds:>3} rounds"
        f"  {arm.search_cells:>5} + {arm.bench_cells:<4} calls  ${arm.usd:>7.2f}"
        f"  {_clock(arm.wall_clock_s):>7} ({_clock(arm.serial_s)} serial){refused}"
    )


def _human(price: HeadToHeadPrice) -> str:
    cell = price.cell
    timed = "untimed" if cell.seconds is None else f"{cell.seconds:.1f}s"
    head = (
        f"bank {price.dataset_hash[:12]} · {price.search_rows} search rows, {price.bench_rows} "
        f"held out · a cell ${cell.usd:.5f} and {timed}, over the archive's {cell.cells} newest "
        f"rows · {price.cells_in_flight} cells in flight"
    )
    optimizers = [
        f"  {name:<8} unpriced: no campaign here called it in a round, so its arms leave its "
        "own calls out"
        if held is None
        else f"  {name:<8} ${held.usd:.4f} and {held.seconds:.0f}s a round, over {held.rounds} "
        f"rounds of {held.campaigns} campaign(s)"
        for name, held in price.optimizers.items()
    ]
    mints = " && \\\n  ".join(head_to_head_command(head) for head in price.heads)
    return "\n".join(
        [
            head,
            "Optimizer calls, as measured here:",
            *optimizers,
            "Arms (calls = search + held-out cells; a point's arms are held equal on search):",
            *map(_arm_line, price.arms),
            f"Total ${price.usd:.2f} · {_clock(price.wall_clock_s)} "
            f"({_clock(price.serial_s)} serial), every arm minted after the last.",
            "Mint them, one head-to-head per budget point:",
            f"  {mints}",
        ]
    )


async def cmd_price_head_to_head(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    try:
        price = await price_head_to_head(stores, read_head_to_head_plan(Path(args.plan)))
    except PotterError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    return CommandResult(data=price.model_dump(mode="json"), human=_human(price))
