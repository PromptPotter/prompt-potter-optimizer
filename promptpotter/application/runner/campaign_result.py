"""Only the cycle holding the campaign's LINE banks or grades; one beside it does neither."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.bench import (
    BenchLine,
    declare_bench,
    graded,
    read_bench,
    score_on_bench,
)
from promptpotter.application.scoring.cells import cycle_instrument
from promptpotter.domain.bench import BenchPasses, BenchScore, LineRun
from promptpotter.domain.campaign import ArmCost, Campaign, CampaignResult, Launch
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import candidate_label
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_bench_passes,
    scan_ledger_spend,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerSpan

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.scoring import Scorer
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "bank_campaign_result",
    "bench_line",
    "bench_origin",
    "bench_under",
    "declare_line_bench",
    "line_passes",
    "line_run",
    "read_campaign_bench",
    "read_cycle_bench",
    "read_line_spend",
]


def _line(stores: Stores, hop: CycleHop) -> list[CycleHop] | None:
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
) -> None:
    line = _line(stores, hop)
    if line is None:
        return
    ledgers = [CycleLayout(stores.campaigns.cycle_dir(h)).ledger for h in line]
    prior = stores.campaigns.load_result(hop.campaign_id)
    calls = scan_ledger_spend(LedgerSpan(ledger) for ledger in ledgers).calls
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
            cost=ArmCost(calls=calls, launches=[*earlier, launch]),
        ),
    )


def _bench_counted(spend: SpendRollup) -> tuple[float, int]:
    bench = spend.by_kind["bench"]
    return bench.incurred_usd, bench.input_tokens + bench.output_tokens


async def bench_origin(
    session: Session,
    origin_sp: JobSearchPoint,
    *,
    origin_id: str,
    spend: SpendRollup,
    cb: RunCallbacks,
) -> BenchPasses | None:
    stores, hop = session.store, session.hop
    if _line(stores, hop) is None:
        return None
    sent = (
        origin_sp.sp_hash(session.pipeline_schema),
        [s.key for s in session.scoring.require_partition().bench],
        session.scoring.require_scorer().id,
    )
    held = line_passes(stores, hop.campaign_id)
    if (
        held is not None
        and held.origin.stop is None
        and (held.origin.sp_hash, held.origin.sample_keys, held.origin.scorer_id) == sent
    ):
        passes = held
    else:
        # The bench bucket's own delta: a search call landing while this pass is out is not reserve.
        usd_before, tokens_before = _bench_counted(spend)
        origin_pass = await score_on_bench(
            session,
            origin_sp,
            individual_id=origin_id,
            subject="origin",
            label=candidate_label(0, 0),
            round_num=0,
            cb=cb,
        )
        split = session.scoring.require_partition().split
        usd_after, tokens_after = _bench_counted(spend)
        passes = BenchPasses(
            tolerance=split.tolerance if split is not None else 0,
            origin=origin_pass,
            reserve_usd=usd_after - usd_before,
            reserve_tokens=tokens_after - tokens_before,
            # A pass sent beside another reference pairs with nothing on this one.
            selections={},
        )
    graded(
        cb,
        session,
        passes.origin,
        subject="origin",
        label=candidate_label(0, 0),
        reserve=(passes.reserve_usd, passes.reserve_tokens),
    )
    return passes


def read_line_spend(stores: Stores, campaign: Campaign) -> SpendRollup:
    """Every cycle read WHOLE: what a superseded cycle paid past its cut is the line's cost too."""
    return scan_ledger_spend(
        LedgerSpan(CycleLayout(stores.campaigns.cycle_dir(h)).ledger)
        for h in stores.campaigns.line(campaign.root_hop)
    ).spend


def line_run(stores: Stores, hop: CycleHop) -> LineRun:
    # A launch stopped before run init names no cycle.
    named = bool(hop.cycle_id)
    index = stores.campaigns.load(hop) if named else None
    standing = None if index is None else index.standing
    return LineRun(
        selecting=named and hop.cycle_id in stores.campaigns.attached_cycle_ids(hop.campaign_id),
        ending=None if index is None else index.stop_reason,
        selection=None if standing is None else standing.selection,
        rounds_closed=0 if standing is None else standing.rounds_closed,
    )


def line_passes(stores: Stores, campaign_id: str) -> BenchPasses | None:
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        return None
    return scan_bench_passes(
        LedgerSpan(CycleLayout(stores.campaigns.cycle_dir(h)).ledger)
        for h in stores.campaigns.line(campaign.root_hop)
    )


def read_campaign_bench(stores: Stores, campaign: Campaign) -> BenchScore:
    return read_cycle_bench(stores, campaign, stores.campaigns.line_holder(campaign.root_hop))


def read_cycle_bench(stores: Stores, campaign: Campaign, hop: CycleHop) -> BenchScore:
    config = resolve_campaign_config(stores, campaign, hop)
    return bench_under(
        stores,
        campaign,
        hop,
        line_passes(stores, campaign.campaign_id),
        config,
        scorer_of(config, verifier_graded=False),
        run=line_run(stores, hop),
    )


def bench_line(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
    config: CampaignConfig,
    *,
    run: LineRun,
) -> BenchLine:
    holder = stores.campaigns.line_holder(campaign.root_hop)
    drawn = stores.campaigns.read_bank_partition(hop)
    split = config.dataset_split
    return BenchLine(
        hop=hop,
        on_line=holder == hop,
        held_by=None if holder == hop else holder.cycle_id,
        trigger=config.bench_trigger,
        held_out=len(drawn.bench_ids) if drawn is not None else 0 if split is None else split.bench,
        instrument_id=cycle_instrument(
            campaign.dataset_name, stores.campaigns.standing_rounds(hop).rounds
        ),
        run=run,
        spend=read_line_spend(stores, campaign),
    )


def bench_under(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
    passes: BenchPasses | None,
    config: CampaignConfig,
    scorer: Scorer,
    *,
    run: LineRun,
) -> BenchScore:
    line = bench_line(stores, campaign, hop, config, run=run)
    return read_bench(stores, passes if line.on_line else None, scorer, line=line)


def declare_line_bench(
    session: Session,
    config: CampaignConfig,
    cb: RunCallbacks,
    *,
    passes: BenchPasses | None,
    selecting: bool,
    ending: StopReason | None,
) -> BenchScore:
    stores, hop = session.store, session.hop
    scorer = scorer_of(config, verifier_graded=False)
    # The index says `selecting` / `ending` only once the run closes; the launch's own word wins.
    run = line_run(stores, hop)._replace(selecting=selecting, ending=ending)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None or not hop.cycle_id:
        split = config.dataset_split
        line = BenchLine(
            hop=hop,
            on_line=False,
            held_by=None,
            trigger=config.bench_trigger,
            held_out=0 if split is None else split.bench,
            instrument_id=session.instrument_id,
            run=run,
            spend=None,
        )
        return declare_bench(cb, read_bench(stores, None, scorer, line=line))
    held = passes if passes is not None else line_passes(stores, hop.campaign_id)
    return declare_bench(cb, bench_under(stores, campaign, hop, held, config, scorer, run=run))
