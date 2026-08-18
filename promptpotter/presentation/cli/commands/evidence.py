"""What a SET of campaigns jointly says — read-only, ZERO spend. It NAMES a leader and writes
nothing: graduating one into an operator-owned manifest stays a deliberate hand-edit."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.evidence import Evidence, campaign_evidence, campaigns_on_dataset
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)

logger = logging.getLogger("promptpotter.presentation.cli")

_COMPARABILITY_NOTE = {
    "datasets_differ": (
        "Comparability NO: this selection spans several datasets, which measure different things "
        "— the levels above are not one quantity and no pairing rescues them."
    ),
    "rulers_differ": (
        "Comparability NO: these origins were measured on different delta rulers, so their "
        "absolute levels are not one quantity. Only within-ruler comparisons hold."
    ),
    "ruler_unstamped": (
        "Comparability UNKNOWN: at least one origin predates the ruler stamp. Absolute levels "
        "above may sit on different delta scales — pair on cells, do not read the column."
    ),
}


def _roster_lines(ev: Evidence) -> list[str]:
    lines = [
        f"{len(ev.origins)} campaign(s), oldest first. `level` is each origin's mean over its own "
        "cells, in the measurand's units.",
        "",
        f"{'created':<17}  {'campaign':<26}  {'dataset':<18}  {'arm':<8}  {'ruler':<8}  "
        f"{'level':>7}  {'cells':>5}  {'rounds':>6}",
    ]
    for o in ev.origins:
        level = "      ." if o.origin_level is None else f"{o.origin_level:>+7.3f}"
        lines.append(
            f"{o.created_at[:16]:<17}  {o.campaign_id:<26}  {o.dataset_name[:18]:<18}  "
            f"{o.arm_id[:8]:<8}  {(o.ruler_id or '-')[:8]:<8}  {level}  "
            f"{o.n_cells:>5}  {o.rounds_scored:>6}"
        )
    note = _COMPARABILITY_NOTE.get(ev.comparability.reason)
    if note:
        lines.append(f"\n{note}")
    for rep in ev.replicates:
        lines.append(
            f"\nArm {rep.arm_id[:8]} ran {len(rep.campaign_ids)} times — a REPLICATE, spread "
            f"{rep.level_spread:+.3f}. That spread is noise, not an effect."
        )
    return lines


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
    lines = [
        "",
        f"Variance over the {v.n_cells} cell(s) all {v.n_arms} campaigns measured:",
        f"  cell effect {v.cell_effect_sd:.3f} | arm effect {v.arm_effect_sd:.3f} | "
        f"residual {v.residual_sd:.3f}",
        f"  under the null an arm mean still scatters by {v.null_arm_scatter:.3f} — {verdict}.",
    ]
    if ev.power is not None:
        p = ev.power
        needed = "-" if p.cells_for_largest_gap is None else str(p.cells_for_largest_gap)
        lines += [
            "",
            f"Resolving power at {p.cells_per_arm} cells/arm: paired SE {p.paired_se:.3f}, "
            f"smallest detectable effect {p.min_detectable_effect:+.3f}.",
            f"  the widest gap on the roster is {p.largest_arm_gap:+.3f}; resolving it would "
            f"take ~{needed} cells per arm.",
        ]
    oc = ev.order_confound
    if oc is not None and oc.level_vs_order is not None:
        note = (
            " — the roster's ordering IS its chronology, so arm and run date cannot be separated"
            if oc.order_confounded
            else ""
        )
        lines += ["", f"Run-order confound: level vs order rho {oc.level_vs_order:+.2f}{note}."]
    return lines


def _ranking_lines(ev: Evidence, top: int) -> list[str]:
    if not ev.ranking_computed:
        return [
            "",
            "Edit ranking not computed — pass `--ranking`. It is the only half that opens a round "
            "document past round 0, so it walks every round of every campaign selected.",
        ]
    if not ev.edits:
        return [
            "",
            "No scored edits: one needs its campaign's round-0 origin plus at least one later "
            "round to compare against.",
        ]
    lines = [
        "",
        f"{len(ev.edits)} edit(s) ranked by how much each beat its own origin on the same cells",
        "",
        f"{'effect':>9}  {'95% CI':>19}  {'cells':>5}  {'obs':>4}  edit",
    ]
    for c in ev.edits[:top]:
        ci = f"[{c.ci_lo:+.4f}, {c.ci_hi:+.4f}]"
        # An interval straddling zero is the ordinary outcome on a small panel; say so per row
        # rather than letting the ranking imply every row above the fold is a winner.
        mark = " " if c.ci_lo <= 0.0 <= c.ci_hi else "*"
        lines.append(
            f"{c.anchor_effect:>+9.4f}  {ci:>19}  {c.n_cells:>5}  "
            f"{c.n_measurements:>4}{mark} {c.label}"
        )
    return [
        *lines,
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
    ids = list(args.campaign) or campaigns_on_dataset(stores, args.dataset or "")
    ev = campaign_evidence(stores, ids, include_ranking=args.ranking)
    if not ev.origins:
        return CommandResult(
            data=ev.model_dump(),
            human=(
                "No scored campaigns in that selection. A campaign answers here once its round-0 "
                "origin has run. Name a dataset, or repeat --campaign for an explicit set."
            ),
        )
    lines = [*_roster_lines(ev), *_variance_lines(ev), *_ranking_lines(ev, args.top)]
    return CommandResult(data=ev.model_dump(), human="\n".join(lines))


__all__ = ["cmd_evidence"]
