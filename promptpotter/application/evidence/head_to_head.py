"""Each campaign's bench headline in one table, guarded by whether the bench sets are one quantity.
The held-out set IS the comparability guard: `docs/architecture.md` § The bench score."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.datasets.authored import config_cell_scorer
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.bench import read_bench, read_pass
from promptpotter.application.runner.campaign_result import read_line_spend
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.domain.bench import BenchPass, BenchReading, BenchScore, DatasetSplit
from promptpotter.domain.campaign import (
    Arm,
    ArmBudget,
    Campaign,
    HeadToHeadRecord,
    Instrument,
    bench_instrument,
    ceiling_meter,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import MeteredSpend, SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.archive_queries import bench_reads
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.statistics import holm_adjusted, paired_reading

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.scoring import CellScorer, QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores


class HeadToHeadRow(StrictModel):
    """One campaign's bench headline beside what it cost to reach."""

    subject: str
    campaign_id: str
    optimizer: str
    # The head-to-head the campaign was minted an arm of; `None` for an undeclared campaign.
    arm: Arm | None
    # An arm of THIS table's declared head-to-head, read against that declaration.
    controlled: bool
    treatment_digest: str | None
    # The budget the campaign ran under: its head-to-head's for an arm, else its own config's.
    budget: ArmBudget
    # What that budget counts of `spend`; `None` beside a `None` spend.
    spend_metered: MeteredSpend | None
    # A cycle on its line was steered, skipped or model-overridden by an operator.
    human_intervened: bool
    # `None` where the line banked no origin's pass: nothing held out, or none sent yet. Its
    # `selected` is `None` until the line grades its selection.
    bench: BenchScore | None
    bench_set: Instrument | None
    # Against the declared instrument, else the row most others share one with; `None` for a row
    # with no headline.
    comparable: bool | None
    # The campaign's line, every cycle and launch on it (`CampaignResult.cost`); `None` for a
    # campaign whose line has banked no result yet.
    spend: SpendRollup | None
    calls: int | None
    # Each launch's clock less its origin gate and its cells' unworked time, summed.
    worked_s: float | None
    rounds: int
    # Each over `HeadToHead.ratio_reference`'s, `None` where either lacks it. INCURRED, never the
    # bill: an arm replaying a sibling's cells is billed nothing for them.
    incurred_usd_ratio: float | None
    # The `loop` bucket alone — the optimizer's own calls, the spend arms differ on by manifest.
    loop_incurred_usd_ratio: float | None
    worked_ratio: float | None
    # The share of the search's incurred USD a replay answered, so billed nothing — what this
    # campaign took from a cache another paid into. `None` where the search incurred nothing.
    replay_share: float | None
    # Bench lift per USD the SEARCH incurred (`BenchScore.lift_per_usd`); `None` without a lift,
    # or where the search carries tokens no rate priced.
    lift_per_incurred_usd: float | None
    # Campaigns whose run overlapped this one's: while both ran they shared the content-addressed
    # cache, so a cell's bill and clock went to whichever reached it first.
    concurrent_with: list[str]
    # Individuals the archive has graded on these held-out rows, this read's own included: each
    # one chosen off a headline spends the holdout, so a high count reads optimistic. `None` = no
    # bench set.
    bench_reads: int | None


class SelectionPair(StrictModel):
    """Two campaigns' selections paired on the bench rows both scored, in the headline composite.
    ``shift = b - a``: ``BenchScore.lift``'s arithmetic with ``a`` where the origin stands."""

    campaign_a: str
    campaign_b: str
    shift: float
    ci_lo: float | None
    ci_hi: float | None
    p_value: float | None
    # Holm across every pair in this table that carries a p.
    p_adjusted: float | None
    n_rows: int


class HeadToHead(StrictModel):
    """The campaigns' bench headlines side by side, and whether one instrument graded them all."""

    rows: list[HeadToHeadRow]
    # The head-to-head whose declaration this table reads: the one most arms in it name.
    head_to_head_id: str | None
    # The one grader every row's bench is read under — the declared one, else the oldest
    # campaign's formula — so no two headlines differ by the function that graded them.
    scorer_id: str
    # `None` below two graded rows: one headline is compared with nothing.
    verdict: bool | None
    # `Instrument` fields, plus `budget` and `human_intervened` where rows differ on them, and
    # `origin_reading` where campaigns on one set read one origin apart.
    differs_on: list[str]
    # Only campaigns on ONE instrument and budget, unsteered, with distinct treatments pair
    # (`_one_instrument`); any other pair is refused.
    pairs: list[SelectionPair]
    # The oldest row carrying a spend and a worked clock, which every row's `*_ratio` divides by.
    ratio_reference: str | None
    # `verdict` in one sentence.
    verdict_line: str
    # Why a row that is no arm of the declared head-to-head is NOT CONTROLLED; `None` where none is.
    uncontrolled_note: str | None
    # The rest qualifying the verdict, one sentence each.
    notes: list[str]


class HeadToHeadEntry(NamedTuple):
    """The subject standing for one campaign — its own, or a course on it — and its tree."""

    reading: SubjectReading
    stores: Stores


class _Graded(NamedTuple):
    row: HeadToHeadRow
    # Each pass's graded rows; `None` where the pass read nothing.
    origin_rows: list[QueryMeasurement] | None
    selected_rows: list[QueryMeasurement] | None
    # Each launch's start and finish, epoch seconds.
    windows: list[tuple[float, float]]


_ORIGIN_READING = "origin_reading"


def _declared(
    entries: list[HeadToHeadEntry], campaigns: list[Campaign]
) -> tuple[HeadToHeadRecord, int] | None:
    """The record most arms here name, ties to the oldest arm's, and its first arm's slot —
    ``None`` where no arm is here. An arm of another record is a foreign row, never a veto."""
    named = [c.arm.head_to_head_id for c in campaigns if c.arm is not None]
    if not named:
        return None
    h2h_id = max(dict.fromkeys(named), key=named.count)
    slot = next(
        i for i, c in enumerate(campaigns) if c.arm is not None and c.arm.head_to_head_id == h2h_id
    )
    record = entries[slot].stores.campaigns.load_head_to_head(h2h_id)
    return None if record is None else (record, slot)


def head_to_head(entries: list[HeadToHeadEntry]) -> HeadToHead | None:
    """Entries arrive oldest first. Arms of one declared head-to-head are read under its declared
    scorer and against its instrument; with none declared, the oldest names the formula."""
    if not entries:
        return None
    campaigns = [_campaign(entry) for entry in entries]
    configs = [
        resolve_campaign_config(e.stores, c, c.root_hop)
        for e, c in zip(entries, campaigns, strict=True)
    ]
    declared = _declared(entries, campaigns)
    scorer, scorer_id = config_cell_scorer(configs[0 if declared is None else declared[1]])
    record = None if declared is None else declared[0]
    if record is not None and scorer_id != record.instrument.scorer_id:
        raise ValueError(
            f"head-to-head {record.head_to_head_id} declares scorer {record.instrument.scorer_id}; "
            f"its arm's config grades as {scorer_id}"
        )
    read = [
        _read(e, c, config, scorer, scorer_id, record)
        for e, c, config in zip(entries, campaigns, configs, strict=True)
    ]
    base = next(
        (g.row for g in read if g.row.spend is not None and g.row.worked_s is not None), None
    )
    graded = [g for g in read if g.row.bench is not None and g.row.bench_set is not None]
    # With nothing declared, the row most others share an instrument with; ties go to the oldest.
    reference = max(graded, key=lambda g: sum(_one_instrument(g, o) for o in graded), default=None)
    sets = [g.row.bench_set for g in graded] + ([] if record is None else [record.instrument])
    differs_on = sorted(
        field
        for field in Instrument.model_fields
        if len({repr(getattr(bench_set, field)) for bench_set in sets}) > 1
    )
    if len({repr(g.row.budget) for g in graded}) > 1:
        differs_on.append("budget")
    if any(g.row.human_intervened for g in graded):
        differs_on.append("human_intervened")
    if any(
        a.row.bench_set == b.row.bench_set and not _read_alike(a, b)
        for i, a in enumerate(graded)
        for b in graded[i + 1 :]
    ):
        differs_on.append(_ORIGIN_READING)
    verdict = None if len(graded) < 2 else not differs_on
    concurrent = {
        g.row.campaign_id: [o.row.campaign_id for o in read if o is not g and _overlapped(g, o)]
        for g in read
    }
    return HeadToHead(
        rows=[
            g.row.model_copy(
                update={
                    "comparable": None
                    if g.row.bench is None or g.row.bench_set is None
                    else _on_declared(g, record)
                    if record is not None
                    else reference is not None and _one_instrument(g, reference),
                    "concurrent_with": concurrent[g.row.campaign_id],
                    **_ratios(g.row, base),
                }
            )
            for g in read
        ],
        head_to_head_id=None if record is None else record.head_to_head_id,
        scorer_id=scorer_id,
        verdict=verdict,
        differs_on=differs_on,
        pairs=_pairs(graded),
        ratio_reference=None if base is None else base.campaign_id,
        verdict_line=_verdict_line(verdict, len(graded)),
        uncontrolled_note=None
        if record is None or all(g.row.controlled for g in read)
        else f"Ran as no arm of head-to-head {record.head_to_head_id}, so its search could read "
        "other campaigns' measurements and take an operator's steer.",
        notes=_notes(
            verdict,
            differs_on,
            len(graded),
            len(read),
            [cid for cid, others in concurrent.items() if others],
            declared=record,
        ),
    )


def _ratios(row: HeadToHeadRow, base: HeadToHeadRow | None) -> dict[str, float | None]:
    def over(fact: Callable[[HeadToHeadRow], float | None]) -> float | None:
        value, of_base = fact(row), None if base is None else fact(base)
        return None if value is None or of_base is None or of_base <= 0.0 else value / of_base

    return {
        "incurred_usd_ratio": over(
            lambda r: None if r.spend is None else r.spend.total_incurred_usd
        ),
        "loop_incurred_usd_ratio": over(
            lambda r: None if r.spend is None else r.spend.loop.incurred_usd
        ),
        "worked_ratio": over(lambda r: r.worked_s),
    }


def _overlapped(a: _Graded, b: _Graded) -> bool:
    return any(sa < eb and sb < ea for sa, ea in a.windows for sb, eb in b.windows)


def _origin(g: _Graded) -> BenchReading | None:
    return None if g.row.bench is None else g.row.bench.origin


def _on_declared(g: _Graded, record: HeadToHeadRecord) -> bool:
    return (
        g.row.bench_set == record.instrument
        and g.row.budget == record.budget
        and not g.row.human_intervened
    )


def _one_instrument(a: _Graded, b: _Graded) -> bool:
    """One bench set and budget, neither run steered, and their shared origin read alike."""
    return (
        a.row.bench_set == b.row.bench_set
        and a.row.bench is not None
        and b.row.bench is not None
        and a.row.budget == b.row.budget
        and not (a.row.human_intervened or b.row.human_intervened)
        and _read_alike(a, b)
    )


def _read_alike(a: _Graded, b: _Graded) -> bool:
    """One formula grades every row here, so a shared origin read apart on its rows is the
    backend or a judge that moved — which no stamp names."""
    oa, ob = _origin(a), _origin(b)
    if oa is None or ob is None or oa.sp_hash != ob.sp_hash:
        return True
    if a.origin_rows is None or b.origin_rows is None:
        return True
    _gap, lo, hi, _p, _n = _paired(a.origin_rows, b.origin_rows)
    return lo is None or hi is None or lo <= 0.0 <= hi


def _paired(
    a_rows: list[QueryMeasurement], b_rows: list[QueryMeasurement]
) -> tuple[float, float | None, float | None, float | None, int]:
    b_grades, a_grades = paired_fitness(b_rows, a_rows, grade="objective")
    return paired_reading(b_grades, a_grades)


def _campaign(entry: HeadToHeadEntry) -> Campaign:
    campaign = entry.stores.campaigns.load_campaign(entry.reading.campaign_id)
    if campaign is None:
        raise ValueError(
            f"campaign {entry.reading.campaign_id} has no manifest to read its bench from"
        )
    return campaign


def _read(
    entry: HeadToHeadEntry,
    campaign: Campaign,
    config: CampaignConfig,
    scorer: CellScorer,
    scorer_id: str,
    record: HeadToHeadRecord | None,
) -> _Graded:
    """Everything but the instrument's inputs is the campaign's result: its passes read under the
    one scorer, and the cost of its whole line, whichever cycle the line ended on."""
    reading, stores = entry
    result = stores.campaigns.load_result(campaign.campaign_id)
    hop = (
        campaign.root_hop
        if result is None
        else CycleHop(campaign_id=campaign.campaign_id, cycle_id=result.cycle_id)
    )
    layout = CycleLayout(stores.campaigns.cycle_dir(hop))
    passes = None if result is None else result.bench
    bench = None if passes is None else read_bench(stores, passes, scorer, scorer_id=scorer_id)

    def rows_of(bench_pass: BenchPass, tolerance: int) -> list[QueryMeasurement] | None:
        read = read_pass(stores, bench_pass, scorer, tolerance=tolerance)
        return None if read.reading is None else read.rows

    partition = read_json_tolerant(layout.bank_partition, {})
    bench_ids = frozenset(int(i) for i in partition.get("bench_ids") or [])
    origin_params = read_json_tolerant(layout.round_file(0), {}).get("pipeline_params")
    bench_set = (
        bench_instrument(
            dataset_name=campaign.dataset_name,
            dataset_hash=read_json_tolerant(layout.export, {}).get("dataset_hash"),
            split=DatasetSplit.model_validate(partition["split"]) if partition["split"] else None,
            bench_ids=bench_ids,
            scorer_id=scorer_id,
            origin_params=origin_params,
            origin=campaign.root_content_hash,
        )
        if partition and bench is not None and bench.selected is not None
        else None
    )
    cost = None if result is None else result.cost
    # Live off the line's ledgers, the fold the campaign card's bill reads: the banked cost is the
    # last ended launch's, so a running arm's would lag its own bill.
    spend = None if cost is None else read_line_spend(stores, campaign)
    windows = [
        (start, end)
        for run in ([] if cost is None else cost.launches)
        if (start := epoch_seconds(run.started_at)) is not None
        and (end := epoch_seconds(run.finished_at)) is not None
    ]
    return _Graded(
        row=HeadToHeadRow(
            subject=reading.key,
            campaign_id=reading.campaign_id,
            optimizer=config.optimization.optimizer,
            arm=campaign.arm,
            # A row's own fact: an arm of the read's record, on its budget and — once graded — its
            # instrument.
            controlled=record is not None
            and campaign.arm is not None
            and campaign.arm.head_to_head_id == record.head_to_head_id
            and config.optimization.arm_budget == record.budget
            and (bench_set is None or bench_set == record.instrument),
            treatment_digest=None if campaign.treatment is None else campaign.treatment.digest,
            budget=config.optimization.arm_budget,
            spend_metered=None
            if spend is None
            else MeteredSpend.of(spend, ceiling_meter(campaign.arm)),
            human_intervened=reading.human_intervened,
            bench=bench,
            bench_set=bench_set,
            comparable=None,
            spend=spend,
            calls=None if cost is None else cost.calls,
            worked_s=None if cost is None else cost.worked_s,
            rounds=reading.cycle_rounds_scored,
            incurred_usd_ratio=None,
            loop_incurred_usd_ratio=None,
            worked_ratio=None,
            replay_share=None if spend is None else spend.search_replay_share,
            lift_per_incurred_usd=None
            if bench is None or spend is None
            else bench.lift_per_usd(spend),
            concurrent_with=[],
            bench_reads=None
            if bench_set is None
            else bench_reads(stores, dataset_name=campaign.dataset_name, sample_ids=bench_ids),
        ),
        origin_rows=None if passes is None else rows_of(passes.origin, passes.tolerance),
        selected_rows=None
        if passes is None or passes.selected is None
        else rows_of(passes.selected, passes.tolerance),
        windows=windows,
    )


def _pairs(graded: list[_Graded]) -> list[SelectionPair]:
    out: list[SelectionPair] = []
    for i, a in enumerate(graded):
        for b in graded[i + 1 :]:
            if not _one_instrument(a, b) or a.selected_rows is None or b.selected_rows is None:
                continue
            # Two arms running one treatment contrast no optimizer.
            if a.row.controlled and b.row.treatment_digest == a.row.treatment_digest:
                continue
            shift, lo, hi, p_value, n = _paired(a.selected_rows, b.selected_rows)
            if not n:
                continue
            out.append(
                SelectionPair(
                    campaign_a=a.row.campaign_id,
                    campaign_b=b.row.campaign_id,
                    shift=shift,
                    ci_lo=lo,
                    ci_hi=hi,
                    p_value=p_value,
                    p_adjusted=None,
                    n_rows=n,
                )
            )
    tested = [i for i, pair in enumerate(out) if pair.p_value is not None]
    adjusted = holm_adjusted([out[i].p_value or 0.0 for i in tested])
    for slot, i in enumerate(tested):
        out[i] = out[i].model_copy(update={"p_adjusted": adjusted[slot]})
    return out


def _verdict_line(verdict: bool | None, n_graded: int) -> str:
    if verdict is None:
        return f"No verdict: a head-to-head needs two campaigns with a graded bench set; {n_graded} here."
    if verdict:
        return (
            "Comparable: one bank, split, held-out row set, scorer, target model, origin and "
            "budget, so the selected column is one quantity."
        )
    return "Not comparable: these headlines are NOT one quantity, and no pair is read across two."


def _notes(
    verdict: bool | None,
    differs_on: list[str],
    n_graded: int,
    n_rows: int,
    concurrent: list[str],
    *,
    declared: HeadToHeadRecord | None,
) -> list[str]:
    notes = []
    if declared is not None:
        notes.append(
            f"Read against head-to-head {declared.head_to_head_id}'s declared instrument, scorer "
            "and budget."
        )
    if verdict is True:
        notes.append("Every pair below is read on the same rows.")
    if verdict is False:
        notes.append("A row off the declared or most shared instrument is marked.")
    if _ORIGIN_READING in differs_on:
        notes.append(
            "`origin_reading`: campaigns on one bench set read the same origin apart beyond its "
            "own noise under one formula, so the backend or a judge moved between them — a change "
            "no stamp names."
        )
    if n_rows > n_graded:
        notes.append(
            f"{n_rows - n_graded} campaign(s) carry no bench headline — nothing held out, or the "
            "line has not graded its selection — and sit outside the verdict."
        )
    if concurrent:
        notes.append(
            f"{', '.join(concurrent)} ran concurrently on one content-addressed cache: the first "
            "to reach a cell paid and the rest replayed it, so the bill and the clock split by "
            "arrival. The USD ratios price INCURRED spend, which counts a replay as paid; the "
            "worked-seconds ratio stays confounded."
        )
    return notes


__all__ = [
    "HeadToHead",
    "HeadToHeadEntry",
    "HeadToHeadRow",
    "SelectionPair",
    "head_to_head",
]
