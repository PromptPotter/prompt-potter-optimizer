"""What a SET of subjects jointly says — read-only, ZERO spend. It NAMES a leader and writes
nothing: graduating one into an operator-owned manifest stays a deliberate hand-edit."""

from __future__ import annotations

import argparse
import logging
from typing import get_args

from promptpotter.application.evidence import (
    Evidence,
    SubjectSpec,
    campaigns_on_dataset,
    parse_subject,
    subject_evidence,
)
from promptpotter.application.evidence_metrics import MEASURAND, MetricUnit
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
    resolve_campaign_hint,
)
from promptpotter.presentation.views.display import fmt_ci, fmt_pvalue

logger = logging.getLogger("promptpotter.presentation.cli")

# How each measurand reads on a terminal. Only a `delta` earns a leading `+`: it is a difference,
# where an absolute level or a ratio is not, and printing one on a ratio dresses it as a lift.
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

# Adding a `MetricUnit` without a format here is a KeyError on the first campaign that resolves to
# it — on the operator's terminal, mid-table. Fail at import instead, the way
# `optimization/resume_and_fork/decisions.py` gates its own kind→policy map.
_unformatted = sorted(set(get_args(MetricUnit)) - set(_UNIT_SPEC))
if _unformatted:
    raise RuntimeError(f"MetricUnit members with no terminal format: {_unformatted}")
del _unformatted


def _roster_lines(ev: Evidence) -> list[str]:
    """One row per subject, identity and reading in ONE table. Two tables — a roster in the
    measurand and a merged estimate in the picked metric — printed one number under the other in
    different units, and the operator had no way to see which column the picker moved."""
    m = ev.metric
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        f"{len(ev.subjects)} subject(s), oldest first, read under {m.spec.axis_label}.",
        m.spec.description,
        # The value column brackets that subject's OWN cells — its `cells` count — because that
        # is what `mean_ci_t` was handed. Calling it "merged over the cells every subject
        # scored" put the shared axis's denominator on a number that never used it, and the two
        # differ exactly when a subject came up short.
        f"Each value merges that subject's own cells (the `cells` column); "
        f"{len(m.scored_cells)} cell(s) are shared by all of them, which is what the pairs and "
        "the variance split are over.",
        # The catalogue is per-selection and the terminal had no way to see it: an operator could
        # only discover a key by guessing one and reading the rejection.
        f"Offered here: {', '.join(s.key for s in m.catalogue) or '-'} — pass --metric KEY, or "
        f"--metric 'expr:<formula>' over {', '.join(m.namespace) or '-'}.",
        "",
        f"{'created':<10}  {'kind':<9}  {'subject':<26}  {'dataset':<18}  {'arm':<8}  "
        f"{'ruler':<8}  {'value':>10}  {'95% CI':>20}  {'cells':>5}  {'unread':>6}  {'rounds':>6}",
    ]
    for r in ev.subjects:
        value = "         ." if r.value is None else f"{spec.format(r.value):>10}"
        ruler = (r.ability.ruler_id if r.ability is not None else None) or "-"
        # `?` where the ruler is unstamped, `x` where this subject measured on another scale than
        # the rest — the same two states a chart renders as a tag rather than as a shorter bar.
        mark = {True: " ", False: "x", None: "?"}[r.comparable]
        # A masked channel wears `~` on its KIND — it shares a label with the record it is a
        # mask of, and two identically-named rows is the one thing this table must not print.
        kind = f"{r.kind}~" if r.mask else r.kind
        lines.append(
            f"{r.created_at[:10]:<10}  {kind:<9}  {mark}{r.label[:25]:<25}  "
            f"{r.dataset_name[:18]:<18}  {(r.arm_id or '-')[:8]:<8}  {ruler[:8]:<8}  {value}  "
            f"{fmt_ci(r.ci_lo, r.ci_hi, spec=spec):>20}  {r.n_cells:>5}  "
            f"{len(r.unscorable_cells):>6}  {r.cycle_rounds_scored:>6}"
        )
    lines += _scenario_lines(ev)
    lines += _trajectory_lines(ev)
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
    lines.append(f"\n{ev.comparability.note}")
    off = [r.label for r in ev.subjects if r.comparable is False]
    if off:
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


def _config_lines(ev: Evidence) -> list[str]:
    """The searchpoints lined up — one row per key, differing keys first. Silent unless
    ``--config`` was asked for; identical keys are counted, not printed, because the comparison is
    the point and a wall of matching model names buries it."""
    read = [r for r in ev.subjects if r.config is not None]
    if len(read) < 2:
        return []
    keys = sorted({k for r in read if r.config for k in r.config})
    differs = [k for k in keys if len({(r.config or {}).get(k) for r in read}) > 1]
    width = max(28, *(len(r.label[:24]) + 2 for r in read))
    lines = [
        "",
        f"{len(differs)} of {len(keys)} configured key(s) differ across "
        f"{len(read)} searchpoint(s); the rest are identical and not printed.",
        "",
        f"{'key':<30}" + "".join(f"{r.label[:24]:<{width}}" for r in read),
    ]
    for key in differs:
        # A prose field is a paragraph — one line per key stays a table, and the full value is
        # in `--json` rather than wrapped across the terminal.
        cells = _diff_window([(r.config or {}).get(key) for r in read], width - 2)
        lines.append(f"{key[:29]:<30}" + "".join(f"{c:<{width}}" for c in cells))
    return lines


def _diff_window(values: list[str | None], width: int) -> list[str]:
    """Clip each cell around where the row FIRST diverges, not around its start.

    Two L1 edits of one prompt field share a long prefix, so a window opened at character zero
    renders both cells identically — a row listed under "differ" that reads as identical is worse
    than no row at all. The window opens just before the first character they disagree on."""
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
    """What each masked channel did to its branch. Silent where no subject carries a mask — the
    ordinary read has no counterfactual to report on."""
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


def _trajectory_lines(ev: Evidence) -> list[str]:
    """The branch behind each subject that carries one — asked for with ``--trajectory``, silent
    otherwise rather than restating that it was not."""
    spec = _UNIT_SPEC[ev.metric.spec.unit]
    lines: list[str] = []
    for r in ev.subjects:
        if not r.trajectory:
            continue
        lines.append(f"\n{r.label} — the branch behind it, origin first:")
        for point in r.trajectory:
            value = "         ." if point.value is None else f"{spec.format(point.value):>10}"
            lines.append(
                f"  r{point.round:<3} {point.label[:20]:<20}  {value}  "
                f"{fmt_ci(point.ci_lo, point.ci_hi, spec=spec):>20}  {point.n_cells:>3} cells"
            )
    return lines


def _pairwise_lines(ev: Evidence) -> list[str]:
    """Every pair, blocked on the cells both scored. An absent interval prints as absent —
    ``fmt_ci``'s rule — because a fabricated bracket claims certainty about a measurement that
    never happened."""
    m = ev.metric
    if not m.pairwise:
        return ["", "A pairwise comparison needs two subjects sharing a scored cell."]
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        "",
        f"{'pair (b - a)':<40}  {'shift':>11}  {'95% CI':>20}  {'n':>3}  "
        f"{'p':>16}  {'p (Holm)':>16}",
    ]
    for pair in m.pairwise:
        label = f"{pair.subject_a[-8:]} -> {pair.subject_b[-8:]}"
        lines.append(
            f"{label:<40}  {spec.format(pair.median_shift):>11}  "
            f"{fmt_ci(pair.ci_lo, pair.ci_hi, spec=spec):>20}  {pair.n_cells:>3}  "
            f"{fmt_pvalue(pair.p_value):>16}  {fmt_pvalue(pair.p_adjusted):>16}"
        )
    return [
        *lines,
        "",
        f"Holm corrects across the {m.n_tests} comparison(s) in this table. It does NOT correct "
        "across metrics: if you tried several and kept the tightest, the interval you are reading "
        "is optimistic by an amount nothing here can compute.",
        "The shift is Hodges-Lehmann and the test is exact — no normal tail is assumed, so p stops "
        "at what this many paired cells can carry rather than borrowing the rest.",
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
    # These are spreads of the SELECTED metric's own cell values, so they read in its unit —
    # the same one the roster and pairwise tables above already format through.
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
            f"  WIDTH, before any of that: an exact test on {p.cells_per_subject} cells cannot "
            f"return a p below {p.exact_p_floor:.3f}, so surviving Holm over {ev.metric.n_tests} "
            f"comparison(s) needs {p.cells_for_corrected_verdict} cells — at any effect size.",
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
            "round of every campaign selected.",
        ]
    if not ev.edits:
        return [
            "",
            "No scored edits: one needs its campaign's round-0 origin plus at least one later "
            "round to compare against.",
        ]
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        "",
        f"{len(ev.edits)} edit(s) ranked by how much each beat its own origin on the same cells, "
        f"in {m.spec.label}",
        "",
        f"{'effect':>11}  {'95% CI':>20}  {'cells':>5}  {'obs':>4}  edit",
    ]
    for c in ev.edits[:top]:
        # An interval straddling zero is the ordinary outcome on a small panel; say so per row
        # rather than letting the ranking imply every row above the fold is a winner.
        clears = c.ci_lo is not None and c.ci_hi is not None and not (c.ci_lo <= 0.0 <= c.ci_hi)
        lines.append(
            f"{spec.format(c.anchor_effect):>11}  {fmt_ci(c.ci_lo, c.ci_hi, spec=spec):>20}  "
            f"{c.n_cells:>5}  {c.n_measurements:>4}{'*' if clears else ' '} {c.label}"
        )
    spread = ev.spread
    return [
        *lines,
        "",
        f"Spread across all {spread.n_edits} edit(s): SD {spec.format(spread.edit_effect_sd)}"
        if spread.edit_effect_sd is not None
        else f"Spread across all {spread.n_edits} edit(s): one reading has none.",
        "",
        "* the interval excludes zero. Everything else is consistent with no effect — the "
        "ranking orders them, it does not endorse them.",
        "To settle one, deepen it rather than repeat it: `verify` re-scores a candidate on "
        "more samples and records the result without touching the cycle.",
    ]


async def cmd_evidence(args: argparse.Namespace) -> CommandResult:
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if get_verbose() else "cli")
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    try:
        # The campaign half of every address goes through the ONE matcher, so
        # `--subject course:ca6d4d/cycle_x` reaches the same campaign here as `--subject
        # campaign:ca6d4d` and as `verify` does. One that still resolves to nothing rides
        # `unread_subjects`, which is the read's own way of saying so.
        specs = [
            spec._replace(campaign_id=resolve_campaign_hint(stores, spec.campaign_id))
            for spec in (parse_subject(raw) for raw in args.subject)
        ]
    except ValueError as exc:
        return CommandResult(data={"error": str(exc)}, human=str(exc))
    specs = specs or [
        SubjectSpec("campaign", cid) for cid in campaigns_on_dataset(stores, args.dataset or "")
    ]
    try:
        ev = subject_evidence(
            stores,
            specs,
            include_ranking=args.ranking,
            include_trajectory=args.trajectory,
            include_config=args.config,
            metric=args.metric or MEASURAND,
        )
    except (ValueError, SyntaxError) as exc:
        # No prefix: the read raises about the METRIC or about the SELECTION and says which, so
        # stamping "Invalid --metric" on both mislabelled every unmeasured selection as a typo in
        # a flag the operator had not passed.
        return CommandResult(data={"error": str(exc)}, human=str(exc))
    lines = [
        *_roster_lines(ev),
        *_config_lines(ev),
        *_pairwise_lines(ev),
        *_variance_lines(ev),
        *_ranking_lines(ev, args.top),
    ]
    return CommandResult(data=ev.model_dump(), human="\n".join(lines))


__all__ = ["cmd_evidence"]
