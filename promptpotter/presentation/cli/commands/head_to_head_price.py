from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.domain.spend import usd_text
from promptpotter.presentation.cli.commands.new import head_to_head_command
from promptpotter.presentation.cli.commands.result import CommandResult

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.caller import Caller
    from promptpotter.application.head_to_head_price import ArmPrice, HeadToHeadPrice
    from promptpotter.application.read_params import HeadToHeadPriceParams


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def _arm_line(arm: ArmPrice) -> str:
    address = f"{arm.head_to_head_id}:{arm.arm_key}"
    refused = "" if arm.refused is None else f"  REFUSED: {arm.refused}"
    return (
        f"  {address:<34} {arm.optimizer:<8} {arm.rounds:>3} rounds"
        f"  {arm.search_cells:>5} + {arm.bench_cells:<4} calls  {usd_text(arm.usd):>8}"
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
        else f"  {name:<8} {usd_text(held.usd)} and {held.seconds:.0f}s a round, over {held.rounds} "
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
            f"Total {usd_text(price.usd)} · {_clock(price.wall_clock_s)} "
            f"({_clock(price.serial_s)} serial), every arm minted after the last.",
            "Mint them, one head-to-head per budget point:",
            f"  {mints}",
        ]
    )


def price_human(
    _params: HeadToHeadPriceParams,
    price: HeadToHeadPrice,
    _args: argparse.Namespace,
    _caller: Caller,
) -> CommandResult:
    return CommandResult(data=price.model_dump(mode="json"), human=_human(price))
