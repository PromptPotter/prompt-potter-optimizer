"""A planned head-to-head priced at $0: the archive's measured cell price and each optimizer's
measured rounds, read through the mint each arm IS, so the price and the mint cannot part."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from pydantic import Field

from promptpotter.application.initialization.loop_start import (
    cycle_partition,
    measured_cell_price,
)
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.mint_and_start import FreshMint
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.preflight import (
    check_cap_funds_round,
    check_search_split_holds_round,
)
from promptpotter.application.runner.campaign_result import line_launches
from promptpotter.domain.command_kinds import MintCampaignPayload
from promptpotter.domain.head_to_head_plan import HeadToHeadPlan
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.spend import Budget, CellPrice
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_spend_by_round,
)
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.read_model import LedgerSpan
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.infrastructure.store.stores import Stores


class OptimizerRoundPrice(FrozenModel):
    """What one round of an optimizer's own calls cost, over its newest campaigns here, any
    treatment of it."""

    usd: float = Field(description="Mean optimizer USD incurred per round that called it.")
    seconds: float = Field(
        description="Mean optimizer call seconds per such round; calls sent together each count."
    )
    rounds: int = Field(description="Rounds the means are over.")
    campaigns: int = Field(description="Campaigns those rounds ran in.")


class ArmPrice(StrictModel):
    mint: MintCampaignPayload = Field(
        description="The mint this arm IS: dispatched as `mint-campaign`, it mints the arm priced."
    )
    optimizer: str
    rounds: int = Field(
        description="Rounds closed after the origin: the point's `rounds`, or what its `usd` "
        "buys first."
    )
    search_cells: int = Field(
        description="Cells the search measures, the origin's included: the calls a budget-matched "
        "head-to-head holds equal, so arms of one point are compared on it."
    )
    bench_cells: int = Field(description="The origin's and the selection's held-out passes.")
    usd: float = Field(
        description="Search and bench at the measured cell price, plus the optimizer's measured "
        "round price for every round it opens."
    )
    wall_clock_s: float | None = Field(
        description="At `cells_in_flight`; null where no priced cell carried a timing."
    )
    serial_s: float | None = Field(description="One cell at a time, the look-ahead's default.")
    refused: str | None = Field(
        description="What this arm's mint refuses (`spend_cap_below_round`): its budget closes no "
        "round."
    )


class HeadToHeadPrice(StrictModel):
    """Every arm of every budget point, priced at what the archive says a cell of this bank costs."""

    dataset_hash: str
    search_rows: int
    bench_rows: int
    cell: CellPrice
    optimizers: dict[str, OptimizerRoundPrice | None] = Field(
        description="Null where no campaign here called it in a round: its arms' figures leave "
        "its own calls out."
    )
    cells_in_flight: int = Field(
        description="The connector's ceiling (`Connector.max_cells_in_flight`) `wall_clock_s` is "
        "read at."
    )
    arms: list[ArmPrice]
    usd: float
    wall_clock_s: float | None = Field(description="Every arm minted after the last, in turn.")
    serial_s: float | None


def read_head_to_head_plan(path: Path) -> HeadToHeadPlan:
    try:
        raw = read_yaml(path)
    except (OSError, ValueError) as exc:
        raise PayloadInvalidError(f"plan {path}: {exc}", code="plan_unreadable") from exc
    return PayloadInvalidError.validating(HeadToHeadPlan, raw)


_PRICED_CAMPAIGNS = 8


def optimizer_round_price(stores: Stores, optimizer: str) -> OptimizerRoundPrice | None:
    ran = sorted(
        (
            campaign
            for campaign in stores.campaigns.list_campaigns(lifecycle="all")
            if campaign.treatment.optimizer == optimizer
        ),
        key=lambda campaign: campaign.created_at,
        reverse=True,
    )
    usd = seconds = 0.0
    rounds = campaigns = 0
    for campaign in ran[:_PRICED_CAMPAIGNS]:
        by_round = scan_ledger_spend_by_round(
            LedgerSpan(stores.campaigns.ledger(hop))
            for hop in stores.campaigns.line(campaign.root_hop)
        )
        # The origin opens no round: what its init sent is no round's price.
        paid = [
            spent
            for n, rollup in by_round.items()
            if n > 0 and (spent := rollup.by_kind["optimizer"].incurred_usd) > 0
        ]
        if not paid:
            continue
        usd += sum(paid)
        rounds += len(paid)
        campaigns += 1
        seconds += sum(
            sum(launch.clock.worked_s.get("optimizer", {}).values())
            for launch in line_launches(stores, campaign)
        )
    if not rounds:
        return None
    return OptimizerRoundPrice(
        usd=usd / rounds, seconds=seconds / rounds, rounds=rounds, campaigns=campaigns
    )


async def price_head_to_head(stores: Stores, plan: HeadToHeadPlan) -> HeadToHeadPrice:
    mints = plan.mints()
    configs = [FreshMint.of(stores, mint).campaign_config(stores) for mint in mints]
    opened = await init_services(
        dataset_name=plan.dataset_name, identity=stores.identity, stores=stores
    )
    # Every arm runs one environment (`SharedPart.environment`): the first resolves what all run.
    session = configure_and_apply_pipeline(opened, configs[0])
    partition = cycle_partition(session, session.bank.samples, configs[0])
    cell = measured_cell_price(session, list(node_config_items(session.pipeline_params)))
    if cell is None:
        raise PayloadInvalidError(
            f"no archive row prices a cell of {plan.dataset_name} on the models it runs, so "
            "there is no measured price to read. Buy one with `seed-screen "
            f"{plan.dataset_name} --seeds 0 --n-samples 20 --repeat 1`, whose cells the archive "
            "files.",
            code="cell_unpriced",
        )
    names = {select_optimizer(config.optimization).name for config in configs}
    return priced_head_to_head(
        mints,
        configs,
        dataset_hash=session.bank.dataset_hash,
        search=len(partition.search),
        bench=len(partition.bench),
        cell=cell,
        optimizers={name: optimizer_round_price(stores, name) for name in sorted(names)},
        cells_in_flight=session.backend_client.max_cells_in_flight,
    )


def priced_head_to_head(
    mints: Sequence[MintCampaignPayload],
    configs: Sequence[CampaignConfig],
    *,
    dataset_hash: str,
    search: int,
    bench: int,
    cell: CellPrice,
    optimizers: Mapping[str, OptimizerRoundPrice | None],
    cells_in_flight: int,
) -> HeadToHeadPrice:
    arms = [
        _arm_price(
            mint,
            config,
            search=search,
            bench=bench,
            cell=cell,
            optimizers=optimizers,
            cells_in_flight=cells_in_flight,
        )
        for mint, config in zip(mints, configs, strict=True)
    ]
    return HeadToHeadPrice(
        dataset_hash=dataset_hash,
        search_rows=search,
        bench_rows=bench,
        cell=cell,
        optimizers=dict(optimizers),
        cells_in_flight=cells_in_flight,
        arms=arms,
        usd=sum(arm.usd for arm in arms),
        wall_clock_s=_in_turn(arm.wall_clock_s for arm in arms),
        serial_s=_in_turn(arm.serial_s for arm in arms),
    )


def _in_turn(seconds: Iterable[float | None]) -> float | None:
    held = list(seconds)
    return None if None in held else sum(s for s in held if s is not None)


def _arm_price(
    mint: MintCampaignPayload,
    config: CampaignConfig,
    *,
    search: int,
    bench: int,
    cell: CellPrice,
    optimizers: Mapping[str, OptimizerRoundPrice | None],
    cells_in_flight: int,
) -> ArmPrice:
    selected = select_optimizer(config.optimization)
    if (unfit := check_search_split_holds_round(selected, search)) is not None:
        raise PayloadInvalidError(unfit, code="search_split_holds_no_round")
    held = optimizers[selected.name]
    optimizer_usd = 0.0 if held is None else held.usd
    rounds, opened, search_cells = _search_bought(
        config.optimization.budget,
        origin=min(config.sp_budget_origin, search),
        per_round=selected.round_cells_ceiling(search),
        cell_usd=cell.usd,
        optimizer_usd=optimizer_usd,
    )
    cells = search_cells + 2 * bench
    calls_s = 0.0 if held is None else opened * held.seconds
    refusal = check_cap_funds_round(config, selected, search, None, cell.usd)
    return ArmPrice(
        mint=mint,
        optimizer=selected.name,
        rounds=rounds,
        search_cells=search_cells,
        bench_cells=2 * bench,
        usd=cells * cell.usd + opened * optimizer_usd,
        wall_clock_s=None
        if cell.seconds is None
        else cells * cell.seconds / cells_in_flight + calls_s,
        serial_s=None if cell.seconds is None else cells * cell.seconds + calls_s,
        refused=None if refusal is None else refusal.title,
    )


def _search_bought(
    budget: Budget, *, origin: int, per_round: int, cell_usd: float, optimizer_usd: float
) -> tuple[int, int, int]:
    """(rounds closed, rounds whose optimizer calls were sent, cells): `usd` is read as the
    search-incurred ceiling a `compare` arm meters, so the round it runs out in is cut there."""
    if unread := sorted(
        axis for axis in ("tokens", "wall_clock_s") if getattr(budget, axis) is not None
    ):
        raise PayloadInvalidError(
            f"this budget point binds {', '.join(unread)}, which no cell price reads: bound it "
            "in `usd` or `rounds`, or price it by hand.",
            code="budget_unpriced",
        )
    cap = budget.rounds
    round_usd = per_round * cell_usd + optimizer_usd
    if budget.usd is None or round_usd <= 0:
        if cap is None:
            raise PayloadInvalidError(
                "this budget point bounds no round: name `rounds`, or a `usd` a cell costs "
                "against.",
                code="budget_unbounded",
            )
        return cap, cap, origin + cap * per_round
    left = budget.usd - origin * cell_usd
    if left < 0:
        return 0, 0, math.floor(budget.usd / cell_usd)
    closed = math.floor(left / round_usd)
    if cap is not None and cap <= closed:
        return cap, cap, origin + cap * per_round
    left -= closed * round_usd
    # The meter refuses a send its ceiling cannot hold, so a round it cannot open costs nothing.
    if left < optimizer_usd:
        return closed, closed, origin + closed * per_round
    left -= optimizer_usd
    return closed, closed + 1, origin + closed * per_round + math.floor(left / cell_usd)
