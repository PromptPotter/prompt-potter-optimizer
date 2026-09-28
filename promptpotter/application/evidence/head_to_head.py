"""Each campaign's bench headline in one table, guarded by whether the bench sets are one quantity.
The held-out set IS the comparability guard: `docs/architecture.md` § The bench score."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.domain.bench import BenchReading, BenchScore, DatasetSplit
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.run_records import WallClock
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.archive_queries import load_run
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.statistics import holm_adjusted, paired_reading

if TYPE_CHECKING:
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores


class BenchSet(StrictModel):
    """What one campaign's held-out set IS; two headlines are one quantity only if all of it agrees.
    A differing field is named in ``HeadToHead.differs_on``."""

    dataset_name: str
    # The bank's rows, order-independent: two banks re-cut under one name share ids, not content.
    dataset_hash: str | None
    split: DatasetSplit | None
    # A digest of the held-out ids `bank_partition.json` names — what the split and its seed drew.
    bench_rows: str
    # Hashes the cell formula and every constant the compiler applies: it alone names the grader.
    scorer_id: str
    # node -> model at the origin: the target every selection's bench pass ran through.
    models: dict[str, str]


class HeadToHeadRow(StrictModel):
    """One campaign's bench headline beside what it cost to reach."""

    subject: str
    campaign_id: str
    optimizer: str
    # `None` = no headline: nothing held out, or the cycle stopped before its selection was graded.
    bench: BenchScore | None
    bench_set: BenchSet | None
    # Against the row most others share an instrument with; `None` for a row with no headline.
    comparable: bool | None
    spend: SpendRollup | None
    wall_clock_s: float | None
    rounds: int
    # Each over `HeadToHead.ratio_reference`'s, `None` where either lacks it. INCURRED, never the
    # bill: an arm replaying a sibling's cells is billed nothing for them.
    incurred_usd_ratio: float | None
    # The `loop` bucket alone — the optimizer's own calls, the spend arms differ on by manifest.
    loop_incurred_usd_ratio: float | None
    wall_clock_ratio: float | None
    # Bench lift per incurred USD; `None` without a lift or a price.
    lift_per_incurred_usd: float | None
    # Campaigns whose run overlapped this one's: while both ran they shared the content-addressed
    # cache, so a cell's bill and clock went to whichever reached it first.
    concurrent_with: list[str]


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
    # `None` below two graded rows: one headline is compared with nothing.
    verdict: bool | None
    # `BenchSet` fields, plus `origin_reading` where campaigns on one set read one origin apart.
    differs_on: list[str]
    # Only campaigns on ONE instrument pair (`_one_instrument`); any other pair is refused.
    pairs: list[SelectionPair]
    # The oldest row carrying a spend and a wall clock, which every row's `*_ratio` divides by.
    ratio_reference: str | None
    note: str


class HeadToHeadEntry(NamedTuple):
    """One campaign subject, with the tree it lives in and its root cycle."""

    reading: SubjectReading
    stores: Stores
    cycle_dir: Path


class _Graded(NamedTuple):
    row: HeadToHeadRow
    # Each pass's archived rows on THIS campaign's held-out ids; `None` where the run is gone.
    origin_rows: list[dict[str, Any]] | None
    selected_rows: list[dict[str, Any]] | None
    # The cycle's own start and finish, epoch seconds; `None` before it finished.
    window: tuple[float, float] | None
    # Ran beside another reading of this origin in this read: the archive keeps one row per cell,
    # so the rows its headline was read off may have been superseded by the other's.
    raced: bool


_ORIGIN_READING = "origin_reading"


def head_to_head(entries: list[HeadToHeadEntry]) -> HeadToHead | None:
    if not entries:
        return None
    read = [_read(entry) for entry in entries]
    read = [
        g._replace(raced=any(o is not g and _same_origin(g, o) and _overlapped(g, o) for o in read))
        for g in read
    ]
    base = next(
        (g.row for g in read if g.row.spend is not None and g.row.wall_clock_s is not None), None
    )
    graded = [g for g in read if g.row.bench is not None and g.row.bench_set is not None]
    # The row most others share an instrument with; ties go to the oldest.
    reference = max(graded, key=lambda g: sum(_one_instrument(g, o) for o in graded), default=None)
    differs_on = sorted(
        field
        for field in BenchSet.model_fields
        if len({repr(getattr(g.row.bench_set, field)) for g in graded}) > 1
    )
    if any(
        a.row.bench_set == b.row.bench_set and not _one_instrument(a, b)
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
                    if reference is None or g.row.bench is None or g.row.bench_set is None
                    else _one_instrument(g, reference),
                    "concurrent_with": concurrent[g.row.campaign_id],
                    **_ratios(g.row, base),
                }
            )
            for g in read
        ],
        verdict=verdict,
        differs_on=differs_on,
        pairs=_pairs(graded),
        ratio_reference=None if base is None else base.campaign_id,
        note=_note(
            verdict,
            differs_on,
            len(graded),
            len(read),
            [cid for cid, others in concurrent.items() if others],
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
        "wall_clock_ratio": over(lambda r: r.wall_clock_s),
    }


def _overlapped(a: _Graded, b: _Graded) -> bool:
    if a.window is None or b.window is None:
        return False
    return a.window[0] < b.window[1] and b.window[0] < a.window[1]


def _origin(g: _Graded) -> BenchReading | None:
    return None if g.row.bench is None else g.row.bench.origin


def _same_origin(a: _Graded, b: _Graded) -> bool:
    oa, ob = _origin(a), _origin(b)
    if oa is None or ob is None or a.row.bench_set != b.row.bench_set:
        return False
    return oa.sp_hash == ob.sp_hash


def _one_instrument(a: _Graded, b: _Graded) -> bool:
    """One bench set, and a shared origin read alike, row by row and in its headline — a replayed
    row keeps its old grade, so a grader that moved shows in the headline alone."""
    if a.row.bench_set != b.row.bench_set or a.row.bench is None or b.row.bench is None:
        return False
    oa, ob = _origin(a), _origin(b)
    if oa is None or ob is None:
        return True
    if oa.sp_hash != ob.sp_hash or a.origin_rows is None or b.origin_rows is None:
        return True
    _gap, lo, hi, _p, _n = _paired(a.origin_rows, b.origin_rows)
    if lo is None or hi is None:
        return True
    headline_gap = (
        0.0
        if oa.composite_fitness is None or ob.composite_fitness is None
        else ob.composite_fitness - oa.composite_fitness
    )
    if not lo <= 0.0 <= hi:
        return False
    if a.raced or b.raced:
        # A raced headline was read LIVE off rows the archive may no longer hold, so the gap is
        # backend noise while it stays inside both readings' own bands.
        return _within_bands(headline_gap, oa, ob)
    return lo <= headline_gap <= hi


def _within_bands(gap: float, a: BenchReading, b: BenchReading) -> bool:
    if a.ci_lo is None or a.ci_hi is None or b.ci_lo is None or b.ci_hi is None:
        return True
    return abs(gap) <= math.hypot(a.ci_hi - a.ci_lo, b.ci_hi - b.ci_lo) / 2


def _paired(
    a_rows: list[dict[str, Any]], b_rows: list[dict[str, Any]]
) -> tuple[float, float | None, float | None, float | None, int]:
    b_grades, a_grades = paired_fitness(
        scoreable_rows(cast("list[QueryMeasurement]", b_rows)),
        scoreable_rows(cast("list[QueryMeasurement]", a_rows)),
        grade="objective",
    )
    return paired_reading(b_grades, a_grades)


def _read(entry: HeadToHeadEntry) -> _Graded:
    """A cross-campaign survey, so every document is read tolerant: a campaign still running has no
    ``final`` block, and one that never finished has no headline rather than failing the read."""
    reading, stores, cycle_dir = entry
    layout = CycleLayout(cycle_dir)
    campaign = stores.campaigns.load_campaign(reading.campaign_id)
    if campaign is None:
        raise ValueError(f"campaign {reading.campaign_id} has no manifest to read its bench from")
    config = resolve_campaign_config(stores, campaign, campaign.root_hop)
    index = read_json_tolerant(layout.manifest, {})
    final = index.get("final") or {}
    bench = BenchScore.model_validate(final["bench"]) if final.get("bench") else None
    partition = read_json_tolerant(layout.bank_partition, {})
    bench_ids = frozenset(int(i) for i in partition.get("bench_ids") or [])
    origin_params = read_json_tolerant(layout.round_file(0), {}).get("pipeline_params")
    bench_set = (
        BenchSet(
            dataset_name=campaign.dataset_name,
            dataset_hash=read_json_tolerant(layout.export, {}).get("dataset_hash"),
            split=DatasetSplit.model_validate(partition["split"]) if partition["split"] else None,
            bench_rows=hashlib.sha256(
                ",".join(str(i) for i in sorted(bench_ids)).encode()
            ).hexdigest()[:12],
            # The run's stamp, never `config.scoring`: that resolves over the LIVE dataset file,
            # which may have moved since this bench was graded.
            scorer_id=final["scorer_id"],
            models={
                node: str(cfg["model"])
                for node, cfg in node_config_items(origin_params)
                if "model" in cfg
            },
        )
        if partition and bench is not None and bench.selected is not None
        else None
    )
    spend_doc = read_json_tolerant(layout.dashboard, {}).get("spend")
    spend = SpendRollup.model_validate(spend_doc) if spend_doc else None
    clock = final.get("wall_clock")
    start, end = epoch_seconds(final.get("started_at")), epoch_seconds(final.get("finished_at"))

    def rows_of(run_id: str) -> list[dict[str, Any]] | None:
        # A content-addressed run log collects every pass that shared its config, so only this
        # campaign's own held-out ids are its rows.
        run = load_run(stores, run_id)
        return (
            None
            if run is None
            else [r for r in run["measurements"] if r.get("sample_id") in bench_ids]
        )

    return _Graded(
        row=HeadToHeadRow(
            subject=reading.key,
            campaign_id=reading.campaign_id,
            optimizer=config.optimization.optimizer,
            bench=bench,
            bench_set=bench_set,
            comparable=None,
            spend=spend,
            wall_clock_s=WallClock.model_validate(clock).elapsed_s if clock else None,
            rounds=reading.cycle_rounds_scored,
            incurred_usd_ratio=None,
            loop_incurred_usd_ratio=None,
            wall_clock_ratio=None,
            lift_per_incurred_usd=None
            if bench is None or spend is None
            else bench.lift_per_usd(spend.total_incurred_usd),
            concurrent_with=[],
        ),
        origin_rows=None if bench is None or bench.origin is None else rows_of(bench.origin.run_id),
        selected_rows=(
            None if bench is None or bench.selected is None else rows_of(bench.selected.run_id)
        ),
        window=None if start is None or end is None else (start, end),
        raced=False,
    )


def _pairs(graded: list[_Graded]) -> list[SelectionPair]:
    out: list[SelectionPair] = []
    for i, a in enumerate(graded):
        for b in graded[i + 1 :]:
            if not _one_instrument(a, b) or a.selected_rows is None or b.selected_rows is None:
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


def _note(
    verdict: bool | None,
    differs_on: list[str],
    n_graded: int,
    n_rows: int,
    concurrent: list[str],
) -> str:
    tail = (
        f" {n_rows - n_graded} campaign(s) carry no bench headline — nothing held out, or the "
        "cycle stopped before its selection was graded — and sit outside the verdict."
        if n_rows > n_graded
        else ""
    ) + (
        f" {', '.join(concurrent)} ran concurrently on one content-addressed cache: the first to "
        "reach a cell paid and the rest replayed it, so the bill and the wall clock split by "
        "arrival. The USD ratios price INCURRED spend, which counts a replay as paid; the "
        "wall-clock ratio stays confounded."
        if concurrent
        else ""
    )
    if verdict is None:
        return (
            f"A head-to-head needs two campaigns with a graded bench set; {n_graded} here." + tail
        )
    if verdict:
        return (
            "Bench set IDENTICAL — one bank, one split, the same held-out rows, one scorer and one "
            "target model — so the selected column is one quantity and every pair below is read on "
            "the same rows." + tail
        )
    drift = (
        " `origin_reading`: campaigns on one bench set read the same origin apart beyond its own "
        "noise, so the grader or the target moved between them — a change no stamp names."
        if _ORIGIN_READING in differs_on
        else ""
    )
    return (
        f"Bench DIFFERS on {', '.join(differs_on)}, so these headlines are NOT one quantity. A "
        "row off the most shared instrument is marked; no pair is read across two." + drift + tail
    )


__all__ = [
    "BenchSet",
    "HeadToHead",
    "HeadToHeadEntry",
    "HeadToHeadRow",
    "SelectionPair",
    "head_to_head",
]
