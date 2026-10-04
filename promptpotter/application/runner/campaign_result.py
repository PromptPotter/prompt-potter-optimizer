"""The campaign's result (``domain/campaign.py::CampaignResult``), banked by the cycle answering for
its LINE — the root, or where supersede cuts handed it on. A cycle beside the line (a steered or diag
offshoot) is not the campaign's result, so it banks nothing and grades nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.datasets.authored import config_cell_scorer
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.bench import read_bench, score_on_bench, unheld_bench
from promptpotter.domain.bench import BenchPasses, BenchScore
from promptpotter.domain.campaign import ArmCost, Campaign, CampaignResult, Launch
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_spend,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "bank_campaign_result",
    "bench_origin",
    "read_campaign_bench",
    "read_cycle_bench",
    "read_line_spend",
]


def _line(stores: Stores, hop: CycleHop) -> list[CycleHop] | None:
    """The campaign's line where *hop* holds it now, oldest first; ``None`` for any other cycle."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        return None
    line = stores.campaigns.line(campaign.root_hop)
    return line if line[-1] == hop else None


def bank_campaign_result(
    stores: Stores,
    hop: CycleHop,
    *,
    started_at: str,
    finished_at: str,
    optimizer_phases: frozenset[str],
    bench: BenchPasses | None,
) -> None:
    """Rewrite the line's result as this launch leaves it: the cost refolded off every ledger on the
    line, this launch's clock, and *bench* — what the launch banked, ``None`` where it reached no
    bench pass and the record's passes stand."""
    line = _line(stores, hop)
    if line is None:
        return
    ledgers = [CycleLayout(stores.campaigns.cycle_dir(h)).ledger for h in line]
    prior = stores.campaigns.load_result(hop.campaign_id)
    spend, calls = scan_ledger_spend(ledgers)
    launch = Launch(
        started_at=started_at,
        finished_at=finished_at,
        clock=scan_ledger_wall_clock(
            ledgers,
            started_at=started_at,
            finished_at=finished_at,
            optimizer_phases=optimizer_phases,
        ),
    )
    earlier = (
        [] if prior is None else [r for r in prior.cost.launches if r.started_at != started_at]
    )
    stores.campaigns.write_result(
        hop.campaign_id,
        CampaignResult(
            cycle_id=hop.cycle_id,
            bench=bench if bench is not None or prior is None else prior.bench,
            cost=ArmCost(spend=spend, calls=calls, launches=[*earlier, launch]),
        ),
    )


def _bench_counted(spend: SpendRollup) -> tuple[float, int]:
    bench = spend.bench
    return bench.incurred_usd, bench.input_tokens + bench.output_tokens


async def bench_origin(
    session: Session,
    origin_sp: JobSearchPoint,
    *,
    started_at: str,
    optimizer_phases: frozenset[str],
    spend: SpendRollup,
    cb: RunCallbacks,
) -> BenchPasses | None:
    """The origin's pass the line holds: an earlier launch's where it sent these rows under this
    grader, else one sent now — so no launch pays twice for the reference. *spend* is the live
    rollup the reserve is read off. ``None`` for a cycle beside the line."""
    stores, hop = session.store, session.hop
    if _line(stores, hop) is None:
        return None
    sent = (
        origin_sp.sp_hash(session.pipeline_schema),
        [s.id for s in session.scoring.require_partition().bench],
        session.scoring.scorer_id,
    )
    prior = stores.campaigns.load_result(hop.campaign_id)
    held = None if prior is None else prior.bench
    if (
        held is not None
        and held.origin.stopped is None
        and (held.origin.sp_hash, held.origin.sample_ids, held.origin.scorer_id) == sent
    ):
        banked = held.model_copy(update={"selected": None})
    else:
        # The bench bucket's own delta: a search call landing while this pass is out is not reserve.
        usd_before, tokens_before = _bench_counted(spend)
        origin_pass = await score_on_bench(session, origin_sp, subject="origin", round_num=0, cb=cb)
        split = session.scoring.require_partition().split
        usd_after, tokens_after = _bench_counted(spend)
        banked = BenchPasses(
            tolerance=split.tolerance if split is not None else 0,
            origin=origin_pass,
            reserve_usd=usd_after - usd_before,
            reserve_tokens=tokens_after - tokens_before,
            selected=None,
        )
    bank_campaign_result(
        stores,
        hop,
        started_at=started_at,
        finished_at=utcnow_iso(),
        optimizer_phases=optimizer_phases,
        bench=banked,
    )
    return banked


def read_line_spend(stores: Stores, campaign: Campaign) -> SpendRollup:
    """The line's spend as ``bank_campaign_result`` folds it, live."""
    spend, _ = scan_ledger_spend(
        CycleLayout(stores.campaigns.cycle_dir(h)).ledger
        for h in stores.campaigns.line(campaign.root_hop)
    )
    return spend


def read_campaign_bench(stores: Stores, campaign: Campaign) -> BenchScore | None:
    """The campaign's headline, read off its result under the formula its line runs; ``None`` until
    the line banks an origin's pass or, holding nothing out, first ends a launch."""
    result = stores.campaigns.load_result(campaign.campaign_id)
    return None if result is None else _headline(stores, campaign, result)


def read_cycle_bench(stores: Stores, hop: CycleHop) -> BenchScore | None:
    """The campaign's headline where *hop* answers for its result; ``None`` for any other cycle."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    result = stores.campaigns.load_result(hop.campaign_id)
    if campaign is None or result is None or result.cycle_id != hop.cycle_id:
        return None
    return _headline(stores, campaign, result)


def _headline(stores: Stores, campaign: Campaign, result: CampaignResult) -> BenchScore | None:
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=result.cycle_id)
    config = resolve_campaign_config(stores, campaign, hop)
    scorer, scorer_id = config_cell_scorer(config)
    if result.bench is not None:
        return read_bench(stores, result.bench, scorer, scorer_id=scorer_id)
    split = config.dataset_split
    return unheld_bench(scorer_id) if split is None or split.bench == 0 else None
