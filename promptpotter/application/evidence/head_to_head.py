"""Each campaign's bench headline in one table, guarded by whether the bench sets are one quantity.
The held-out set IS the comparability guard: `docs/architecture.md` § The bench score."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.datasets.authored import config_cell_scorer
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.jobs.quota import declare_run_ceiling
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.bench import bench_rows
from promptpotter.application.runner.campaign_result import headline_under, read_line_spend
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    COLUMN_GRADE,
    BandedValue,
    BenchColumn,
    BenchReading,
    BenchScore,
    DatasetSplit,
    bench_missing_reason,
)
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
from promptpotter.domain.dashboard_rows import LiftSide, lift_side
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.spend import MeteredSpend, SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.archive_queries import bench_reads
from promptpotter.infrastructure.store.campaign_store.store import cycle_ending
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.statistics import holm_adjusted, paired_reading

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.scoring import CellScorer, QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores


class HeadlineLift(BandedValue):
    """A bench's lift in its headline column, with the side of 0 its band sits on: the one lift a
    surface leads with, so none picks the column or reads the sign."""

    side: LiftSide | None

    @classmethod
    def of(cls, bench: BenchScore | None) -> HeadlineLift | None:
        if bench is None or (lift := bench.headline_lift) is None:
            return None
        return cls(**lift.model_dump(), side=lift_side(lift.ci_lo, lift.ci_hi))


class HeadToHeadRow(StrictModel):
    """One campaign's bench headline beside what it cost to reach."""

    subject: str
    campaign_id: str
    optimizer: str
    # Every model its optimizer's llm nodes call, sorted: a stronger one is a lift no manifest made.
    optimizer_models: list[str]
    # The head-to-head the campaign was minted an arm of; `None` for an undeclared campaign.
    arm: Arm | None
    # An arm of THIS table's declared head-to-head, read against that declaration.
    controlled: bool
    treatment_digest: str | None
    # The budget the line's cycle runs under — its config's, with any cap an operator set on it.
    budget: ArmBudget
    # What that budget counts of `spend`; `None` beside a `None` spend.
    spend_metered: MeteredSpend | None
    # A cycle on its line was steered, skipped or model-overridden by an operator.
    human_intervened: bool
    # The line holder's `index.json::stop_reason`; `None` while that cycle has not ended.
    stop_reason: StopReason | None
    # Its class off `STOP_REASON_INFO`; a `failed` arm ended on no result, whatever `bench` holds.
    outcome: StopOutcome | None
    # `None` until the line banks an origin's pass; `missing_reason` where it holds nothing out.
    # Its `selected` is `None` until the line grades its selection.
    bench: BenchScore | None
    # `None` where `bench` carries no lift in its headline column.
    headline_lift: HeadlineLift | None
    # Why `bench` is `None`; `None` beside one.
    bench_missing_reason: str | None
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
    # The `optimizer` kind alone — the optimizer's own calls, the spend arms differ on by manifest.
    optimizer_incurred_usd_ratio: float | None
    worked_ratio: float | None
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
    """Two campaigns' selections paired on the bench rows both scored, in the headline column.
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
    # The column every row's headline, its lift per USD and every pair is read in.
    headline: BenchColumn
    # `None` below two graded rows, and while an arm of the declared head-to-head is ungraded: the
    # table is not yet the comparison that was declared.
    verdict: bool | None
    # `Instrument` fields, plus `budget`, `optimizer_models` and `human_intervened` where rows
    # differ on them, and `origin_reading` where campaigns on one set read one origin apart.
    differs_on: list[str]
    # Only campaigns on ONE instrument, budget and optimizer model set, unsteered, with distinct
    # treatments pair
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


class Grader(NamedTuple):
    """The ONE scorer a read grades every row under, the search rows and the bench passes alike."""

    scorer: CellScorer
    scorer_id: str
    # The head-to-head whose declared scorer it is; `None` where no campaign read is an arm.
    record: HeadToHeadRecord | None


class _Graded(NamedTuple):
    row: HeadToHeadRow
    # Each pass's graded rows; `None` where the pass read nothing.
    origin_rows: list[QueryMeasurement] | None
    selected_rows: list[QueryMeasurement] | None
    # Each launch's start and finish, epoch seconds.
    windows: list[tuple[float, float]]


_ORIGIN_READING = "origin_reading"
_OPTIMIZER_MODELS = "optimizer_models"


def _declared(campaigns: list[tuple[Stores, Campaign]]) -> HeadToHeadRecord | None:
    """The record most arms here name, ties to the oldest arm's; ``None`` where no arm is here. An
    arm of another record is a foreign row, never a veto."""
    named = [c.arm.head_to_head_id for _, c in campaigns if c.arm is not None]
    if not named:
        return None
    h2h_id = max(dict.fromkeys(named), key=named.count)
    stores = next(s for s, c in campaigns if c.arm is not None and c.arm.head_to_head_id == h2h_id)
    return stores.campaigns.load_head_to_head(h2h_id)


def comparison_grader(campaigns: list[tuple[Stores, Campaign]]) -> Grader:
    """*campaigns* oldest first. The comparison's one scorer is the one every campaign declares,
    and a head-to-head they are arms of declares it too; campaigns declaring different ones share
    none, and the read refuses rather than grade one under another's formula."""
    graders = {
        scorer_id: scorer
        for scorer, scorer_id in (
            config_cell_scorer(resolve_campaign_config(stores, c, c.root_hop))
            for stores, c in campaigns
        )
    }
    if len(graders) > 1:
        raise ValueError(
            f"These subjects are graded under {len(graders)} scorers ({', '.join(sorted(graders))}) "
            "and share none, so no column reads them as one quantity. Compare subjects one "
            "formula grades."
        )
    ((scorer_id, scorer),) = graders.items()
    record = _declared(campaigns)
    if record is not None and scorer_id != record.instrument.scorer_id:
        raise ValueError(
            f"head-to-head {record.head_to_head_id} declares scorer {record.instrument.scorer_id}; "
            f"its arm's config grades as {scorer_id}"
        )
    return Grader(scorer, scorer_id, record)


def head_to_head(entries: list[HeadToHeadEntry], grader: Grader) -> HeadToHead | None:
    """Entries arrive oldest first, read under the read's one *grader* and against the instrument
    of the head-to-head it declares."""
    if not entries:
        return None
    campaigns = [_campaign(entry) for entry in entries]
    configs = [
        resolve_campaign_config(e.stores, c, c.root_hop)
        for e, c in zip(entries, campaigns, strict=True)
    ]
    scorer, scorer_id, record = grader
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
    if len({tuple(g.row.optimizer_models) for g in graded}) > 1:
        differs_on.append(_OPTIMIZER_MODELS)
    if any(g.row.human_intervened for g in graded):
        differs_on.append("human_intervened")
    if any(
        a.row.bench_set == b.row.bench_set and not _read_alike(a, b)
        for i, a in enumerate(graded)
        for b in graded[i + 1 :]
    ):
        differs_on.append(_ORIGIN_READING)
    ungraded_arms = [
        g.row.campaign_id for g in read if g.row.controlled and g.row.bench_set is None
    ]
    verdict = None if len(graded) < 2 else False if differs_on else None if ungraded_arms else True
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
        headline=BENCH_HEADLINE,
        verdict=verdict,
        differs_on=differs_on,
        pairs=_pairs(graded),
        ratio_reference=None if base is None else base.campaign_id,
        verdict_line=_verdict_line(verdict, len(graded), ungraded_arms),
        uncontrolled_note=None
        if record is None or all(g.row.controlled for g in read)
        else f"Ran as no arm of head-to-head {record.head_to_head_id}, so its search could read "
        "other campaigns' measurements and take an operator's steer.",
        notes=_notes(
            verdict,
            differs_on,
            len(read) - len(graded) - len(ungraded_arms),
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
        "optimizer_incurred_usd_ratio": over(
            lambda r: None if r.spend is None else r.spend.by_kind["optimizer"].incurred_usd
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
    """One bench set, budget and optimizer model set, neither run steered, and their shared origin
    read alike."""
    return (
        a.row.bench_set == b.row.bench_set
        and a.row.bench is not None
        and b.row.bench is not None
        and a.row.budget == b.row.budget
        and a.row.optimizer_models == b.row.optimizer_models
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
    # The rows both SCORED, as `BenchScore.lift` reads them: paired raw, a cell either campaign
    # left ungraded would enter as a 0.0 and shift the pair on an outage.
    b_grades, a_grades = paired_fitness(
        scoreable_rows(b_rows), scoreable_rows(a_rows), grade=COLUMN_GRADE[BENCH_HEADLINE]
    )
    return paired_reading(b_grades, a_grades)


def _campaign(entry: HeadToHeadEntry) -> Campaign:
    campaign = entry.stores.campaigns.load_campaign(entry.reading.campaign_id)
    if campaign is None:
        raise ValueError(
            f"campaign {entry.reading.campaign_id} has no manifest to read its bench from"
        )
    return campaign


def _ending(stores: Stores, campaign: Campaign) -> tuple[StopReason | None, StopOutcome | None]:
    """How the cycle holding the campaign's line ended, off its own index."""
    index = stores.campaigns.load(stores.campaigns.line_holder(campaign.root_hop))
    reason = None if index is None else cycle_ending(index)
    return reason, None if reason is None else stop_reason_outcome(reason)


def _held_budget(stores: Stores, config: CampaignConfig, hop: CycleHop) -> ArmBudget:
    """The line's knobs (an arm's are its record's, ``campaign_config.py::under_record``) under the
    standing ceiling its launch declares. An arm whose cap was moved is off its budget."""
    ceiling, _ = declare_run_ceiling(config, stores=stores, hop=hop, requested=LaunchLimits())
    rounds = stores.campaigns.read_run_limits(hop).rounds
    return config.optimization.arm_budget.model_copy(
        update={
            "usd": ceiling.usd,
            "max_rounds": config.optimization.max_rounds if rounds is None else rounds.max_rounds,
        }
    )


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
    selected = select_optimizer(config.optimization)
    passes = None if result is None else result.bench
    bench = (
        None
        if result is None
        else headline_under(stores, result, config, scorer, scorer_id=scorer_id)
    )
    origin_rows, selected_rows = (
        (None, None) if passes is None else bench_rows(stores, passes, scorer, scorer_id=scorer_id)
    )

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
            origin=origin,
        )
        if (origin := campaign.root_content_hash) is not None
        and partition
        and bench is not None
        and bench.selected is not None
        else None
    )
    cost = None if result is None else result.cost
    budget = _held_budget(stores, config, hop)
    stop_reason, outcome = _ending(stores, campaign)
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
            optimizer_models=sorted({selected.model(node) for node in selected.llm_nodes}),
            arm=campaign.arm,
            # A row's own fact: an arm of the read's record, on its budget and — once graded — its
            # instrument.
            controlled=record is not None
            and campaign.arm is not None
            and campaign.arm.head_to_head_id == record.head_to_head_id
            and budget == record.budget
            and (bench_set is None or bench_set == record.instrument),
            treatment_digest=None if campaign.treatment is None else campaign.treatment.digest,
            budget=budget,
            spend_metered=None
            if spend is None
            else MeteredSpend.of(spend, ceiling_meter(campaign.arm)),
            human_intervened=reading.human_intervened,
            stop_reason=stop_reason,
            outcome=outcome,
            bench=bench,
            headline_lift=HeadlineLift.of(bench),
            bench_missing_reason=None if bench is not None else bench_missing_reason(stop_reason),
            bench_set=bench_set,
            comparable=None,
            spend=spend,
            calls=None if cost is None else cost.calls,
            worked_s=None if cost is None else cost.worked_s,
            rounds=reading.cycle_rounds_scored,
            incurred_usd_ratio=None,
            optimizer_incurred_usd_ratio=None,
            worked_ratio=None,
            lift_per_incurred_usd=None
            if bench is None or spend is None
            else bench.lift_per_usd(spend),
            concurrent_with=[],
            bench_reads=None
            if bench_set is None
            else bench_reads(stores, dataset_name=campaign.dataset_name, sample_ids=bench_ids),
        ),
        origin_rows=origin_rows,
        selected_rows=selected_rows,
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


def _verdict_line(verdict: bool | None, n_graded: int, ungraded_arms: list[str]) -> str:
    if verdict is None and n_graded >= 2:
        return (
            f"No verdict yet: no bench headline for {', '.join(ungraded_arms)}, declared arm(s) "
            "of this head-to-head, so the graded rows are not the comparison that was declared."
        )
    if verdict is None:
        return f"No verdict: a head-to-head needs two campaigns with a graded bench set; {n_graded} here."
    if verdict:
        return (
            "Comparable: one bank, split, held-out row set, scorer, target model, origin, budget "
            "and optimizer model set, so the selected column is one quantity."
        )
    return "Not comparable: these headlines are NOT one quantity, and no pair is read across two."


def _notes(
    verdict: bool | None,
    differs_on: list[str],
    n_outside: int,
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
    if _OPTIMIZER_MODELS in differs_on:
        notes.append(
            "`optimizer_models`: the optimizers called different models, so a gap between two "
            "headlines is the model's as much as the method's."
        )
    if n_outside > 0:
        notes.append(
            f"{n_outside} campaign(s) carry no bench headline — nothing held out, or the "
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
    "Grader",
    "HeadToHead",
    "HeadToHeadEntry",
    "HeadToHeadRow",
    "HeadlineLift",
    "SelectionPair",
    "comparison_grader",
    "head_to_head",
]
