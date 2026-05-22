"""Diagnostic + cross-cycle memory injection renderers.

Two themes: the per-round readout (``_r_diagnostics`` — STATUS header +
``RoundDiagnostics`` body) and the cross-cycle archive memory derived
from the ``MeasurementArchive`` via ``AxisIndex`` (axis rankings,
historical best runs, rare-hit samples, intractable-sample clusters).
All share the uniform ``(InjectionBundle) -> str`` renderer signature.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.bundle import (
    INTRACTABLE_SAMPLES_RENDER_CAP,
    NEAR_MISS_RENDER_CAP,
    SAMPLE_RENDER_CAP,
    InjectionBundle,
    fence_untrusted,
)


def _r_diagnostics(b: InjectionBundle) -> str:
    """Layer-agnostic round readout: STATUS header (cycle counters, plain) +
    fenced body (RoundDiagnostics dataset content).

    STATUS lists round, parent fitness, best fitness, and per-layer stall +
    fire counters. It renders even before the first round closes (when
    ``digest.diagnostics`` is None), since cycle counters are always
    populated. The fenced body follows only when ``RoundDiagnostics`` is
    available — wrapped in ``<UNTRUSTED_DATASET_CONTENT>`` because it
    echoes raw queries / GTs / pipeline warnings.
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

    # TRAJECTORY line: skip when only 1 evolution row — the "Too few rounds
    # to classify" trajectory_description carries no signal and the EVOLUTION
    # table also requires len>1 to render anything useful, so the whole block
    # is dead weight on R1.
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

    if d.anomalies:
        parts.append("ANOMALIES:\n  " + "\n  ".join(d.anomalies))

    if d.n_valid:
        rb = d.rank_buckets
        # Suppress the rank block when every query is ``not_found`` —
        # the pipeline has no ranker / candidate_source node so ``rank``
        # is structurally undefined for this schema. Reporting
        # ``top-1: 0% top-3: 0% ... not_found: 20`` only steers L2
        # toward hallucinated "fix the ranker" advice. The block is
        # only meaningful when at least one query landed at a real rank.
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

    # PIPELINE HEALTH: skip when nothing's wrong AND only one terminator —
    # a single-node pipeline with 0% errors / 0% warnings is the default
    # success state, no signal to surface. Re-emit when error_rate or
    # warning_rate is non-zero OR multiple termination steps appear (pipeline
    # is branching across nodes, useful telemetry).
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

    pop_bits: list[str] = []
    if d.l1_diversity != 1.0:
        pop_bits.append(f"diversity={d.l1_diversity:.2f}")
    if d.cache_share > 0:
        pop_bits.append(f"cache_share={d.cache_share:.0%}")
    if pop_bits:
        parts.append("POPULATION: " + ", ".join(pop_bits))

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

    if (po := d.probe_outcome) is not None:
        parts.append(
            f"PROBE OUTCOME: axis={po.axis_tested} subset={po.target_subset_size} "
            f"hit_rate={po.hit_rate:.0%} delta={po.delta_vs_full:+.1%}"
        )

    if parts:
        sections.append(fence_untrusted("\n\n".join(parts)))
    return "\n\n".join(sections)


# Order in which AxisIndex.digest() keys are surfaced to the optimizer LLM.
# Effect-driven items first (rankings, top values, trends, exhausted axes)
# so attention lands on what to mutate; sample-side findings second
# (persistent failures, clusters, bottleneck); narrative tail last
# (improvement attribution). Keys absent from the digest are skipped.
# ``value_trends`` removed: peaked-axis tags are now inlined into the
# ``axis_rankings`` entries themselves (see
# ``intelligence/indexes/format._fmt_axis_rankings``). A separate trend
# line on the side let L1 read "highest effect → mutate" and "peaked →
# don't" as two independent facts on the same axis; collapsing to one
# annotated line removes the contradiction.
_AXIS_MEMORY_LABEL_ORDER: tuple[str, ...] = (
    "axis_rankings",
    "top_values",
    "exhausted_axes",
    "persistent_failures",
    "failure_clusters",
)


def _critique_is_all_prompt_field(critique: dict | None) -> bool:
    """True when every entry in ``critique.suggested_axes`` is a prompt-field axis.

    Drives the axis-memory filter: when the last L1_CRITIQUE flagged
    only semantic failures (and therefore only prompt-field axes to
    mutate), param-axis rankings in the cross-cycle digest are noise
    that pulls L2 toward param mutations the critique already vetoed.
    Empty / missing ``suggested_axes`` returns False — without an
    explicit critique steer we keep all rows visible.
    """
    from promptpotter.config.settings import PROMPT_STRING_FIELDS

    sa = (critique or {}).get("suggested_axes") or []
    if not sa:
        return False
    prompt_axes = set(PROMPT_STRING_FIELDS)
    return all(a in prompt_axes for a in sa)


def _filter_axis_rankings_to_prompt(value: str) -> str:
    """Keep only axis-rankings entries whose axis name maps to a prompt-field.

    The digest's ``axis_rankings`` value is a semicolon-separated string
    like ``"llm_only.prompt (effect=0.208, ...); llm_only.max_tokens
    (PEAKED ...); steps (effect=0.000, dead)"``. We split on ``"; "``,
    take the axis-name prefix before ``" ("``, and keep entries whose
    last dotted component is ``"prompt"`` (the catch-all axis that
    rolls up prompt-field mutations on single-node pipelines). All
    scalar-param entries (``max_tokens``, ``temperature``,
    ``reasoning_effort``, etc.) drop out. Returns empty string when no
    entries survive — caller handles the empty case.
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


def _r_axis_memory(b: InjectionBundle) -> str:
    """Cross-cycle axis & sample memory derived from the MeasurementArchive.

    Wraps :meth:`AxisIndex.digest` — the axis-keyed view that aggregates
    effect sizes, persistent failures, failure clusters, value trends,
    and exhausted axes across this dataset's prior cycles. Empty when
    ``cycle.axes`` isn't initialised (pre-first-round) or the digest
    yields nothing. The formatters this signal aggregates already exist
    in ``intelligence/indexes/format.py``; the wiring here is the only
    new code — no new computation, no new query path.

    Critique-aware filter: when the latest L1_CRITIQUE flagged only
    semantic failures (suggested_axes all in PROMPT_STRING_FIELDS),
    param-axis rankings in the digest are noise — they pull L2 toward
    param mutations the critique already vetoed. We strip
    ``axis_rankings`` to prompt-axis entries and suppress
    ``top_values`` in that case, replacing the section header with a
    note explaining the redaction. Sample-side rows
    (persistent_failures, failure_clusters, etc.) stay visible — they
    don't suggest axis mutations.
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


def _query_stem(row: dict, n: int = 70) -> str:
    q = (row.get("query") or "").replace("\n", " ").strip()
    return q[:n]


def _r_origin_strengths(b: InjectionBundle) -> str:
    """Origin-hit count — one-line summary.

    The hit count is the actionable signal ("don't strip scaffolding
    earning these N"). Enumerated samples added bytes without adding
    decision input — L1 doesn't pick which origin-hit to preserve, it
    preserves all of them. Cited from
    ``cycle.tracking.origin_per_sample_results``.
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


def _r_intractable_samples(b: InjectionBundle) -> str:
    """Samples the cumulative best trajectory still misses.

    Sourced from ``cycle.tracking.current_results`` (live, cycle-wide
    cumulative per-sample state) filtered to ``hit=False`` — the set
    of samples no candidate in any prior round has converted. L1 should
    treat these as the next cluster to attack; mutations that don't
    address any of them are unlikely to break the plateau. Empty when
    every sample has been hit at least once. Fenced (echoes queries).
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


def _r_archive_top_runs(b: InjectionBundle) -> str:
    """Top historical runs across the dataset's archive — anchor against the best.

    Surfaces the highest-composite runs ever scored on this dataset so the
    optimizer reasons "beat run X (acc=Y%, comp=Z)" instead of re-discovering
    a peak that's already on disk. Empty until ``AxisIndex.refresh`` has
    folded at least one run.
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


def _r_rare_hit_samples(b: InjectionBundle) -> str:
    """Samples cracked by ≤3 of ≥10 attempts — the unlock-pattern pointers.

    Each rare hit names the run(s) that cracked the sample. Samples with
    zero hits surface as ``capacity-bound`` (the optimizer should stop
    engineering for them). Empty until the archive has accumulated at
    least 10 measurements per sample.
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
