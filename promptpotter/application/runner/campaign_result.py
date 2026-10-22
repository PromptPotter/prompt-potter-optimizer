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
from promptpotter.application.scoring.closed_rounds import cycle_instrument
from promptpotter.domain.bench import BenchPasses, BenchScore, LineRun
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import candidate_label
from promptpotter.domain.run_records import LaunchClockRecord
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LedgerSpend,
    scan_bench_passes,
    scan_launch_clocks,
    scan_ledger_spend,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import LaunchClock, Session
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.scoring import Scorer
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.stores import Stores


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
    ledger: CycleEventLog,
    clock: LaunchClock,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> None:
    """Once per launch, on the cycle holding the line as it ends: a rebased parent banks none."""
    line = _line(stores, hop)
    if line is None:
        return
    wall_clock = scan_ledger_wall_clock(
        [stores.campaigns.ledger(h) for h in line],
        started_at=clock.started_at,
        finished_at=finished_at,
        optimizer_phases=optimizer_phases,
    )
    ledger.append(
        LaunchClockRecord(started_at=clock.started_at, finished_at=finished_at, clock=wall_clock)
    )


def line_launches(stores: Stores, campaign: Campaign) -> list[LaunchClockRecord]:
    """Each cycle's OWN appends: a launch banks on the cycle holding the line as it ends."""
    return scan_launch_clocks(
        LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
    )


def _bench_counted(spend: SpendRollup) -> Budget:
    bench = spend.by_kind["bench"]
    return Budget.amount(bench.incurred_usd, bench.input_tokens + bench.output_tokens)


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
    held = line_passes(stores, hop.campaign_id)
    if held is not None and held.origin.answers(
        origin_sp.sp_hash(session.pipeline_schema),
        [s.key for s in session.state.scoring.require_partition().bench],
        session.state.scoring.require_scorer().id,
    ):
        passes = held
    else:
        # The bench bucket's own delta: a search call landing while this pass is out is not reserve.
        before = _bench_counted(spend)
        origin_pass = await score_on_bench(
            session,
            origin_sp,
            individual_id=origin_id,
            subject="origin",
            label=candidate_label(0, 0),
            round_num=0,
            cb=cb,
        )
        split = session.state.scoring.require_partition().split
        after = _bench_counted(spend)
        passes = BenchPasses(
            tolerance=split.tolerance if split is not None else 0,
            origin=origin_pass,
            reserve_usd=after.usd_amount - before.usd_amount,
            reserve_tokens=after.token_amount - before.token_amount,
            # A pass sent beside another reference pairs with nothing on this one.
            selections={},
        )
    graded(
        cb,
        session,
        passes.origin,
        subject="origin",
        label=candidate_label(0, 0),
        reserve=Budget.amount(passes.reserve_usd, passes.reserve_tokens),
    )
    return passes


def read_line_spend(stores: Stores, campaign: Campaign) -> LedgerSpend:
    """Every cycle read WHOLE: what a superseded cycle paid past its cut is the line's cost too."""
    return scan_ledger_spend(
        LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
    )


def line_run(stores: Stores, hop: CycleHop) -> LineRun:
    # A launch stopped before run init names no cycle.
    named = bool(hop.cycle_id)
    index = stores.campaigns.load(hop, moment=None) if named else None
    standing = None if index is None else index.standing
    return LineRun(
        selecting=named and hop.cycle_id in stores.campaigns.attached_cycle_ids(hop.campaign_id),
        ending=None if index is None else index.stop_reason,
        selection=None if standing is None else standing.selection,
        rounds_closed=0 if standing is None else standing.rounds_closed,
    )


def line_passes(stores: Stores, campaign_id: str) -> BenchPasses | None:
    """The line's OWN ledgers: a root cut from another tree graded its prefix in another epoch."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        return None
    return scan_bench_passes(
        LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
    )


def read_cycle_bench(stores: Stores, campaign: Campaign, hop: CycleHop) -> BenchScore:
    return cycle_bench(stores, campaign, hop, resolve_campaign_config(stores, campaign, hop))


def cycle_bench(
    stores: Stores, campaign: Campaign, hop: CycleHop, config: CampaignConfig
) -> BenchScore:
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
        inner=stores.inner,
        held_by=None if holder == hop else holder.cycle_id,
        trigger=config.bench_trigger,
        held_out=len(drawn.bench_ids) if drawn is not None else 0 if split is None else split.bench,
        instrument_id=cycle_instrument(
            drawn, stores.campaigns.standing_rounds(hop, moment=None).rounds
        ),
        run=run,
        spend=read_line_spend(stores, campaign).spend,
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
            inner=stores.inner,
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
