"""Signal renderers + :data:`INJECTIONS` registry.

Every renderer has the uniform signature ``(InjectionBundle) -> str`` and
is layer-agnostic — same render for every layer that subscribes. Per-layer
specialisation is the kind of complexity the dispatch hub exists to remove.

To add an input to any prompt, add an entry here. Anything else is drift.
"""

from __future__ import annotations

import json
from typing import Any

from promptpotter.application.optimization.dispatch.hub.bundle import (
    AXES_ENUM_PREVIEW,
    NEAR_MISS_RENDER_CAP,
    PIPELINE_PARAM_CATALOGUE_MODEL_CAP,
    PROMPT_BLOAT_CHARS,
    RUNTIME_FAILURE_RECENCY_WINDOW,
    SAMPLE_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    _Injection,
    fence_untrusted,
)
from promptpotter.domain.escalation_signals import RuntimeFailure
from promptpotter.domain.l1_layout import L1_POSSIBLE
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS


def _rf_matches_current_config(rf: RuntimeFailure, pipeline_params: dict[str, dict]) -> bool:
    """Filter ACCUMULATED runtime failures to the current backend config.

    A RuntimeFailure carries an ``observed_config`` snapshot of the
    offending node's pipeline_params at the time it fired. When a
    cycle's overlay changes (e.g. provider/model swap mid-experiment),
    accumulated failures from the prior overlay become stale evidence
    — keeping them in L2's prompt mis-steers the framing refinement.

    The comparison is on operator-locked keys
    (``PARAM_FORBIDDEN_KEYS``: provider, model). The node is parsed
    from ``rf.dominant_warning`` (``"<node>:<warning>"``); if the node
    isn't in the current pipeline_params, the failure is dropped.
    """
    node = (rf.dominant_warning or "").split(":", 1)[0]
    if not node:
        return True
    current = pipeline_params.get(node)
    if not isinstance(current, dict):
        return False
    observed = rf.observed_config or {}
    return all(observed.get(k) == current.get(k) for k in PARAM_FORBIDDEN_KEYS if k in observed)


# ---------------------------------------------------------------------------
# Signal renderers — uniform ``(InjectionBundle) -> str`` signature, layer-agnostic.
# ---------------------------------------------------------------------------


def _r_plan(b: InjectionBundle) -> str:
    return f"PLAN:\n{b.opt_sp.plan}" if b.opt_sp.plan else ""


def _r_l3_to_l2_note(b: InjectionBundle) -> str:
    """Sticky L3→L2 pointer. Mounted only in L2's template; absent from
    ``L1_POSSIBLE`` so L1 never sees it."""
    note = b.opt_sp.wounds.l3_note
    return f"L3 NOTE TO L2:\n{note}" if note else ""


def _r_rendered_prompt(b: InjectionBundle) -> str:
    rendered = b.opt_sp.render()
    return f"CURRENT PROMPT:\n---\n{rendered}\n---" if rendered else ""


# Single-entry cache keyed on (pipeline_schema identity, forbidden_axes_strict).
# The schema is session-immutable, so the rendered string is byte-identical
# across every round of a session under the same lock state. Skipping the
# recompute saves CPU and — more importantly for small models — guarantees
# the same text appears verbatim in every prompt, which trains attention to
# skip past the static block cheaply. id() is sufficient: a session-long
# schema can't be GC'd-and-reused mid-run. The lock flag is part of the key
# because it gates whether MODELS appears.
_pipeline_param_catalogue_last: tuple[int, bool, str] | None = None


def _r_pipeline_param_catalogue(b: InjectionBundle) -> str:
    """Pipeline-param search-space menu — name + ≤4-value enum hint, no full dump.

    Carries the *available* options (allowed enums + models) the LLM picks
    from when proposing ``pipeline_params_override`` — symmetric with
    ``l1_signal_catalogue`` (the menu L2 picks from for L1's layout).
    """
    global _pipeline_param_catalogue_last
    schema = b.pipeline_schema
    if schema is None:
        return ""
    schema_id = id(schema)
    lock = b.forbidden_axes_strict
    if (
        _pipeline_param_catalogue_last is not None
        and _pipeline_param_catalogue_last[0] == schema_id
        and _pipeline_param_catalogue_last[1] == lock
    ):
        return _pipeline_param_catalogue_last[2]
    npk = schema.node_param_keys()
    if not npk:
        return ""
    lines = ["PIPELINE PARAM CATALOGUE (use only these — do not invent):"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions or {}
        enums = node.param_allowed_values or {}
        bits: list[str] = []
        for p in sorted(params):
            allowed = enums.get(p)
            if allowed:
                shown = list(allowed)[:AXES_ENUM_PREVIEW]
                preview = ", ".join(str(x) for x in shown)
                if len(allowed) > AXES_ENUM_PREVIEW:
                    preview += f", … (+{len(allowed) - AXES_ENUM_PREVIEW})"
                bits.append(f"{p} [{preview}]")
            elif desc := descs.get(p):
                bits.append(f"{p} ({desc[:40]})")
            else:
                bits.append(p)
        lines.append(f"  {node_name}: {', '.join(bits)}")
    # Suppress MODELS catalogue when model is operator-locked
    # (forbidden_axes_strict). Advertising a list that the validator will
    # immediately reject just costs L1 a candidate slot per round to Wound 1.
    if schema.available_models and not b.forbidden_axes_strict:
        lines.append("MODELS:")
        lines.append(
            "  " + ", ".join(list(schema.available_models)[:PIPELINE_PARAM_CATALOGUE_MODEL_CAP])
        )
    result = "\n".join(lines)
    _pipeline_param_catalogue_last = (schema_id, lock, result)
    return result


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

    if d.evolution_rows:
        line = f"TRAJECTORY: {d.trajectory}"
        if d.trajectory_description:
            line += f" — {d.trajectory_description}"
        parts.append(line)
        if len(d.evolution_rows) > 1:
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

    if d.termination_dist:
        td_lines = ["PIPELINE HEALTH:"]
        for step, count in sorted(d.termination_dist.items(), key=lambda x: -x[1]):
            td_lines.append(f"  terminate@{step}: {count}")
        td_lines.append(f"  error_rate: {d.error_rate:.0%} | warning_rate: {d.warning_rate:.0%}")
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

    if d.prompt_chars:
        bloat = " — bloated; favour compression" if d.prompt_chars > PROMPT_BLOAT_CHARS else ""
        parts.append(f"PROMPT SIZE: {d.prompt_chars} chars{bloat}")

    if (po := d.probe_outcome) is not None:
        parts.append(
            f"PROBE OUTCOME: axis={po.axis_tested} subset={po.target_subset_size} "
            f"hit_rate={po.hit_rate:.0%} delta={po.delta_vs_full:+.1%}"
        )

    if parts:
        sections.append(fence_untrusted("\n\n".join(parts)))
    return "\n\n".join(sections)


def _r_validation_failures(b: InjectionBundle) -> str:
    """Wound 1 — L1 parse-time deterministic validator.

    Fenced because it echoes LLM-proposed values (``vf.value``), which
    the LLM could have written as anything.
    """
    failures = b.opt_sp.wounds.validation_failures
    if not failures:
        return ""
    sec = ["L1 VALIDATION FAILURES (last round produced invalid variants):"]
    for vf in failures:
        allowed_str = ", ".join((vf.allowed or [])[:5])
        sec.append(
            f"  axis={vf.axis} value={vf.value!r} reason={vf.reason}"
            + (f" allowed=[{allowed_str}]" if allowed_str else "")
        )
    return fence_untrusted("\n".join(sec))


def _r_runtime_failures(b: InjectionBundle) -> str:
    """Wound 2 — DegradationCheck mid-eval evidence (per-candidate runtime
    failures from ``DegradationCheck``). Fenced because it echoes pipeline
    warning strings.

    ACCUMULATED entries are filtered against the current
    ``pipeline_params[node].{provider, model}``: a runtime failure
    observed under a now-superseded backend config (e.g. yesterday's
    openrouter/gpt-oss-20b carried forward into today's
    groq/gpt-oss-120b cycle) is dropped before injection. NEW entries
    (first-seen this round) always pass — they describe the failure
    being heard right now.
    """
    runtime_failures = b.opt_sp.wounds.runtime_failures
    parts: list[str] = []

    if runtime_failures:
        round_num = b.cycle_slice.round_num
        cutoff = round_num - RUNTIME_FAILURE_RECENCY_WINDOW + 1
        new_rfs = [rf for rf in runtime_failures if rf.first_seen_round == round_num]
        acc_rfs = [
            rf
            for rf in runtime_failures
            if rf.first_seen_round != round_num
            and rf.first_seen_round >= cutoff
            and _rf_matches_current_config(rf, b.cycle_slice.pipeline_params)
        ]
        dropped = sum(1 for rf in runtime_failures if rf.first_seen_round < cutoff)
        sec = ["RUNTIME FAILURES (candidates ran but degraded):"]
        if new_rfs:
            sec.append("  NEW (this round):")
            for rf in new_rfs:
                sec.extend(_format_runtime_failure_lines(rf))
        if acc_rfs:
            sec.append(f"  ACCUMULATED ({len(acc_rfs)} surviving from earlier rounds):")
            for rf in acc_rfs:
                sec.extend(_format_runtime_failure_lines(rf))
        if dropped:
            sec.append(
                f"  … {dropped} older failures suppressed "
                f"(first-seen >{RUNTIME_FAILURE_RECENCY_WINDOW} rounds ago)."
            )
        parts.append("\n".join(sec))

    if not parts:
        return ""
    return fence_untrusted("\n\n".join(parts))


def _r_l2_guard_breaches(b: InjectionBundle) -> str:
    """Wound 4 — L2_CONTEXT post-parse guard outcomes.

    Set by ``run_l2_output_validators`` after parsing L2's LLM output;
    non-empty triggers immediate L3 fire. "Guard breach" — programmatic
    guards on L2's LLM output, distinct from ``validation_failures`` /
    ``runtime_failures`` (pipeline evidence from L1 candidate runs).
    Plain (only ``validator_id`` from a controlled registry + ``score``
    float — no untrusted content).
    """
    outcomes = b.opt_sp.wounds.l2_guard_breaches
    if not outcomes:
        return ""
    lines = ["L2 GUARD BREACHES (post-parse guards on L2's output caught thrashing):"]
    lines.extend(f"  • {o.validator_id} (score={o.score:.2f})" for o in outcomes)
    return "\n".join(lines)


def _r_l3_guard_breaches(b: InjectionBundle) -> str:
    """L3_PLAN post-parse guard outcomes — L3's self-healing evidence.

    L3 sees its own past breaches to avoid repeating them. Plain (only
    ``validator_id`` + ``score``).
    """
    outcomes = b.opt_sp.wounds.l3_guard_breaches
    if not outcomes:
        return ""
    lines = ["L3 GUARD BREACHES (post-parse guards on L3's output caught thrashing):"]
    lines.extend(f"  • {o.validator_id} (score={o.score:.2f})" for o in outcomes)
    return "\n".join(lines)


def _format_runtime_failure_lines(rf: Any) -> list[str]:
    """Two-line render of one RuntimeFailure for ``runtime_failures``."""
    rate_pct = round(float(rf.degraded_rate) * 100)
    cfg_parts = [f"{k}={v}" for k, v in (rf.observed_config or {}).items() if k != "prompt"]
    cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
    label = (rf.candidate_label or "")[:60]
    head = (
        f"    ⚠ {label} — {rate_pct}% degraded on {rf.total_scored}, dom={rf.dominant_warning}"
        if label
        else f"    ⚠ {rf.dominant_warning} — {rate_pct}% degraded on {rf.total_scored}"
    )
    return [head, f"      observed_config: {cfg_str}"]


def _r_task_context(b: InjectionBundle) -> str:
    tc = b.opt_sp.task_context
    if not tc:
        return ""
    skip = {"raw_description", "upstream_context", "downstream_context"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return ""
    return "TASK CONTEXT:\n" + "\n".join(f"  {k}: {v}" for k, v in pairs)


def format_l1_critique_for_prompt(critique: dict) -> str:
    """L1 critique dict → compact text (summary + priority_fix + axes + top-5 highlights).

    Shared between the ``critique`` dispatch signal (which feeds it the raw
    round dict) and downstream display sites (tracing emit, view factories,
    review). One formatter — same wire shape everywhere.
    """
    if not critique:
        return ""
    parts: list[str] = []
    if s := critique.get("summary"):
        parts.append(s)
    if pf := critique.get("priority_fix"):
        parts.append(f"Priority fix: {pf}")
    if sa := critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Key failures:")
        for h in fh[:5]:
            parts.append(f"  {h}")
    return "\n".join(parts)


def _r_critique(b: InjectionBundle) -> str:
    """Compact view of the most recent L1_CRITIQUE output dict."""
    return format_l1_critique_for_prompt(b.digest.critique or {})


def _r_l1_overrides(b: InjectionBundle) -> str:
    return f"CURRENT L1 CONFIG: {json.dumps(b.opt_sp.l1_overrides)}"


def _r_l1_signal_catalogue(b: InjectionBundle) -> str:
    """Names only — sorted ``L1_POSSIBLE``. L2 may pick from this menu."""
    return "L1 SIGNAL MENU (placeholders L2 may use in l1_layout):\n  " + "\n  ".join(
        sorted(L1_POSSIBLE)
    )


# Order in which AxisIndex.digest() keys are surfaced to the optimizer LLM.
# Effect-driven items first (rankings, top values, trends, exhausted axes)
# so attention lands on what to mutate; sample-side findings second
# (persistent failures, clusters, bottleneck); narrative tail last
# (improvement attribution). Keys absent from the digest are skipped.
_AXIS_MEMORY_LABEL_ORDER: tuple[str, ...] = (
    "axis_rankings",
    "top_values",
    "value_trends",
    "exhausted_axes",
    "persistent_failures",
    "failure_clusters",
    "bottleneck_distribution",
    "failure_group_insights",
    "dead_queries",
    "discriminating_queries",
    "volatile_queries",
    "improvement_attribution",
)


def _r_axis_memory(b: InjectionBundle) -> str:
    """Cross-cycle axis & sample memory derived from the MeasurementArchive.

    Wraps :meth:`AxisIndex.digest` — the axis-keyed view that aggregates
    effect sizes, persistent failures, failure clusters, value trends,
    and exhausted axes across this dataset's prior cycles. Empty when
    ``cycle.axes`` isn't initialised (pre-first-round) or the digest
    yields nothing. The formatters this signal aggregates already exist
    in ``intelligence/indexes/format.py``; the wiring here is the only
    new code — no new computation, no new query path.
    """
    if b.axes is None:
        return ""
    digest = b.axes.digest()
    if not digest:
        return ""
    lines = ["AXIS MEMORY (cross-cycle observations from MeasurementArchive):"]
    for key in _AXIS_MEMORY_LABEL_ORDER:
        val = digest.get(key)
        if val is None:
            continue
        label = key.replace("_", " ")
        lines.append(f"  {label}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Signal registry — lookup by name from layouts / templates / fill_*.
# ---------------------------------------------------------------------------


INJECTIONS: dict[str, _Injection] = {
    "plan": _Injection(
        "plan",
        InjectionKind.TRACE,
        _r_plan,
        "L3's strategic plan text. Persistent until next L3 fire.",
    ),
    "l3_to_l2_note": _Injection(
        "l3_to_l2_note",
        InjectionKind.DIRECTIVE,
        _r_l3_to_l2_note,
        "Sticky L3→L2 pointer. Mounted only in L2's template; absent from L1.",
    ),
    "rendered_prompt": _Injection(
        "rendered_prompt",
        InjectionKind.TRACE,
        _r_rendered_prompt,
        "Current best searchpoint's compiled prompt body.",
    ),
    "pipeline_param_catalogue": _Injection(
        "pipeline_param_catalogue",
        InjectionKind.DERIVED,
        _r_pipeline_param_catalogue,
        "Pipeline-param menu: name + ≤4-value enum hint per node, plus available models.",
    ),
    "diagnostics": _Injection(
        "diagnostics",
        InjectionKind.DERIVED,
        _r_diagnostics,
        "Layer-agnostic round readout: STATUS header + RoundDiagnostics body.",
    ),
    "validation_failures": _Injection(
        "validation_failures",
        InjectionKind.MEASUREMENT,
        _r_validation_failures,
        "Wound 1: L1 parse-time validator failures (per-axis, per-value).",
    ),
    "runtime_failures": _Injection(
        "runtime_failures",
        InjectionKind.MEASUREMENT,
        _r_runtime_failures,
        "Wound 2: DegradationCheck mid-eval evidence — per-candidate runtime failures.",
    ),
    "l2_guard_breaches": _Injection(
        "l2_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l2_guard_breaches,
        "Wound 4: L2_CONTEXT post-parse guard outcomes; non-empty force-triggers L3 heal.",
    ),
    "l3_guard_breaches": _Injection(
        "l3_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l3_guard_breaches,
        "L3_PLAN post-parse guard outcomes. L3 reads its own past breaches.",
    ),
    "task_context": _Injection(
        "task_context",
        InjectionKind.TRACE,
        _r_task_context,
        "Persistent task framing dict refined by L2; broadcast to all four prompts.",
    ),
    "critique": _Injection(
        "critique",
        InjectionKind.TRACE,
        _r_critique,
        "Compact view of the most recent L1_CRITIQUE LLM output dict.",
    ),
    "l1_overrides": _Injection(
        "l1_overrides",
        InjectionKind.TRACE,
        _r_l1_overrides,
        "Current L1 runtime knobs (creativity, n_variants, etc.) as JSON.",
    ),
    "l1_signal_catalogue": _Injection(
        "l1_signal_catalogue",
        InjectionKind.DERIVED,
        _r_l1_signal_catalogue,
        "L1 SIGNAL MENU: sorted L1_POSSIBLE placeholder names L2 may use in l1_layout.",
    ),
    "axis_memory": _Injection(
        "axis_memory",
        InjectionKind.DERIVED,
        _r_axis_memory,
        "Cross-cycle axis-keyed digest from AxisIndex: rankings, persistent failures, "
        "failure clusters, value trends, exhausted axes.",
    ),
}


__all__ = ["INJECTIONS", "format_l1_critique_for_prompt"]
