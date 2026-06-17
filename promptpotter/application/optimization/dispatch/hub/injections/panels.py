"""Diagnostic + cross-cycle memory injection renderers.

Per-round readout (`_r_diagnostics`: STATUS + RoundDiagnostics body) and archive memory derived
via `AxisIndex` (rankings, top runs, rare hits, intractable clusters). Uniform
`(InjectionBundle) -> str` signature.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.dispatch.hub.bundle import (
    INTRACTABLE_SAMPLES_RENDER_CAP,
    NEAR_MISS_RENDER_CAP,
    SAMPLE_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    fence_untrusted,
    signal,
)
from promptpotter.domain.results import CritiqueReadout


@signal(
    "diagnostics",
    kind=InjectionKind.DERIVED,
    description="Layer-agnostic round readout: STATUS header + RoundDiagnostics body.",
    char_cap=2000,
)
def _r_diagnostics(b: InjectionBundle) -> str:
    """Round readout: plain STATUS (cycle counters, renders even pre-R1) + fenced RoundDiagnostics
    body (wrapped because it echoes raw queries/GTs/warnings).

    Section order is **actionability-first**: the per-sample failure detail (SAMPLE DIAGNOSTICS,
    NEAR MISSES, MISSED OPPORTUNITIES) — the only content that names *which* queries failed and how
    — renders before the aggregate distributions, and the historical TRAJECTORY/EVOLUTION narrative
    renders last. The render façade truncates by blind tail-cut at `char_cap`, so whatever is least
    actionable must sit last to be the first dropped when a round runs over budget.
    """
    sections: list[str] = []
    cs = b.cycle_slice
    status: list[str] = [
        f"STATUS: round {cs.round_num} | current {cs.current_accuracy:.1%} | "
        f"best {cs.best_accuracy:.1%} @ round {cs.best_round}"
    ]
    if cs.l1_stall_count > 0:
        status.append(f"  L1 stall: {cs.l1_stall_count} rounds")
    if cs.l2_round > 0:
        status.append(f"  L2 fired: {cs.l2_round}x (stall: {cs.l2_stall_count})")
    if cs.l3_round > 0:
        status.append(f"  L3 fired: {cs.l3_round}x (stall: {cs.l3_stall_count})")
    sections.append("\n".join(status))

    d = b.digest.diagnostics
    if d is None:
        return "\n\n".join(sections)
    parts: list[str] = []

    if d.anomalies:
        parts.append("ANOMALIES:\n  " + "\n  ".join(d.anomalies))

    miss_samples = [s for s in d.samples if not s.hit][:SAMPLE_RENDER_CAP]
    if miss_samples:
        s_lines = [f"SAMPLE DIAGNOSTICS ({len(miss_samples)}/{len(d.samples)} misses shown):"]
        for s in miss_samples:
            rank_str = f"r={s.rank}" if s.rank is not None else "no rank"
            extras = []
            if s.gt_in_source is not None:
                extras.append(f"gt_in_source={s.gt_in_source}")
            if s.gt_in_ranked is not None:
                extras.append(f"gt_in_ranked={s.gt_in_ranked}")
            extra_str = f" | {', '.join(extras)}" if extras else ""
            s_lines.append(
                f"  MISS [{s.terminated_at}] {s.query[:70]} → {s.predicted[:60]}"
                f" (GT: {s.ground_truth[:60]}, {rank_str}{extra_str})"
            )
        parts.append("\n".join(s_lines))

    if d.near_misses:
        nm_lines = [f"NEAR MISSES ({len(d.near_misses)} — GT in candidates but not r=1):"]
        for nm in d.near_misses[:NEAR_MISS_RENDER_CAP]:
            nm_lines.append(
                f"  [r={nm.rank}] {nm.query} → predicted: {nm.predicted} (GT: {nm.ground_truth})"
            )
        parts.append("\n".join(nm_lines))

    if d.cross_candidate_diff:
        parts.append(
            "MISSED OPPORTUNITIES (queries other candidates solved but winner missed):\n"
            + "\n".join(d.cross_candidate_diff)
        )

    if d.n_valid:
        rb = d.rank_buckets
        # Suppress RANK block when every query is `not_found` — schema has no ranker, so reporting
        # 0%-all only steers L2 toward hallucinated "fix the ranker" advice.
        all_not_found = rb.get("not_found", 0) == d.n_valid
        if not all_not_found:
            rank_line = (
                f"RANK DISTRIBUTION ({d.n_valid} queries): "
                f"r=1: {rb.get('1', 0)} | r=2-5: {rb.get('2-5', 0)} | "
                f"r=6-10: {rb.get('6-10', 0)} | r=11-20: {rb.get('11-20', 0)} | "
                f"not_found: {rb.get('not_found', 0)}"
            )
            if d.top_k_accuracy:
                rank_line += "\n  " + " | ".join(
                    f"top-{k}: {v:.0%}" for k, v in sorted(d.top_k_accuracy.items())
                )
            parts.append(rank_line)

    # PIPELINE HEALTH: skip when single-terminator + 0% errors + 0% warnings (default success).
    if d.termination_dist and (
        d.error_rate > 0 or d.warning_rate > 0 or len(d.termination_dist) > 1
    ):
        td_lines = ["PIPELINE HEALTH:"]
        for step, count in sorted(d.termination_dist.items(), key=lambda x: -x[1]):
            td_lines.append(f"  terminate@{step}: {count}")
        if d.error_rate > 0 or d.warning_rate > 0:
            td_lines.append(
                f"  error_rate: {d.error_rate:.0%} | warning_rate: {d.warning_rate:.0%}"
            )
        parts.append("\n".join(td_lines))

    if d.l1_diversity != 1.0:
        parts.append(f"POPULATION: diversity={d.l1_diversity:.2f}")

    if (po := d.probe_outcome) is not None:
        parts.append(
            f"PROBE OUTCOME: axis={po.axis_tested} subset={po.target_subset_size} "
            f"hit_rate={po.hit_rate:.0%} delta={po.delta_vs_full:+.1%}"
        )

    # TRAJECTORY + EVOLUTION last (least actionable: historical narrative, first to be tail-cut).
    # Skipped at R1 — "too few rounds to classify" is dead weight.
    if len(d.evolution_rows) > 1:
        line = f"TRAJECTORY: {d.trajectory}"
        if d.trajectory_description:
            line += f" — {d.trajectory_description}"
        parts.append(line)
        tbl = ["EVOLUTION (last rounds):", "  round  acc      Δ       degraded"]
        for row in d.evolution_rows[-5:]:
            tbl.append(
                f"  {row.round:>5}  {row.accuracy:>6.1%}  {row.delta:>+6.1%}  {row.degraded:>5}"
            )
        parts.append("\n".join(tbl))

    if parts:
        sections.append(fence_untrusted("\n\n".join(parts)))
    return "\n\n".join(sections)


# Order keys surface to the LLM — effect-driven items first (rankings, top values, exhausted) so
# attention lands on what to mutate; sample-side findings second; narrative tail last.
_AXIS_MEMORY_LABEL_ORDER: tuple[str, ...] = (
    "axis_rankings",
    "top_values",
    "exhausted_axes",
    "persistent_failures",
    "failure_clusters",
)


def _critique_is_all_prompt_field(critique: CritiqueReadout | None) -> bool:
    """All `critique.suggested_axes` are prompt-field axes? Drives axis-memory filter — when L1_CRITIQUE
    flagged only semantic failures, param-axis rankings are noise the critique already vetoed.
    """
    from promptpotter.config.settings import PROMPT_STRING_FIELDS

    sa = (critique.get("suggested_axes") if critique else None) or []
    if not sa:
        return False
    prompt_axes = set(PROMPT_STRING_FIELDS)
    return all(a in prompt_axes for a in sa)


def _filter_axis_rankings_to_prompt(value: str) -> str:
    """Keep only `axis_rankings` entries whose last dotted component is `prompt` — drops
    scalar-param entries (max_tokens, temperature, reasoning_effort, …). Returns "" on no survivors.
    """
    if not value:
        return value
    kept: list[str] = []
    for entry in value.split("; "):
        name = entry.split(" (", 1)[0].strip()
        tail = name.rsplit(".", 1)[-1]
        if tail == "prompt":
            kept.append(entry)
    return "; ".join(kept)


@signal(
    "axis_memory",
    kind=InjectionKind.DERIVED,
    description=(
        "Cross-cycle axis-keyed digest from AxisIndex: rankings, persistent failures, "
        "failure clusters, value trends, exhausted axes."
    ),
    char_cap=1200,  # digest() already caps to top-5 axes; this is the hard backstop.
)
def _r_axis_memory(b: InjectionBundle) -> str:
    """Cross-cycle axis/sample memory from the MeasurementArchive via `AxisIndex.digest`.

    Critique-aware filter: when L1_CRITIQUE flagged only semantic failures, strip `axis_rankings`
    to prompt axes + suppress `top_values` (param rankings are noise the critique already vetoed).
    Sample-side rows stay visible — they don't suggest axis mutations.
    """
    if b.axes is None:
        return ""
    digest = b.axes.digest()
    if not digest:
        return ""
    semantic = _critique_is_all_prompt_field(b.digest.critique)
    header = "AXIS MEMORY (cross-cycle observations from MeasurementArchive):"
    if semantic:
        header = (
            "AXIS MEMORY (cross-cycle observations from MeasurementArchive — "
            "CRITIQUE IS SEMANTIC: param-axis rankings hidden, target a prompt-field axis):"
        )
    lines = [header]
    for key in _AXIS_MEMORY_LABEL_ORDER:
        val = digest.get(key)
        if val is None:
            continue
        if semantic:
            if key == "top_values":
                continue
            if key == "axis_rankings":
                val = _filter_axis_rankings_to_prompt(val)
                if not val:
                    continue
        label = key.replace("_", " ")
        lines.append(f"  {label}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _query_stem(row: dict[str, Any], n: int = 70) -> str:
    q = (row.get("query") or "").replace("\n", " ").strip()
    return q[:n]


@signal(
    "origin_strengths",
    kind=InjectionKind.MEASUREMENT,
    description="Round-0 origin's per-sample hits — the floor variants must preserve.",
    char_cap=None,
)
def _r_origin_strengths(b: InjectionBundle) -> str:
    """One-line hit count — actionable signal ("don't strip scaffolding"). Enumerating samples
    added bytes without adding input: L1 preserves all origin hits, doesn't pick.
    """
    rows = b.origin_per_sample
    if not rows:
        return ""
    hits = [r for r in rows if r.get("hit")]
    if not hits:
        return ""
    return (
        f"ORIGIN STRENGTHS: {len(hits)}/{len(rows)} samples solved by origin "
        "— preserve the parent scaffolding earning these."
    )


@signal(
    "intractable_samples",
    kind=InjectionKind.MEASUREMENT,
    description="Cumulative cycle-wide miss set — samples no candidate has solved yet this cycle.",
    char_cap=None,
)
def _r_intractable_samples(b: InjectionBundle) -> str:
    """Cumulative-best misses from `current_results` — the cluster L1 must attack next.
    Mutations that don't address any of them are unlikely to break the plateau. Fenced.
    """
    rows = b.trajectory_misses
    if not rows:
        return ""
    shown = rows[:INTRACTABLE_SAMPLES_RENDER_CAP]
    lines = [
        f"INTRACTABLE SAMPLES ({len(rows)} samples the trajectory still misses — "
        "the cluster the next mutation must attack):"
    ]
    for r in shown:
        sid = r.get("sample_id")
        gt = (r.get("ground_truth") or "")[:30]
        lines.append(f"  [#{sid}] {_query_stem(r)} → GT: {gt}")
    if len(rows) > INTRACTABLE_SAMPLES_RENDER_CAP:
        lines.append(
            f"  … +{len(rows) - INTRACTABLE_SAMPLES_RENDER_CAP} more trajectory misses not shown."
        )
    return fence_untrusted("\n".join(lines))


@signal(
    "archive_top_runs",
    kind=InjectionKind.MEASUREMENT,
    description=(
        "Top-K historical runs across the dataset's archive — anchor the optimizer "
        "against the best composite ever scored instead of re-discovering it."
    ),
    char_cap=None,
)
def _r_archive_top_runs(b: InjectionBundle) -> str:
    """Top historical runs — anchor "beat run X (acc=Y%, comp=Z)" instead of re-discovering a
    peak already on disk. Empty until `AxisIndex.refresh` has folded at least one run.
    """
    if b.axes is None:
        return ""
    runs = b.axes.top_runs(3)
    if not runs:
        return ""
    lines = [f"HISTORICAL BEST (top {len(runs)} runs across the dataset's archive):"]
    for i, r in enumerate(runs, 1):
        label = r.name or r.run_id
        lines.append(
            f"  #{i}  acc={r.accuracy:.1%}  comp={r.composite:.3f}  {r.hits}/{r.total}  run={label}"
        )
    return "\n".join(lines)


@signal(
    "rare_hit_samples",
    kind=InjectionKind.MEASUREMENT,
    description=(
        "Samples cracked by ≤3 of ≥10 attempts — names the run(s) that hit them "
        "(recipe pointers). Zero-hit samples surface as capacity-bound."
    ),
    char_cap=None,
)
def _r_rare_hit_samples(b: InjectionBundle) -> str:
    """Samples cracked by ≤3 of ≥10 attempts — unlock-pattern pointers. Zero-hit samples surface
    as `capacity-bound` (stop engineering for them).
    """
    if b.axes is None:
        return ""
    rare = b.axes.sample_index.rare_hit_samples(max_hits=3, min_observations=10)
    if not rare:
        return ""
    lines = ["RARE-HIT SAMPLES (cracked by ≤3 of ≥10 attempts — replicate the unlock pattern):"]
    for sid, query, hits, total, hit_run_ids in rare[:6]:
        if hits == 0:
            lines.append(f"  [#{sid}] {query}… → 0/{total} (capacity-bound; do not engineer for)")
        else:
            run_str = ", ".join(rid[:24] for rid in hit_run_ids[:2])
            lines.append(f"  [#{sid}] {query}… → {hits}/{total} hit by {run_str}")
    return fence_untrusted("\n".join(lines))
