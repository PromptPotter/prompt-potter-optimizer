"""What a SET of campaigns jointly says — read-only, ZERO spend. It NAMES a leader and writes
nothing: graduating one into an operator-owned manifest stays a deliberate hand-edit."""

from __future__ import annotations

import argparse
import logging
from typing import get_args

from promptpotter.application.evidence import Evidence, campaign_evidence, campaigns_on_dataset
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
    """One row per campaign, identity and reading in ONE table. Two tables — a roster in the
    measurand and a merged estimate in the picked metric — printed one number under the other in
    different units, and the operator had no way to see which column the picker moved."""
    m = ev.metric
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        f"{len(ev.campaigns)} campaign(s), oldest first, read under {m.spec.axis_label}.",
        m.spec.description,
        # The value column brackets that campaign's OWN cells — its `cells` count — because that
        # is what `mean_ci_t` was handed. Calling it "merged over the cells every campaign
        # scored" put the shared axis's denominator on a number that never used it, and the two
        # differ exactly when a campaign came up short.
        f"Each value merges that campaign's own cells (the `cells` column); "
        f"{len(m.scored_cells)} cell(s) are shared by all of them, which is what the pairs and "
        "the variance split are over.",
        # The catalogue is per-selection and the terminal had no way to see it: an operator could
        # only discover a key by guessing one and reading the rejection.
        f"Offered here: {', '.join(s.key for s in m.catalogue) or '-'} — pass --metric KEY, or "
        f"--metric 'expr:<formula>' over {', '.join(m.namespace) or '-'}.",
        "",
        f"{'created':<10}  {'campaign':<26}  {'dataset':<18}  {'arm':<8}  {'ruler':<8}  "
        f"{'value':>10}  {'95% CI':>20}  {'cells':>5}  {'unread':>6}  {'rounds':>6}",
    ]
    for r in ev.campaigns:
        value = "         ." if r.value is None else f"{spec.format(r.value):>10}"
        ruler = (r.ability.ruler_id if r.ability is not None else None) or "-"
        lines.append(
            f"{r.created_at[:10]:<10}  {r.campaign_id:<26}  {r.dataset_name[:18]:<18}  "
            f"{(r.arm_id or '-')[:8]:<8}  {ruler[:8]:<8}  {value}  "
            f"{fmt_ci(r.ci_lo, r.ci_hi, spec=spec):>20}  {r.n_cells:>5}  {r.n_unscorable:>6}  "
            f"{r.rounds_scored:>6}"
        )
    if ev.unread_campaigns:
        lines.append(
            f"\nAsked for but not read: {', '.join(ev.unread_campaigns)}. Each either does not "
            "exist under this tenant or has no scored round-0 origin yet, so it is absent from "
            "every number above rather than counted as a low one."
        )
    if not any(r.values for r in ev.campaigns):
        lines.append(
            "\nNo campaign scored a cell under this metric — unavailable for this selection, "
            "which is not the same as a value of zero."
        )
    lines.append(f"\n{ev.comparability.note}")
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


def _pairwise_lines(ev: Evidence) -> list[str]:
    """Every pair, blocked on the cells both scored. An absent interval prints as absent —
    ``fmt_ci``'s rule — because a fabricated bracket claims certainty about a measurement that
    never happened."""
    m = ev.metric
    if not m.pairwise:
        return ["", "A pairwise comparison needs two campaigns sharing a scored cell."]
    spec = _UNIT_SPEC[m.spec.unit]
    lines = [
        "",
        f"{'pair (b - a)':<40}  {'shift':>11}  {'95% CI':>20}  {'n':>3}  "
        f"{'p':>16}  {'p (Holm)':>16}",
    ]
    for pair in m.pairwise:
        label = f"{pair.campaign_a[-8:]} -> {pair.campaign_b[-8:]}"
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
            "Variance: needs two campaigns sharing two cells; nothing to decompose. Campaigns on "
            "different datasets never share a cell, which is why a mixed selection stops here.",
        ]
    verdict = (
        "so nothing here is distinguishable from noise"
        if v.arm_sd_below_noise
        else "so the arms differ by more than noise alone would produce"
    )
    # These are spreads of the SELECTED metric's own cell values, so they read in its unit —
    # the same one the roster and pairwise tables above already format through.
    spec = _UNIT_SPEC[ev.metric.spec.unit]
    lines = [
        "",
        f"Variance over the {v.n_cells} cell(s) all {v.n_arms} campaigns measured:",
        f"  cell effect {spec.format(v.cell_effect_sd)} | "
        f"arm effect {spec.format(v.arm_effect_sd)} | residual {spec.format(v.residual_sd)}",
        f"  under the null an arm mean still scatters by "
        f"{spec.format(v.null_arm_scatter)} — {verdict}.",
    ]
    if ev.power is not None:
        p = ev.power
        needed = "-" if p.cells_for_largest_gap is None else str(p.cells_for_largest_gap)
        lines += [
            "",
            f"Resolving power at {p.cells_per_arm} cells/arm: paired SE "
            f"{spec.format(p.paired_se)}, smallest detectable effect "
            f"{spec.format(p.min_detectable_effect)}.",
            f"  the widest gap on the roster is {spec.format(p.largest_arm_gap)}; resolving it "
            f"would take ~{needed} cells per arm.",
            f"  WIDTH, before any of that: an exact test on {p.cells_per_arm} cells cannot return "
            f"a p below {p.exact_p_floor:.3f}, so surviving Holm over {ev.metric.n_tests} "
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
    # Through the ONE matcher, so `--campaign ca6d4d` reaches the same campaign here as it does
    # for `verify`. An id that still resolves to nothing rides `unread_campaigns`, which is the
    # read's own way of saying so — it was the only way a mistyped id surfaced at all.
    ids = [resolve_campaign_hint(stores, c) for c in args.campaign] or campaigns_on_dataset(
        stores, args.dataset or ""
    )
    try:
        ev = campaign_evidence(
            stores, ids, include_ranking=args.ranking, metric=args.metric or MEASURAND
        )
    except (ValueError, SyntaxError) as exc:
        # No prefix: the read raises about the METRIC or about the SELECTION and says which, so
        # stamping "Invalid --metric" on both mislabelled every unmeasured selection as a typo in
        # a flag the operator had not passed.
        return CommandResult(data={"error": str(exc)}, human=str(exc))
    lines = [
        *_roster_lines(ev),
        *_pairwise_lines(ev),
        *_variance_lines(ev),
        *_ranking_lines(ev, args.top),
    ]
    return CommandResult(data=ev.model_dump(), human="\n".join(lines))


__all__ = ["cmd_evidence"]
