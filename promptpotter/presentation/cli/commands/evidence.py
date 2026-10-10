from __future__ import annotations

import argparse
import logging
from typing import get_args

from promptpotter.application.evidence.head_to_head import GuardState, HeadToHeadRow
from promptpotter.application.evidence.metric_catalogue import MEASURAND, MetricUnit
from promptpotter.application.evidence.read import Evidence, select_evidence
from promptpotter.application.views.render.primitives import fmt_ci, fmt_pvalue
from promptpotter.config.logging import setup_logging
from promptpotter.domain.bench import BandedValue, BenchColumn, BenchScore, DatasetSplit
from promptpotter.domain.campaign import ArmBudget, BenchSet
from promptpotter.domain.paired_reading import READING_STATE_INFO, LiftEstimate, PairedReading
from promptpotter.domain.spend import RATE_PRICED_LABEL, SPEND_KIND_LABELS
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores

logger = logging.getLogger("promptpotter.presentation.cli")

# Only a `delta` earns a leading `+`: on a level or a ratio it dresses the value as a lift.
_UNIT_SPEC: dict[MetricUnit, str] = {
    "level": "{:.3f}",
    "delta": "{:+.3f}",
    "seconds": "{:.1f}",
    "usd": "{:.4f}",
    "tokens": "{:.0f}",
    "rank": "{:.2f}",
    "rounds": "{:.1f}",
    "composed": "{:.3f}",
}

# Fails at import: a `MetricUnit` with no format is otherwise a KeyError mid-table on the operator's terminal.
_unformatted = sorted(set(get_args(MetricUnit)) - set(_UNIT_SPEC))
if _unformatted:
    raise RuntimeError(f"MetricUnit members with no terminal format: {_unformatted}")
del _unformatted


def _roster_lines(ev: Evidence) -> list[str]:
    m = ev.metric
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        f"{len(ev.subjects)} subject(s), oldest first, read under {m.spec.axis_label}.",
        m.spec.description,
        f"Each value merges that subject's own cells (the `cells` column); "
        f"{len(m.scored_cells)} cell(s) are shared by all of them, which is what the pairs and "
        "the variance split are over.",
        f"Offered here: {', '.join(s.key for s in m.catalogue) or '-'} — pass --metric KEY, or "
        f"--metric 'expr:<formula>' over {', '.join(m.namespace) or '-'}.",
        "",
        f"{'created':<10}  {'kind':<9}  {'subject':<26}  {'dataset':<18}  {'author':<18}  "
        f"{'arm':<8}  {'ruler':<8}  {'value':>10}  {'95% CI':>20}  {'cells':>5}  {'unread':>6}  "
        f"{'rounds':>6}",
    ]
    for r in ev.subjects:
        value = "         ." if r.value is None else f"{spec.format(r.value):>10}"
        ruler = (r.ability.ruler_id if r.ability is not None else None) or "-"
        mark = {True: " ", False: "x", None: "?"}[r.comparable]
        # `~` on the kind: a masked channel shares its label with the record it masks, and two rows must not read alike.
        kind = f"{r.kind}~" if r.mask else r.kind
        lines.append(
            f"{r.created_at[:10]:<10}  {kind:<9}  {mark}{r.label[:25]:<25}  "
            f"{r.dataset_name[:18]:<18}  {(r.authorship or '-')[:18]:<18}  "
            f"{(r.arm_id or '-')[:8]:<8}  {ruler[:8]:<8}  {value}  "
            f"{fmt_ci(r.ci_lo, r.ci_hi, spec=spec):>20}  {r.n_cells:>5}  "
            f"{len(r.unscorable_cells):>6}  {r.cycle_rounds_scored:>6}"
        )
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


def _bench_set_text(bench_set: BenchSet | None, field: str) -> str:
    if bench_set is None:
        return "—"
    value = getattr(bench_set, field)
    if isinstance(value, DatasetSplit):
        return f"bench {value.bench}, demo {value.demo}, seed {value.seed}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items())) or "—"
    return "—" if value is None else str(value)


def _differing_text(row: HeadToHeadRow, field: str) -> str:
    if field in BenchSet.model_fields:
        return _bench_set_text(row.bench_set, field)
    if field == "origin_reading":
        return _origin_text(row.bench)
    value = getattr(row, field)
    if isinstance(value, ArmBudget):
        return ", ".join(f"{k}={v}" for k, v in value.model_dump().items() if v is not None) or "—"
    if isinstance(value, list):
        return ", ".join(value) or "—"
    return str(value)


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
    differs_on = [d.value for d in h2h.guard.differs_on]
    shared = next(
        (
            r.bench_set
            for r in h2h.rows
            if r.bench_set is not None and r.guard.state is not GuardState.DIFFERS
        ),
        None,
    )
    if shared is not None and not set(differs_on) & set(BenchSet.model_fields):
        lines.append(
            "  bench set: "
            + " · ".join(
                f"{field} {_bench_set_text(shared, field)}" for field in BenchSet.model_fields
            )
        )
    for field in differs_on:
        lines.append(
            f"  {field} DIFFERS: "
            + "; ".join(f"{r.campaign_id[:22]}={_differing_text(r, field)}" for r in h2h.rows)
        )
    lines += [
        "",
        f"  /ref divides by {h2h.ratio_reference or '—'}, the oldest run carrying a spend and a "
        "worked clock: total and optimizer-only INCURRED USD, then worked seconds — every "
        "launch of the campaign's line, less its origin gate and unworked time. selected, origin "
        f"and lift are {h2h.headline}; {beside[:4]} lift is the same pairing in {beside}. lift/$ "
        "is the bench lift per USD the SEARCH incurred, the bench's own pass excluded. billed $ is the "
        "providers' REPORTED bill and our rate $ what our rate table prices the calls none "
        "reported one for — never spent; incurred $ is the same calls with every replay priced at what it would "
        "have cost, and replay is the share of the search's incurred USD a replay answered; "
        "cap $ is what the row's budget counts — the search's incurred USD for "
        "an arm, billed plus our rate otherwise. reads counts the individuals graded on those held-out "
        "rows before this selection: each one chosen off a headline spends the holdout. ended is how the cycle holding "
        "the row's line reads now — one that failed ended on no result.",
        f"  {'campaign':<24}  {'optimizer':<9}  {'sel':>3}  {'selected':>8}  {'95% CI':>16}  "
        f"{'origin':>7}  {'95% CI':>16}  {'lift':>7}  {'95% CI':>18}  {beside[:4] + ' lift':>9}  {'billed $':>8}  "
        f"{'our rate $':>10}  {'incurred $':>10}{'replay':>6}  {'cap $':>8}  {'tokens':>8}  {'calls':>6}  {'work s':>7}  {'rounds':>6}  "
        f"{'inc/ref':>8}  {'opt/ref':>8}  {'work/ref':>8}  {'lift/$':>7}  {'reads':>5}  ended",
    ]
    for r in h2h.rows:
        mark = "x" if r.guard.state is GuardState.DIFFERS else " "
        b = r.bench
        graded = b.graded
        reads = b.status.reads_before
        spend = r.spend
        per_usd = b.cost.lift_per_usd
        metered = r.spend_metered
        cap = "—" if metered is None else f"{metered.metered_usd:.4f}"
        replay = (
            "—"
            if metered is None or metered.replay_share is None
            else f"{metered.replay_share:.0%}"
        )
        lines.append(
            f" {mark}{r.campaign_id[:24]:<24}  {r.optimizer[:9]:<9}  "
            + (
                f"{graded.selected.round:>3}  {_banded(graded.selected.level, '{:.3f}', 8, 16)}  "
                f"{_banded(graded.origin.level, '{:.3f}', 7, 16)}  "
                f"{_banded(_lift(b, h2h.headline), '{:+.3f}', 7, 18)}  "
                f"{_value(_lift(b, beside), '{:+.3f}'):>9}  "
                if graded is not None
                else f"{b.status.sentence:<106.106}  "
            )
            + (
                f"{spend.total_used_usd:>8.4f}  {spend.total_rate_priced_usd:>10.4f}  "
                f"{spend.total_incurred_usd:>10.4f}  {replay:>6}  "
                f"{cap:>8}  {spend.total_tokens_used:>8}  "
                if spend is not None
                else f"{'—':>8}  {'—':>10}  {'—':>10}  {'—':>6}  {'—':>8}  {'—':>8}  "
            )
            + f"{'—' if r.calls is None else r.calls:>6}  "
            + f"{'—' if r.worked_s is None else f'{r.worked_s:.0f}':>7}  {r.rounds:>6}  "
            + "  ".join(
                f"{'—' if x is None else f'{x:.2f}':>8}"
                for x in (r.incurred_usd_ratio, r.optimizer_incurred_usd_ratio, r.worked_ratio)
            )
            + f"  {'—' if per_usd is None else f'{per_usd:+.2f}':>7}"
            + f"  {'—' if reads is None else reads:>5}"
            + f"  {r.status.label[:60]}"
        )
    for reading, field in (
        ("billed", "used_usd"),
        (RATE_PRICED_LABEL, "rate_priced_usd"),
        ("incurred, replays priced", "incurred_usd"),
    ):
        lines += [
            "",
            f"  USD {reading} by bucket — `bench` is the held-out pass that graded the selection:",
            f"  {'campaign':<24}"
            + "".join(f"  {w.lower():>10}" for w in SPEND_KIND_LABELS.values()),
        ]
        for r in h2h.rows:
            spend = r.spend
            lines.append(
                f"  {r.campaign_id[:24]:<24}"
                + "".join(
                    f"  {'—' if spend is None else f'{getattr(spend.by_kind[k], field):.4f}':>10}"
                    for k in SPEND_KIND_LABELS
                )
            )
    if h2h.pairs:
        lines += [
            "",
            f"  selection b - selection a on the bench rows both scored, in {h2h.headline} — "
            "the lift column's arithmetic with the origin replaced by a. `x` marks a pair whose "
            "guard differs: read, labelled, and outside the Holm correction:",
            f"  {'pair (b - a)':<50}  {'shift':>7}  {'95% CI':>18}  {'n':>4}  {'p':>12}  "
            f"{'p (Holm)':>12}",
        ]
        for p in h2h.pairs:
            pair = p.reading
            if pair.a is None or pair.b is None:
                continue
            first, second = (m.address.path[-1].campaign_id for m in (pair.a, pair.b))
            mark = "x" if p.guard.state is GuardState.DIFFERS else " "
            label = f"{first[:23]} -> {second[:23]}"
            lines.append(f" {mark}{label:<50}  {_pair_text(pair, '{:+.3f}', 7, 18, 4, 12)}")
    lines.append("")
    return lines


def _pair_text(
    pair: PairedReading, spec: str, width: int, ci_width: int, n_width: int, p_width: int
) -> str:
    lift = pair.headline
    if lift is None or pair.coverage is None:
        return READING_STATE_INFO[pair.state].sentence
    est = lift.estimate
    floored = "*" if est.p_value <= est.p_floor else " "
    return (
        f"{spec.format(est.value):>{width}}  "
        f"{fmt_ci(est.ci_lo, est.ci_hi, spec=spec):>{ci_width}}  "
        f"{pair.coverage.scored:>{n_width}}  {fmt_pvalue(est.p_value):>{p_width}}{floored} "
        f"{fmt_pvalue(None if lift.family is None else lift.family.p_adjusted):>{p_width}}"
    )


def _lift(bench: BenchScore, column: BenchColumn) -> LiftEstimate | None:
    measured = bench.lift(column)
    return None if measured is None else measured.estimate


def _value(banded: BandedValue | LiftEstimate | None, spec: str) -> str:
    return "—" if banded is None else spec.format(banded.value)


def _banded(banded: BandedValue | LiftEstimate | None, spec: str, width: int, ci_width: int) -> str:
    ci = "—" if banded is None else fmt_ci(banded.ci_lo, banded.ci_hi, spec=spec)
    return f"{_value(banded, spec):>{width}}  {ci:>{ci_width}}"


def _origin_text(bench: BenchScore) -> str:
    if bench.origin is None:
        return "—"
    return f"{bench.origin.sp_hash[:8]} at {_value(bench.origin.level, '{:.3f}')}"


def _config_lines(ev: Evidence) -> list[str]:
    read = [r for r in ev.subjects if r.config is not None]
    bands = ev.config_keys
    if bands is None or len(read) < 2:
        return []
    width = max(28, *(len(r.label[:24]) + 2 for r in read))
    lines = [
        "",
        f"{len(bands.differs)} configured key(s) are set differently across {len(read)} "
        f"searchpoint(s) and {len(bands.one_sided)} are configured by only some; the "
        f"{len(bands.same)} identical are not printed.",
        "",
        f"{'key':<30}" + "".join(f"{r.label[:24]:<{width}}" for r in read),
    ]
    for key in [*bands.differs, *bands.one_sided]:
        cells = _diff_window([(r.config or {}).get(key) for r in read], width - 2)
        lines.append(f"{key[:29]:<30}" + "".join(f"{c:<{width}}" for c in cells))
    return lines


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
        lines.append("")
        lines.append(head)
        for level in factor.levels:
            value = "—" if level.value is None else f"{level.value:,.2f} {unit}"
            lines.append(
                f"      {_clip(level.level, 38):<40}{value:>18}"
                f"   {level.n_cells} cell(s), {len(level.subjects)} subject(s)"
            )
        if factor.note:
            lines.append(f"      {factor.note}")
    return lines


def _grid_lines(ev: Evidence) -> list[str]:
    if (grid := ev.grid) is None:
        return []
    rows = sorted({c.row for c in grid.cells})
    cols = sorted({c.col for c in grid.cells})
    at = {(c.row, c.col): c for c in grid.cells}
    width = max((len(_clip(c, 16)) for c in cols), default=8) + 3
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
    lines.append("")
    lines.append("  " + "".ljust(22) + "".join(_clip(c, 16).rjust(width) for c in cols))
    for row in rows:
        painted = []
        for col in cols:
            cell = at.get((row, col))
            value = "—" if cell is None or cell.value is None else f"{cell.value:,.2f}"
            painted.append(value.rjust(width))
        lines.append("  " + _clip(row, 20).ljust(22) + "".join(painted))
    return lines


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
        return "—"
    head = "…" if start else ""
    tail = value[start:]
    room = width - len(head)
    return f"{head}{tail}" if len(tail) <= room else f"{head}{tail[: room - 1]}…"


def _scenario_lines(ev: Evidence) -> list[str]:
    lines: list[str] = []
    for r in ev.subjects:
        s = r.scenario
        if s is None:
            continue
        verdict = (
            f"parts at round {s.first_divergent_round}, where it would have crowned "
            f"{(s.scenario_winner_id or '-')[:8]} instead of {(s.recorded_winner_id or '-')[:8]}"
            if s.winner_changed
            else "never parts from the record"
        )
        lines.append(
            f"\n{r.label} under {r.mask.lens if r.mask else '-'}: {verdict}. "
            f"{s.invariant_rounds} of {s.total_rounds} round(s) unchanged; the head reads over "
            f"{s.n_samples_scored} sample(s)."
        )
        lines.append(f"  {s.note}")
    return lines


def _winner_chain_lines(ev: Evidence) -> list[str]:
    spec = _UNIT_SPEC[ev.metric.spec.unit]
    lines: list[str] = []
    for r in ev.subjects:
        if not r.winner_chain:
            continue
        lines.append(f"\n{r.label} — the branch behind it, origin first:")
        for point in r.winner_chain:
            value = "         ." if point.value is None else f"{spec.format(point.value):>10}"
            lines.append(
                f"  r{point.round:<3} {point.label[:20]:<20}  {value}  "
                f"{fmt_ci(point.ci_lo, point.ci_hi, spec=spec):>20}  {point.n_cells:>3} cells"
            )
    return lines


def _pairwise_lines(ev: Evidence) -> list[str]:
    m = ev.metric
    if not m.pairwise:
        return ["", "A pairwise comparison needs two subjects."]
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        "",
        f"{'pair (b - a)':<40}  {'lift':>11}  {'95% CI':>20}  {'n':>3}  "
        f"{'p':>16}  {'p (Holm)':>16}",
    ]
    for pair in m.pairwise:
        label = f"{pair.subject_a[-8:]} -> {pair.subject_b[-8:]}"
        lines.append(f"{label:<40}  {_pair_text(pair.reading, spec, 11, 20, 3, 16)}")
    return [
        *lines,
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
    spec = _UNIT_SPEC[ev.metric.spec.unit]
    lines = [
        "",
        f"Variance over the {v.n_cells} cell(s) all {v.n_subjects} subjects measured:",
        f"  cell effect {spec.format(v.cell_effect_sd)} | "
        f"subject effect {spec.format(v.subject_effect_sd)} | residual {spec.format(v.residual_sd)}",
        f"  under the null a subject mean still scatters by "
        f"{spec.format(v.null_subject_scatter)} — {verdict}.",
    ]
    if ev.power is not None:
        p = ev.power
        needed = "-" if p.cells_for_largest_gap is None else str(p.cells_for_largest_gap)
        lines += [
            "",
            f"Resolving power at {p.cells_per_subject} cells/subject: paired SE "
            f"{spec.format(p.paired_se)}, smallest detectable effect "
            f"{spec.format(p.min_detectable_effect)}.",
            f"  the widest gap on the roster is {spec.format(p.largest_subject_gap)}; resolving it "
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
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        "",
        f"{len(ev.edits)} searchpoint(s) ranked by how much each beat its OWN campaign's origin "
        f"on the same cells, in {m.spec.label}. Identity is `sp_hash`, so a prompt-only edit "
        "ranks like any other; nothing pools across campaigns, which share no anchor to pool on.",
        "",
        f"{'lift':>11}  {'95% CI':>20}  {'cells':>5}    {'campaign':<10}  edit",
    ]
    for c in ev.edits[:top]:
        lift, coverage = c.reading.headline, c.reading.coverage
        if lift is None or coverage is None:
            lines.append(
                f"{'unranked':>11}  {READING_STATE_INFO[c.reading.state].sentence}  "
                f"{c.campaign_id[:10]:<10}  {c.label}"
            )
            continue
        est = lift.estimate
        lines.append(
            f"{spec.format(est.value):>11}  {fmt_ci(est.ci_lo, est.ci_hi, spec=spec):>20}  "
            f"{coverage.scored:>5}  {' ' if est.side == 'spans' else '*'} "
            f"{c.campaign_id[:10]:<10}  {c.label}"
        )
    spread = ev.spread
    return [
        *lines,
        "",
        f"Spread across the {spread.n_edits} ranked edit(s): SD "
        f"{spec.format(spread.edit_effect_sd)}"
        if spread.edit_effect_sd is not None
        else f"Spread across the {spread.n_edits} ranked edit(s): one reading has none.",
        "",
        "* the interval excludes zero. Everything else is consistent with no effect — the "
        "ranking orders them, it does not endorse them.",
        "To settle one, deepen it rather than repeat it: `verify` re-scores a candidate on "
        "more samples and records the result without touching the cycle.",
    ]


async def cmd_evidence(args: argparse.Namespace) -> CommandResult:

    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    try:
        ev = select_evidence(
            stores,
            subjects=args.subject,
            dataset=args.dataset or "",
            grid=args.grid or "",
            include_ranking=args.ranking,
            include_winner_chain=args.winner_chain,
            include_config=args.config,
            metric=args.metric or MEASURAND,
        )
    except (ValueError, SyntaxError) as exc:
        return CommandResult(data={"error": str(exc)}, human=str(exc))
    lines = [
        *_head_to_head_lines(ev),
        *_roster_lines(ev),
        *_factor_lines(ev),
        *_grid_lines(ev),
        *_config_lines(ev),
        *_pairwise_lines(ev),
        *_variance_lines(ev),
        *_ranking_lines(ev, args.top),
    ]
    return CommandResult(data=ev.model_dump(), human="\n".join(lines))


__all__ = ["cmd_evidence"]
