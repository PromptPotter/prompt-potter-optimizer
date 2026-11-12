"""The `evidence` read on a terminal: each table is a label table laid out by one renderer (`_table`)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import get_args

from pydantic import BaseModel

from promptpotter.application.evidence.head_to_head import (
    ArmContrast,
    ContrastArm,
    GuardDifference,
    GuardState,
    HeadToHead,
    HeadToHeadRow,
)
from promptpotter.application.evidence.metric_catalogue import MetricSpec
from promptpotter.application.evidence.read import Evidence
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.application.views.render.primitives import fmt_ci, fmt_pvalue
from promptpotter.domain.arm_reading import (
    level_text,
    lift_band_text,
    lift_interval_text,
    lift_text,
)
from promptpotter.domain.bench import (
    BandedValue,
    BenchColumn,
    BenchScore,
    DatasetSplit,
)
from promptpotter.domain.campaign import BenchSet
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    CellSet,
    PairedReading,
)
from promptpotter.domain.spend import RATE_PRICED_LABEL, SPEND_KIND_LABELS

_ABSENT = "—"


@dataclass(frozen=True)
class Column:
    """One column's label and width; text columns align left and clip, numbers align right."""

    label: str
    width: int
    text: bool = False


type Row = Sequence[str]


def _table(columns: Sequence[Column], rows: Iterable[Row], *, indent: str = "") -> list[str]:
    def line(cells: Iterable[str]) -> str:
        laid = [
            _clip(cell, col.width).ljust(col.width) if col.text else cell.rjust(col.width)
            for col, cell in zip(columns, cells, strict=True)
        ]
        return (indent + "  ".join(laid)).rstrip()

    return [line(col.label for col in columns), *(line(row) for row in rows)]


def _num(value: float | None, spec: str) -> str:
    return _ABSENT if value is None else spec.format(value)


def _count(value: int | None) -> str:
    return _ABSENT if value is None else str(value)


def _mark(differs: bool) -> str:
    return "x" if differs else ""


def _metric_text(value: float | None, metric: MetricSpec) -> str:
    """A value of the read metric; only a lift earns a sign."""
    if value is None:
        return _ABSENT
    return lift_text(value, metric.unit) if metric.lift else level_text(value, metric.unit)


def _metric_ci(lo: float | None, hi: float | None, metric: MetricSpec) -> str:
    if lo is None or hi is None:
        return _ABSENT
    if metric.lift:
        return lift_interval_text(lo, hi, metric.unit)
    return f"[{level_text(lo, metric.unit)}, {level_text(hi, metric.unit)}]"


# --- the roster ---------------------------------------------------------------------------------

_ROSTER = (
    Column("created", 10, text=True),
    Column("kind", 9, text=True),
    Column("", 1, text=True),
    Column("subject", 25, text=True),
    Column("dataset", 18, text=True),
    Column("author", 18, text=True),
    Column("arm", 8, text=True),
    Column("ruler", 8, text=True),
    Column("value", 10),
    Column("95% CI", 20),
    Column("cells", 5),
    Column("unread", 6),
    Column("rounds", 6),
)


def _roster_row(r: SubjectReading, metric: MetricSpec) -> Row:
    # `~` on the kind: a masked channel shares its label with the record it masks, and two rows must not read alike.
    return (
        r.created_at[:10],
        f"{r.kind}~" if r.mask else r.kind,
        {True: "", False: "x", None: "?"}[r.comparable],
        r.label,
        r.dataset_name,
        r.authorship or "-",
        r.arm_id or "-",
        r.ability.ruler.id if r.ability is not None else "-",
        _metric_text(r.value, metric),
        _metric_ci(r.ci_lo, r.ci_hi, metric),
        str(r.n_cells),
        str(len(r.unscorable_cells)),
        str(r.cycle_rounds_scored),
    )


def _roster_lines(ev: Evidence) -> list[str]:
    m = ev.metric
    lines = [
        f"{len(ev.subjects)} subject(s), oldest first, read under {m.spec.axis_label}.",
        m.spec.description,
        f"Each value merges that subject's own cells (the `cells` column); "
        f"{len(m.scored_cells)} cell(s) are shared by all of them, which is what the pairs and "
        "the variance split are over.",
        f"Offered here: {', '.join(s.key for s in m.catalogue) or '-'} — pass --metric KEY, or "
        f"--metric 'expr:<formula>' over {', '.join(m.namespace) or '-'}.",
        "",
        *_table(_ROSTER, (_roster_row(r, m.spec) for r in ev.subjects)),
    ]
    lines += _scenario_lines(ev)
    lines += _winner_chain_lines(ev)
    if ev.unread_subjects:
        lines.append(
            f"\nAsked for but not read: {', '.join(ev.unread_subjects)}. Each either does not "
            "exist under this tenant or has nothing measured at the head it addresses, so it is "
            "absent from every number above rather than counted as a low one."
        )
    if not any(r.values for r in ev.subjects):
        lines.append(
            "\nNo campaign scored a cell under this metric — unavailable for this selection, "
            "which is not the same as a value of zero."
        )
    babysat = [r.label for r in ev.subjects if r.human_intervened]
    if babysat:
        lines.append(
            f"\nAn operator intervened mid-run on: {', '.join(babysat)}. Those cycles are no "
            "longer purely reproducible, whoever authored the searchpoint they read at."
        )
    lines.append(f"\n{ev.comparability.note}")
    off = [r.label for r in ev.subjects if r.comparable is False]
    if ev.comparability.roster_note is not None:
        lines.append(f"Every subject of this selection — {ev.comparability.roster_note}")
    elif off:
        lines.append(
            f"Marked `x` and not comparable to the rest of this selection: {', '.join(off)}. "
            "Their cells still pair where they overlap; their absolute levels do not compare."
        )
    for rep in ev.replicates:
        verdict = (
            "That spread is noise, not an effect."
            if rep.n_instruments == 1
            else (
                f"NOT a replicate: those runs span {rep.n_instruments} measurement identities, so "
                "the arm was held while the instrument moved. Read that spread as engine drift, "
                "and expect no cell of theirs to have replayed."
            )
        )
        lines.append(
            f"\nArm {rep.arm_id[:8]} ran {len(rep.campaign_ids)} times, spread "
            f"{rep.level_spread:+.3f}. {verdict}"
        )
    return lines


def _scenario_lines(ev: Evidence) -> list[str]:
    said = [(r.label, r.scenario) for r in ev.subjects if r.scenario is not None]
    lines = [f"\n{label} {s.label}" for label, s in said]
    lines += sorted({f"  {s.detail}" for _, s in said if s.detail})
    return lines


_CHAIN = (
    Column("round", 5, text=True),
    Column("point", 20, text=True),
    Column("value", 10),
    Column("95% CI", 20),
    Column("cells", 5),
)


def _winner_chain_lines(ev: Evidence) -> list[str]:
    metric = ev.metric.spec
    lines: list[str] = []
    for r in ev.subjects:
        if not r.winner_chain:
            continue
        lines.append(f"\n{r.label} — the branch behind it, origin first:")
        lines += _table(
            _CHAIN,
            (
                (
                    f"r{p.round}",
                    p.label,
                    _metric_text(p.value, metric),
                    _metric_ci(p.ci_lo, p.ci_hi, metric),
                    str(p.n_cells),
                )
                for p in r.winner_chain
            ),
            indent="  ",
        )
    return lines


# --- the head-to-head ---------------------------------------------------------------------------


def _bench_set_text(bench_set: BenchSet | None, field: str) -> str:
    if bench_set is None:
        return _ABSENT
    value = getattr(bench_set, field)
    if isinstance(value, DatasetSplit):
        return f"bench {value.bench}, demo {value.demo}"
    if isinstance(value, CellSet):
        return f"{value.id} ({value.size} cells)"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items())) or _ABSENT
    return _ABSENT if value is None else str(value)


def _axes_text(model: BaseModel | None) -> str:
    # Named axes only, and a named null is a lift: printed, never dropped.
    if model is None:
        return _ABSENT
    return ", ".join(f"{k}={v}" for k, v in model.model_dump().items()) or _ABSENT


def _differing_text(row: HeadToHeadRow, field: GuardDifference) -> str:
    match field:
        case GuardDifference.ORIGIN_READING:
            return _origin_text(row.bench)
        case GuardDifference.BUDGET:
            return (
                f"{_axes_text(row.budget)} · stopping {_axes_text(row.stopping)} · "
                f"clamp {_axes_text(row.determinism)}"
            )
        case GuardDifference.DEFAULTS:
            return row.bench_defaults_pin
        case GuardDifference.OPTIMIZER_MODELS:
            return ", ".join(row.optimizer_models) or _ABSENT
        case GuardDifference.HUMAN_INTERVENED:
            return str(row.human_intervened)
        case _:
            return _bench_set_text(row.bench_set, field.value)


def _h2h_columns(beside: BenchColumn) -> tuple[Column, ...]:
    return (
        Column("", 1, text=True),
        Column("campaign", 24, text=True),
        Column("optimizer", 9, text=True),
        Column("sel", 3),
        Column("selected", 8),
        Column("95% CI", 16),
        Column("origin", 7),
        Column("95% CI", 16),
        Column("lift [95% CI]", 24),
        Column(f"{beside[:4]} lift", 13),
        Column("billed $", 8),
        Column("our rate $", 10),
        Column("incurred $", 10),
        Column("replay", 6),
        Column("cap $", 8),
        Column("tokens", 8),
        Column("calls", 6),
        Column("work s", 7),
        Column("rounds", 6),
        Column("inc/ref", 8),
        Column("opt/ref", 8),
        Column("work/ref", 8),
        Column("lift/$", 11),
        Column("reads", 5),
        Column("ended", 60, text=True),
    )


def _h2h_row(r: HeadToHeadRow, h2h: HeadToHead, beside: BenchColumn) -> Row:
    b = r.bench
    graded = b.graded
    bench: Row = (
        (
            str(graded.selected.round),
            _value(graded.selected.level, "{:.3f}"),
            _band(graded.selected.level, "{:.3f}"),
            _value(graded.origin.level, "{:.3f}"),
            _band(graded.origin.level, "{:.3f}"),
            _lift_text(b, h2h.headline, banded=True),
            _lift_text(b, beside, banded=False),
        )
        if graded is not None
        else (_ABSENT,) * 7
    )
    spend, metered = r.spend, r.spend_metered
    per, headline = b.cost.lift_per_usd, b.vs_origin.headline
    spent: Row = (
        (
            f"{spend.total_used_usd:.4f}",
            f"{spend.total_rate_priced_usd:.4f}",
            f"{spend.total_incurred_usd:.4f}",
        )
        if spend is not None
        else (_ABSENT,) * 3
    )
    return (
        _mark(r.guard.state is GuardState.DIFFERS),
        r.campaign_id,
        r.optimizer,
        *bench,
        *spent,
        _ABSENT
        if metered is None or metered.replay_share is None
        else f"{metered.replay_share:.0%}",
        _ABSENT if metered is None else f"{metered.metered_usd:.4f}",
        _ABSENT if spend is None else str(spend.total_tokens_used),
        _count(r.calls),
        _num(r.worked_s, "{:.0f}"),
        str(r.rounds),
        _num(r.incurred_usd_ratio, "{:.2f}"),
        _num(r.optimizer_incurred_usd_ratio, "{:.2f}"),
        _num(r.worked_ratio, "{:.2f}"),
        _ABSENT if per is None or headline is None else lift_text(per, headline.measurand.unit),
        _count(b.status.reads_before),
        r.status.label,
    )


_BUCKETS = (
    Column("campaign", 24, text=True),
    *(Column(word.lower(), 10) for word in SPEND_KIND_LABELS.values()),
)

_BUCKET_READINGS = (
    ("billed", "used_usd"),
    (RATE_PRICED_LABEL, "rate_priced_usd"),
    ("incurred, replays priced", "incurred_usd"),
)

_CONTRASTS = (
    Column("", 1, text=True),
    Column("contrast (b - a)", 50, text=True),
    Column("shift [95% CI]", 27),
    Column("rows", 4),
    Column("se row", 7),
    Column("se iid", 7),
    Column("deff", 5),
    Column("p", 13),
    Column("p (Holm)", 12),
    Column("q (BH)", 12),
)

_ELECTIONS = (
    Column("campaign", 24, text=True),
    Column("elects on", 9, text=True),
    Column("θ", 7),
    Column("95% CI", 18),
    Column("caveat", 18, text=True),
    Column("quote on", 12, text=True),
)


def _head_to_head_lines(ev: Evidence) -> list[str]:
    if (h2h := ev.head_to_head) is None:
        return []
    beside = next(c for c in get_args(BenchColumn) if c != h2h.headline)
    lines = [
        f"Head-to-head on the held-out bench, oldest first, every row graded by "
        f"`{ev.scorer_id}` and read in {h2h.headline}. {h2h.verdict_line}",
        *(f"  {note}" for note in h2h.notes),
    ]
    if not h2h.covers_selection:
        lines.append(
            "  Not every subject asked for carries a bench headline: the roster below reads the "
            "selection subject by subject."
        )
    differs_on = h2h.guard.differs_on
    shared = next(
        (
            r.bench_set
            for r in h2h.rows
            if r.bench_set is not None and r.guard.state is not GuardState.DIFFERS
        ),
        None,
    )
    if shared is not None and not {d.value for d in differs_on} & set(BenchSet.model_fields):
        lines.append(
            "  bench set: "
            + " · ".join(
                f"{field} {_bench_set_text(shared, field)}" for field in BenchSet.model_fields
            )
        )
    for differing in differs_on:
        lines.append(
            f"  {differing.value} DIFFERS: "
            + "; ".join(f"{r.campaign_id[:22]}={_differing_text(r, differing)}" for r in h2h.rows)
        )
    lines += [
        "",
        f"  /ref divides by {h2h.ratio_reference or _ABSENT}, the oldest run carrying a spend and "
        "a worked clock: total and optimizer-only INCURRED USD, then worked seconds — every "
        "launch of the campaign's line, less its origin gate and unworked time. selected, origin "
        f"and lift are {h2h.headline}; {beside[:4]} lift is the same pairing in {beside}. lift/$ "
        "is the bench lift per USD the SEARCH incurred, the bench's own pass excluded. billed $ is "
        "the providers' REPORTED bill and our rate $ what our rate table prices the calls none "
        "reported one for — never spent; incurred $ is the same calls with every replay priced at "
        "what it would have cost, and replay is the share of the search's incurred USD a replay "
        "answered; cap $ is what the row's budget counts — the search's incurred USD for an arm, "
        "billed plus our rate otherwise. reads counts the individuals graded on those held-out "
        "rows before this selection: each one chosen off a headline spends the holdout. ended is "
        "how the cycle holding the row's line reads now — one that failed ended on no result.",
        *_table(_h2h_columns(beside), (_h2h_row(r, h2h, beside) for r in h2h.rows), indent="  "),
    ]
    lines += [
        f"  {r.campaign_id}: {READING_STATE_INFO[r.bench.status.state].sentence}"
        for r in h2h.rows
        if r.bench.graded is None
    ]
    for reading, field in _BUCKET_READINGS:
        lines += [
            "",
            f"  USD {reading} by bucket — `bench` is the held-out pass that graded the selection:",
            *_table(
                _BUCKETS,
                (
                    (
                        r.campaign_id,
                        *(
                            _ABSENT
                            if r.spend is None
                            else f"{getattr(r.spend.by_kind[k], field):.4f}"
                            for k in SPEND_KIND_LABELS
                        ),
                    )
                    for r in h2h.rows
                ),
                indent="  ",
            ),
        ]
    lines += _election_lines(h2h.rows)
    if h2h.contrasts:
        lines += [
            "",
            f"  The arm-by-arm matrix in {h2h.headline}: an arm is a treatment and its replicates; "
            "shift is the mean over bench rows of b's replicate mean less a's, every replicate of "
            "both having scored the row, its 95% CI and p over rows — an SE clustered by row. "
            "se iid is the same mean's SE were every replicate pairing independent, deff the "
            "squared ratio. Holm gates a claim; BH q is the false-discovery reading. `x` marks a "
            "contrast whose guard differs: read, labelled, and outside the correction:",
            *_table(_CONTRASTS, (_contrast_row(c) for c in h2h.contrasts), indent="  "),
        ]
    lines.append("")
    return lines


def _arm_text(arm: ContrastArm) -> str:
    return f"{arm.optimizer[:9]}:{arm.treatment_digest[:6]}({len(arm.replicates)})"


def _contrast_row(c: ArmContrast) -> Row:
    head = (_mark(c.guard.state is GuardState.DIFFERS), f"{_arm_text(c.a)} -> {_arm_text(c.b)}")
    lift = c.lift
    if lift is None:
        return (*head, *(_ABSENT,) * 8)
    est, family = lift.estimate, lift.family
    floored = "*" if est.p_value <= est.p_floor else " "
    return (
        *head,
        lift_band_text(est, lift.measurand.unit),
        str(len(c.differences)),
        _num(c.se_clustered, "{:.4f}"),
        _num(c.se_naive, "{:.4f}"),
        _num(c.design_effect, "{:.2f}"),
        f"{fmt_pvalue(est.p_value)}{floored}",
        fmt_pvalue(None if family is None else family.p_adjusted),
        fmt_pvalue(c.q_value),
    )


def _election_lines(rows: list[HeadToHeadRow]) -> list[str]:
    elected = [(r, r.election) for r in rows if r.election is not None]
    if not elected:
        return []
    table: list[Row] = []
    for r, election in elected:
        ability = election.ability
        table.append(
            (
                r.campaign_id,
                election.elects_on,
                _ABSENT if ability is None else _num(ability.theta, "{:+.3f}"),
                _ABSENT
                if ability is None
                else fmt_ci(ability.ci_lo, ability.ci_hi, spec="{:+.3f}"),
                _ABSENT if ability is None or ability.caveat is None else ability.caveat.value,
                election.reads_on,
            )
        )
    return [
        "",
        "  Each selection's election, and the column to quote it on: an `ability` election is "
        "won on θ unless its θ carries a caveat, where θ is not ability and it reads on fitness:",
        *_table(_ELECTIONS, table, indent="  "),
    ]


def _lift_text(bench: BenchScore, column: BenchColumn, *, banded: bool) -> str:
    if (measured := bench.lift(column)) is None:
        return _ABSENT
    unit = measured.measurand.unit
    if banded:
        return lift_band_text(measured.estimate, unit)
    return lift_text(measured.estimate.value, unit)


def _value(banded: BandedValue | None, spec: str) -> str:
    return _ABSENT if banded is None else spec.format(banded.value)


def _band(banded: BandedValue | None, spec: str) -> str:
    return _ABSENT if banded is None else fmt_ci(banded.ci_lo, banded.ci_hi, spec=spec)


def _origin_text(bench: BenchScore) -> str:
    if bench.origin is None:
        return _ABSENT
    return f"{bench.origin.sp_hash[:8]} at {_value(bench.origin.level, '{:.3f}')}"


# --- configuration, factors, grid -----------------------------------------------------------


def _config_lines(ev: Evidence) -> list[str]:
    read = [r for r in ev.subjects if r.config is not None]
    bands = ev.config_keys
    if bands is None or len(read) < 2:
        return []
    width = max(26, *(len(r.label[:24]) for r in read))
    columns = (
        Column("key", 28, text=True),
        *(Column(r.label[:24], width, text=True) for r in read),
    )
    return [
        "",
        f"{len(bands.differs)} configured key(s) are set differently across {len(read)} "
        f"searchpoint(s) and {len(bands.one_sided)} are configured by only some; the "
        f"{len(bands.same)} identical are not printed.",
        "",
        *_table(
            columns,
            (
                (key, *_diff_window([(r.config or {}).get(key) for r in read], width))
                for key in [*bands.differs, *bands.one_sided]
            ),
        ),
    ]


def _factor_lines(ev: Evidence) -> list[str]:
    if not ev.factors:
        return []
    unit = ev.metric.spec.unit
    free = [f for f in ev.factors if not f.confounded_with]
    aliased = [f for f in ev.factors if f.confounded_with]
    lines = [
        "",
        f"{len(ev.factors)} factor(s) vary across {len(ev.subjects)} subject(s), "
        f"read on {ev.metric.spec.axis_label}. "
        f"{len(free)} separable, {len(aliased)} aliased by another.",
    ]
    for factor in (*free, *aliased):
        head = f"  {factor.key} [{factor.kind}]"
        if factor.confounded_with:
            head += f"  — ALIASED BY {', '.join(factor.confounded_with)}; this column is theirs"
        lines += [
            "",
            head,
            *_table(
                (
                    Column("level", 38, text=True),
                    Column(unit, 18),
                    Column("cells", 5),
                    Column("subjects", 8),
                ),
                (
                    (
                        level.level,
                        _metric_text(level.value, ev.metric.spec),
                        str(level.n_cells),
                        str(len(level.subjects)),
                    )
                    for level in factor.levels
                ),
                indent="      ",
            ),
        ]
        if factor.note:
            lines.append(f"      {factor.note}")
    return lines


def _grid_lines(ev: Evidence) -> list[str]:
    if (grid := ev.grid) is None:
        return []
    rows = sorted({c.row for c in grid.cells})
    cols = sorted({c.col for c in grid.cells})
    at = {(c.row, c.col): c for c in grid.cells}
    lines = [
        "",
        f"{grid.row_key} x {grid.col_key}, read on {ev.metric.spec.axis_label} — "
        f"{len(grid.cells)} of {len(rows) * len(cols)} coordinates measured, "
        f"in {ev.metric.spec.unit}.",
    ]
    if grid.marginalised:
        lines.append(f"  Marginalised into every cell: {', '.join(grid.marginalised)}.")
    if grid.note:
        lines.append(f"  {grid.note}")
    width = max((len(_clip(c, 16)) for c in cols), default=8)
    return [
        *lines,
        "",
        *_table(
            (Column("", 20, text=True), *(Column(_clip(c, 16), width) for c in cols)),
            (
                (
                    row,
                    *(
                        _ABSENT
                        if (cell := at.get((row, col))) is None or cell.value is None
                        else _metric_text(cell.value, ev.metric.spec)
                        for col in cols
                    ),
                )
                for row in rows
            ),
            indent="  ",
        ),
    ]


def _diff_window(values: list[str | None], width: int) -> list[str]:
    """Opens at the first divergence: two L1 edits share a long prefix, and a window from zero renders them alike."""
    flat = [None if v is None else " ".join(v.split()) for v in values]
    present = [v for v in flat if v is not None]
    start = 0
    if len(present) > 1:
        shortest = min(len(v) for v in present)
        while start < shortest and len({v[start] for v in present}) == 1:
            start += 1
        start = max(0, start - 8)
    return [_clip(v, width, start) for v in flat]


def _clip(value: str | None, width: int, start: int = 0) -> str:
    if value is None:
        return _ABSENT
    head = "…" if start else ""
    tail = value[start:]
    room = width - len(head)
    return f"{head}{tail}" if len(tail) <= room else f"{head}{tail[: room - 1]}…"


# --- pairs, variance, environments, ranking -----------------------------------------------------

_PAIRS = (
    Column("pair (b - a)", 40, text=True),
    Column("lift [95% CI]", 33),
    Column("n", 3),
    Column("p", 17),
    Column("p (Holm)", 16),
)


def _pair_row(label: str, pair: PairedReading) -> Row:
    lift = pair.headline
    if lift is None or pair.coverage is None:
        return (label, READING_STATE_INFO[pair.state].sentence, *(_ABSENT,) * 3)
    est = lift.estimate
    floored = "*" if est.p_value <= est.p_floor else " "
    return (
        label,
        lift_band_text(est, lift.measurand.unit),
        str(pair.coverage.scored),
        f"{fmt_pvalue(est.p_value)}{floored}",
        fmt_pvalue(None if lift.family is None else lift.family.p_adjusted),
    )


def _pairwise_lines(ev: Evidence) -> list[str]:
    m = ev.metric
    if not m.pairwise:
        return ["", "A pairwise comparison needs two subjects."]
    return [
        "",
        *_table(
            _PAIRS,
            (_pair_row(f"{p.subject_a[-8:]} -> {p.subject_b[-8:]}", p.reading) for p in m.pairwise),
        ),
        "",
        f"Holm corrects across the {m.n_tests} comparison(s) in this table. It does NOT correct "
        "across metrics: if you tried several and kept the tightest, the interval you are reading "
        "is optimistic by an amount nothing here can compute.",
        "The lift is the mean paired difference and its interval Student-t. `*` marks a p below "
        "what an exact sign test can reach on the cells that differ: the reading claims more than "
        "that many cells can show.",
    ]


def _variance_lines(ev: Evidence) -> list[str]:
    v = ev.variance
    if v is None:
        return [
            "",
            "Variance: needs two subjects sharing two cells; nothing to decompose. Subjects on "
            "different datasets never share a cell, which is why a mixed selection stops here.",
        ]
    verdict = (
        "so nothing here is distinguishable from noise"
        if v.subject_sd_below_noise
        else "so the subjects differ by more than noise alone would produce"
    )
    unit = ev.metric.spec.unit

    def sd(value: float) -> str:
        return level_text(value, unit)

    lines = [
        "",
        f"Variance over the {v.n_cells} cell(s) all {v.n_subjects} subjects measured:",
        f"  cell effect {sd(v.cell_effect_sd)} | "
        f"subject effect {sd(v.subject_effect_sd)} | residual {sd(v.residual_sd)}",
        f"  under the null a subject mean still scatters by "
        f"{sd(v.null_subject_scatter)} — {verdict}.",
    ]
    if ev.power is not None:
        p = ev.power
        needed = "-" if p.cells_for_largest_gap is None else str(p.cells_for_largest_gap)
        lines += [
            "",
            f"Resolving power at {p.cells_per_subject} cells/subject: paired SE "
            f"{sd(p.paired_se)}, smallest detectable effect "
            f"{sd(p.min_detectable_effect)}.",
            f"  the widest gap on the roster is {sd(p.largest_subject_gap)}; resolving it "
            f"would take ~{needed} cells per subject.",
        ]
    oc = ev.order_confound
    if oc is not None and oc.level_vs_order is not None:
        note = (
            " — the roster's ordering IS its chronology, so arm and run date cannot be separated"
            if oc.order_confounded
            else ""
        )
        spend = "" if oc.spend_vs_order is None else f", spend vs order {oc.spend_vs_order:+.2f}"
        lines += [
            "",
            f"Run-order confound: value vs order rho {oc.level_vs_order:+.2f}{spend}{note}.",
        ]
    return lines


_ENVIRONMENTS = (
    Column("cell", 44, text=True),
    Column("held", 4),
    Column("n", 3),
    Column("spread", 8),
    Column("signal SD", 9),
    Column("SNR", 6),
    Column("best", 28, text=True),
)

_BREADTH = (
    Column("subject", 44, text=True),
    Column("lifted", 6),
    Column("flat", 4),
    Column("dropped", 7),
    Column("of", 4),
)


def _environment_lines(ev: Evidence) -> list[str]:
    if not ev.environments:
        return []
    unit = ev.metric.spec.unit
    held = sum(1 for e in ev.environments if e.held_out)
    lines = [
        "",
        f"{len(ev.environments)} environment(s), {held} of them held out — each cell as the block "
        f"the subjects are compared within, on {ev.metric.spec.axis_label}. SNR is the subjects' "
        "spread at that cell over the roster's residual SD: the cells that tell the subjects apart. "
        "A held-out cell reads the subjects' bench passes, which only a round's selection has.",
        *_table(
            _ENVIRONMENTS,
            (
                (
                    e.cell,
                    "yes" if e.held_out else "",
                    str(e.n_subjects),
                    level_text(e.spread, unit),
                    level_text(e.signal_sd, unit),
                    _num(e.snr, "{:.2f}"),
                    _ABSENT if e.best_subject is None else e.best_subject[-28:],
                )
                for e in ev.environments
            ),
            indent="  ",
        ),
    ]
    if (ra := ev.rank_agreement) is not None:
        ranked = sorted(ra.mean_rank.items(), key=lambda kv: kv[1])
        lines += [
            "",
            f"Rank agreement over the {ra.n_cells} cell(s) all {ra.n_subjects} subjects scored: "
            f"Friedman chi2 {ra.statistic:.2f} on {ra.df} df, p {fmt_pvalue(ra.p_value)}; "
            f"Kendall's W {ra.kendall_w:.2f}.",
            "  mean rank, 1 best: " + ", ".join(f"{k[-28:]} {v:.2f}" for k, v in ranked),
        ]
    if ev.lift_profiles:
        lines += [
            "",
            "Breadth — of each subject's own cells, how many it lifted, left flat, dropped:",
            *_table(
                _BREADTH,
                (
                    (p.key[-44:], str(p.lifted), str(p.flat), str(p.dropped), str(p.n_cells))
                    for p in ev.lift_profiles
                ),
                indent="  ",
            ),
        ]
    return lines


_EDITS = (
    Column("lift", 14),
    Column("95% CI", 20),
    Column("cells", 5),
    Column("", 1, text=True),
    Column("campaign", 10, text=True),
    Column("edit", 60, text=True),
)


def _ranking_lines(ev: Evidence, top: int) -> list[str]:
    m = ev.metric
    if not ev.ranking_computed:
        return [
            "",
            "Edit ranking not computed — pass `--ranking`. It is the widest walk here: "
            "everything above reads one round-0 document per campaign, while this opens every "
            "round of every campaign selected, and ranks each searchpoint against its own "
            "campaign's origin.",
        ]
    if not ev.edits:
        return [
            "",
            "No scored edits: one needs its campaign's round-0 origin plus a second searchpoint "
            "measured on cells that origin also scored.",
        ]
    unit = m.spec.unit
    rows: list[Row] = []
    unranked: list[str] = []
    for c in ev.edits[:top]:
        lift, coverage = c.reading.headline, c.reading.coverage
        if lift is None or coverage is None:
            unranked.append(
                f"unranked {c.campaign_id[:10]} {c.label}: "
                f"{READING_STATE_INFO[c.reading.state].sentence}"
            )
            continue
        est = lift.estimate
        rows.append(
            (
                lift_text(est.value, unit),
                lift_interval_text(est.ci_lo, est.ci_hi, unit),
                str(coverage.scored),
                "" if est.side == "spans" else "*",
                c.campaign_id,
                c.label,
            )
        )
    spread = ev.spread
    return [
        "",
        f"{len(ev.edits)} searchpoint(s) ranked by how much each beat its OWN campaign's origin "
        f"on the same cells, in {m.spec.label}. Identity is `sp_hash`, so a prompt-only edit "
        "ranks like any other; nothing pools across campaigns, which share no anchor to pool on.",
        "",
        *_table(_EDITS, rows),
        *unranked,
        "",
        f"Spread across the {spread.n_edits} ranked edit(s): SD "
        f"{level_text(spread.edit_effect_sd, unit)}"
        if spread.edit_effect_sd is not None
        else f"Spread across the {spread.n_edits} ranked edit(s): one reading has none.",
        "",
        "* the interval excludes zero. Everything else is consistent with no effect — the "
        "ranking orders them, it does not endorse them.",
        "To settle one, deepen it rather than repeat it: `verify` re-scores a candidate on "
        "more samples and records the result without touching the cycle.",
    ]


def evidence_lines(ev: Evidence, *, top: int) -> list[str]:
    """Every section of the read, in the order a terminal prints them."""
    return [
        *_head_to_head_lines(ev),
        *_roster_lines(ev),
        *_factor_lines(ev),
        *_grid_lines(ev),
        *_config_lines(ev),
        *_pairwise_lines(ev),
        *_variance_lines(ev),
        *_environment_lines(ev),
        *_ranking_lines(ev, top),
    ]
