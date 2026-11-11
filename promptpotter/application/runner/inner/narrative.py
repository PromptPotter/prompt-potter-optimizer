"""What the outer model reads of one inner run: hashed with the L4 instrument
(`tasks.py::measurement_modules`), so an edit here re-keys L4's cells and no optimizer's."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.views.render.primitives import fmt_pct
from promptpotter.domain.arm_reading import ArmFit, order_floor
from promptpotter.domain.arms import Cut, Measured
from promptpotter.domain.l4.inner_facts import SeriesGap, series_reason_head
from promptpotter.domain.l4.proxies import (
    effective_round_budget,
    floor_reason,
    parent_levels,
    unread_rounds,
)
from promptpotter.domain.wounds import COLLAPSE_WORDS, collapse_counts

if TYPE_CHECKING:
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec
    from promptpotter.domain.l4.inner_facts import InnerCellFacts
    from promptpotter.domain.l4.proxies import OuterSampleProxies
    from promptpotter.domain.results import CycleResult


def _clip(text: str, cap: int) -> str:
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


def _lift_shape(result: CycleResult) -> str:
    l1 = [rr for rr in result.rounds if rr.round > 0]
    marks = " ".join(f"r{rr.round}{'+' if rr.improved else '.'}" for rr in l1)
    if not marks:
        return "lifts: none — no L1 round closed."
    n = sum(1 for rr in l1 if rr.improved)
    budget = effective_round_budget(result)
    return f"lifts: {marks} ({n}/{budget}; target: early and often, thinning late)"


def narrative_head(spec: InnerTaskSpec) -> str:
    # Two cells of a pool share a dataset and a seed, so the model is what tells them apart.
    env = f"{spec.inner_dataset} on {spec.inner_model}" if spec.inner_model else spec.inner_dataset
    return f"INNER {env} seed-{spec.run_seed}"


def inner_narrative(
    result: CycleResult, facts: InnerCellFacts, proxies: OuterSampleProxies, head: str
) -> str:
    """Authored under ``TRANSCRIPT_REASONING_CAP``, so the outer transcripts render never clips it."""
    if (floor := floor_reason(result)) is not None:
        return (
            f"{head}: {floor} — scored at the floor "
            f"(optimizer-prompt-owned); stop={result.stop_reason}."
        )
    # Both guaranteed by `compute_outer_proxies`, which ran first.
    bench_origin, bench_final = facts.bench_origin_level, facts.bench_final_lift
    assert bench_origin is not None and bench_final is not None
    held = parent_levels(result)
    levels = [] if held is None else held[1]
    origin = facts.origin_level
    if origin is None:
        reason = facts.series_reason
        assert reason is not None
        holes = f", round(s) {unread_rounds(result)}" if reason is SeriesGap.UNREAD_ROUND else ""
        search = f" | search-split th: withheld — {series_reason_head(reason)}{holes}"
    else:
        assert facts.mean_round_delta is not None
        assert facts.final_lift is not None and facts.peak_lift is not None
        search = (
            f" | search-split th: floor {origin:+.2f} -> mean D{facts.mean_round_delta:+.3f}"
            f", ended {origin + facts.final_lift:+.2f}, peak {origin + facts.peak_lift:+.2f}"
            f" over {facts.rounds_ran} of {facts.round_budget} rounds"
        )
    lines = [
        f"{head}: held-out {result.bench.bench_size} rows:"
        f" floor {bench_origin:.3f} -> mean-over-rounds B{proxies.held_out_lift:+.3f}"
        f" (the scored lift), ended {bench_origin + bench_final:.3f} (B{bench_final:+.3f})"
        f"{search}; stop={result.stop_reason}.",
        _lift_shape(result),
    ]
    by_round = {rnd.round: rnd for rnd in result.rounds}
    critiques = {r: rnd.closed_state.feedback() for r, rnd in by_round.items()}
    highlight = next(
        (
            f"{cluster['mechanism']}: {cluster['claim']}"
            for r in sorted(by_round)
            if (c := critiques.get(r))
            for cluster in c.get("failure_highlights") or []
        ),
        None,
    )
    if highlight:
        lines.append(f"saw: {_clip(highlight, 200)}")
    # Positional: the window's levels run beside its rounds, whatever round it starts at.
    for at, rnd in enumerate(sorted(result.rounds, key=lambda rr: rr.round)):
        if rnd.round == 0:
            continue
        parts = []
        if origin is not None and at < len(levels):
            parts.append(f"level {levels[at]:.3f} (D{levels[at] - origin:+.3f})")
        steer = critiques.get(rnd.round - 1)
        if steer and steer.get("priority_fix"):
            parts.append(f"steer: {_clip(steer['priority_fix'], 130)}")
        fits = {key: rnd.fit(key) or ArmFit() for key in rnd.panel}
        scored = [
            (key, c, fold)
            for key, c in rnd.panel.items()
            if isinstance(c, Cut | Measured) and (fold := rnd.folds.get(key)) is not None
        ]
        if scored:
            # Lift over the MATCHED parent only: an arm that never covered its panel has no lift.
            floors = {
                key: reading.reference_level("fitness")
                if (reading := fits[key].vs_reference)
                else None
                for key, _, _ in scored
            }
            margins = {
                key: float("-inf")
                if (matched := floors[key]) is None or fold.accuracy is None
                else fold.accuracy - matched
                for key, _, fold in scored
            }
            top_key, top, top_fold = max(
                scored, key=lambda kc: (margins[kc[0]], order_floor(kc[2].composite_fitness))
            )
            reference = floors[top_key]
            fit = fits[top_key]
            theta = (
                f", th {fit.theta:.2f}+/-{fit.theta_se:.2f}"
                if fit.theta is not None and fit.theta_se is not None
                else ""
            )
            versus = (
                f" vs matched-parent {reference:.3f}"
                if reference is not None
                else " (stopped before it covered the origin's samples, so nothing to compare)"
            )
            parts.append(
                f"tried {top.label} (acc {fmt_pct(top_fold.accuracy, '{:.3f}')}{versus}{theta}): "
                f"{_clip(top.changes_description, 100)}"
            )
        else:
            parts.append("no scored candidates")
        collapses = collapse_counts(c.validation_failures for c in rnd.panel.values())
        anomalies = [
            f"{word} x{n}"
            for reason, word in COLLAPSE_WORDS.items()
            if (n := collapses.get(reason))
        ]
        if anomalies:
            parts.append(", ".join(anomalies))
        # Each fact carries its round: a citation quotes one fact, never the line it sits on.
        lines.append(" | ".join(f"R{rnd.round} {part}" for part in parts))
    # Earliest rounds go first: the panel's own head-keep clip would cut the latest instead.
    n_head = 3 if highlight else 2
    head_lines, round_lines = lines[:n_head], lines[n_head:]
    elided = False
    while len(round_lines) > (2 if elided else 1) and (
        len("\n".join(head_lines + round_lines)) > 1150
    ):
        round_lines.pop(0 if not elided else 1)
        if not elided:
            round_lines.insert(0, "[earlier rounds elided]")
            elided = True
    return "\n".join(head_lines + round_lines)
