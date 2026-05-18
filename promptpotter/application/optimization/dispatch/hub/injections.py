"""Signal renderers + :data:`INJECTIONS` registry.

Every renderer has the uniform signature ``(InjectionBundle) -> str`` and
is layer-agnostic — same render for every layer that subscribes. Per-layer
specialisation is the kind of complexity the dispatch hub exists to remove.

To add an input to any prompt, add an entry here. Anything else is drift.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from promptpotter.application.optimization.dispatch.hub.auto_rules import (
    AUTO_RULES,
    BUILTIN_EXAMPLES,
)
from promptpotter.application.optimization.dispatch.hub.bundle import (
    AXES_ENUM_PREVIEW,
    INTRACTABLE_SAMPLES_RENDER_CAP,
    NEAR_MISS_RENDER_CAP,
    PIPELINE_PARAM_CATALOGUE_MODEL_CAP,
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

# Param-axis names recognised by the continuous_envelope auto-trigger.
# When L1_CRITIQUE.suggested_axes names one of these, render the envelope rule.
_NUMERIC_PARAM_AXES: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "top_p", "reasoning_effort"}
)

# LaTeX corruption markers — match 'oxed{' NOT preceded by '\b' (legitimate
# `\boxed{` is preceded by '\b' so the lookbehind skips it) OR 'athematical'
# at a word boundary NOT preceded by 'm' (so 'mathematical' is skipped).
_LATEX_CORRUPTION_RE = re.compile(r"(?<!\\b)oxed\{|(?<![a-zA-Z])athematical")


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
    if not runtime_failures:
        return ""
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
    if not (new_rfs or acc_rfs or dropped):
        return ""
    sec = ["RUNTIME FAILURES (do not re-propose):"]
    if new_rfs:
        sec.append("  NEW:")
        sec.extend(_format_runtime_failure_group(new_rfs))
    if acc_rfs:
        sec.append(f"  ACCUMULATED ({len(acc_rfs)} from earlier rounds):")
        sec.extend(_format_runtime_failure_group(acc_rfs))
    if dropped:
        sec.append(f"  … {dropped} older suppressed (>{RUNTIME_FAILURE_RECENCY_WINDOW} rounds).")
    return fence_untrusted("\n".join(sec))


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
    """Two-line render of one RuntimeFailure.

    Used for single-entry groups; multi-entry groups collapse via
    :func:`_format_runtime_failure_group` to keep small-model prompts lean.
    """
    rate_pct = round(float(rf.degraded_rate) * 100)
    cfg_parts = [f"{k}={v}" for k, v in (rf.observed_config or {}).items() if k != "prompt"]
    cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
    label = (rf.candidate_label or "")[:60]
    head = (
        f"    BLOCKED: {label} — {rate_pct}% degraded on {rf.total_scored}, dom={rf.dominant_warning}"
        if label
        else f"    BLOCKED: {rf.dominant_warning} — {rate_pct}% degraded on {rf.total_scored}"
    )
    return [head, f"      cfg: {cfg_str}"]


def _format_runtime_failure_group(rfs: list[Any]) -> list[str]:
    """Cluster RFs by (dominant_warning, provider, model); compact-render clusters of 2+.

    Each accumulated failure carries the same warning + same provider/model on
    different param-axis values is one discovery, not five. Rendering each as
    its own two-line entry wastes ~70% of the runtime_failures block on small
    models. We group, keep one representative label, and list the varying
    params with their distinct values.

    Single-entry groups fall through to :func:`_format_runtime_failure_lines`
    unchanged.
    """
    clusters: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for rf in rfs:
        cfg = rf.observed_config or {}
        key = (
            rf.dominant_warning or "",
            str(cfg.get("provider", "")),
            str(cfg.get("model", "")),
        )
        clusters[key].append(rf)

    out: list[str] = []
    for (warning, provider, model), group in clusters.items():
        if len(group) == 1:
            out.extend(_format_runtime_failure_lines(group[0]))
            continue
        backend = f"{provider}/{model}" if (provider or model) else "(backend n/a)"
        out.append(f"    BLOCKED x{len(group)} — dom={warning}, model={backend}")
        varied: dict[str, set[str]] = defaultdict(set)
        for rf in group:
            for k, v in (rf.observed_config or {}).items():
                if k in ("provider", "model", "prompt"):
                    continue
                varied[k].add(str(v))
        varied_parts: list[str] = []
        for k, vs in varied.items():
            if len(vs) == 1:
                varied_parts.append(f"{k}={next(iter(vs))}")
                continue
            try:
                sorted_vs = sorted(vs, key=float)
            except (ValueError, TypeError):
                sorted_vs = sorted(vs)
            varied_parts.append(f"{k}∈{{{','.join(sorted_vs)}}}")
        if varied_parts:
            out.append(f"      varied: {', '.join(varied_parts)}")
    return out


def _r_task_context(b: InjectionBundle) -> str:
    tc = b.opt_sp.task_context
    if not tc:
        return ""
    skip = {"raw_description", "upstream_context", "downstream_context"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return ""
    return "TASK CONTEXT:\n" + "\n".join(f"  {k}: {v}" for k, v in pairs)


def _valid_axis_set(schema: Any) -> set[str]:
    """Axes the schema legitimately exposes: prompt fields + node names + param keys.

    L2's free-form ``suggested_axes`` may hallucinate names that aren't in
    the pipeline (e.g. ``prompt_size``); rendering those into the next
    round's prompt seeds the cycle with a non-existent mutation target.
    Match the union the operator can actually act on.
    """
    from promptpotter.config.settings import PROMPT_STRING_FIELDS

    out: set[str] = set(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}
    if schema is None:
        return out
    for node in getattr(schema, "nodes", ()):
        name = getattr(node, "name", "")
        if name:
            out.add(name)
        for pk in getattr(node, "param_keys", ()) or ():
            out.add(pk)
            if name:
                out.add(f"{name}.{pk}")
    return out


def format_l1_critique_for_prompt(critique: dict, pipeline_schema: Any = None) -> str:
    """L1 critique dict → compact text for L1_GENERATE + L2_CONTEXT consumption.

    Three load-bearing fields only: ``priority_fix`` (axis+change+quoted
    failure), ``suggested_axes`` (schema-filtered), ``failure_highlights``
    (per-sample evidence). Prose summary / positive_critique /
    negative_critique were dropped from the schema — see L1CritiqueOutput.

    When ``pipeline_schema`` is provided, ``suggested_axes`` is filtered
    against the schema's known axis names so a hallucinated axis (e.g.
    ``prompt_size``) doesn't get re-rendered into the next round's L2
    brief.
    """
    if not critique:
        return ""
    parts: list[str] = []
    if pf := critique.get("priority_fix"):
        parts.append(f"Fix: {pf}")
    sa = critique.get("suggested_axes") or []
    if sa:
        if pipeline_schema is not None:
            valid = _valid_axis_set(pipeline_schema)
            sa = [a for a in sa if a in valid]
        if sa:
            parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Failures:")
        for h in fh[:3]:
            parts.append(f"  {h}")
    return "\n".join(parts)


def _r_critique(b: InjectionBundle) -> str:
    """Compact view of the most recent L1_CRITIQUE output dict."""
    return format_l1_critique_for_prompt(b.digest.critique or {}, b.pipeline_schema)


def _r_l1_overrides(b: InjectionBundle) -> str:
    if not b.opt_sp.l1_overrides:
        return ""
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
            f"  #{i}  acc={r.accuracy:.1%}  comp={r.composite:.3f}  "
            f"{r.hits}/{r.total}  run={label}"
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


def _detect_auto_triggers(b: InjectionBundle) -> list[str]:
    """Walk the deterministic auto-trigger conditions in fixed order.

    The order is the order the rules render in L1's prompt. Each clause
    is a cheap read off the bundle — no allocation beyond the small
    intermediate strings.
    """
    triggers: list[str] = []
    if b.axes is not None and b.axes.peaked_axes():
        triggers.append("peaked_axis")
    if b.opt_sp.wounds.runtime_failures:
        triggers.append("runtime_failure")
    sa = (b.digest.critique or {}).get("suggested_axes") or []
    if any(a in _NUMERIC_PARAM_AXES for a in sa):
        triggers.append("continuous_envelope")
    key_challenges = b.opt_sp.task_context.key_challenges or ""
    if "targeting L1 axis" in key_challenges:
        triggers.append("chain_bind")
    if b.opt_sp.wounds.l2_guard_breaches:
        triggers.append("l2_stall_diversity")
    if _LATEX_CORRUPTION_RE.search(b.opt_sp.render()):
        triggers.append("latex_repair")
    if any(vf.reason == "forbidden_axis" for vf in b.opt_sp.wounds.validation_failures):
        triggers.append("forbidden_axis_attempted")
    return triggers


def _r_l1_supplemental_rules(b: InjectionBundle) -> str:
    """Auto-triggered situational rules + L2-authored rules.

    Renders each active auto-trigger's canonical rule body (from
    :data:`AUTO_RULES`) followed by every L2-authored
    :class:`L1SupplementalRule` on ``opt_sp.l1_supplemental_rules`` with
    its citation in brackets. Returns empty when neither source has
    anything to say — variable absent ⇒ L1's instruction omits the
    block entirely.
    """
    rendered: list[tuple[str, str]] = []
    for trigger_id in _detect_auto_triggers(b):
        body = AUTO_RULES.get(trigger_id)
        if body:
            rendered.append((trigger_id, body))
    for rule in b.opt_sp.l1_supplemental_rules:
        body = f"{rule.body} [citation: {rule.citation}]"
        rendered.append((rule.rule_id, body))
    if not rendered:
        return ""
    lines = ["SITUATIONAL RULES (apply only when the cited evidence is present):"]
    for trigger_id, body in rendered:
        lines.append(f"  • [{trigger_id}] {body}")
    return "\n".join(lines)


def _r_l1_situational_examples(b: InjectionBundle) -> str:
    """Built-in + L2-authored worked examples for currently-active triggers.

    L2-authored examples whose ``trigger_id`` matches neither an
    auto-trigger that fired this round nor a currently-authored
    supplemental rule are silently filtered — the example would land in
    L1's prompt without the rule it illustrates, which is worse than
    omitting it.

    When the same ``trigger_id`` exists in both built-ins and L2-authored
    entries, L2's entry wins (overrides the built-in).
    """
    auto_triggers = _detect_auto_triggers(b)
    l2_rule_ids = {r.rule_id for r in b.opt_sp.l1_supplemental_rules}
    active = set(auto_triggers) | l2_rule_ids
    l2_example_triggers = {ex.trigger_id for ex in b.opt_sp.l1_situational_examples}

    blocks: list[str] = []
    for trigger_id in auto_triggers:
        if trigger_id in l2_example_triggers:
            continue  # L2's entry overrides the built-in
        builtin = BUILTIN_EXAMPLES.get(trigger_id)
        if builtin:
            blocks.append(_format_example(trigger_id, builtin))
    for l2_ex in b.opt_sp.l1_situational_examples:
        if l2_ex.trigger_id not in active:
            continue
        blocks.append(_format_example(l2_ex.trigger_id, l2_ex.model_dump()))
    if not blocks:
        return ""
    return "WORKED EXAMPLES:\n" + "\n".join(blocks)


def _format_example(trigger_id: str, ex: dict) -> str:
    """Compact worked-example block — trigger header + ✗/✓/→ lines.

    Parent excerpt is dropped: it almost always duplicates the rule body
    rendered immediately above in SITUATIONAL RULES. The ✗/✓/→ symbols
    replace verbose REJECTED/ACCEPTED/Why labels for small-model legibility.
    """
    lines = [f"  [{trigger_id}]"]
    if rej := (ex.get("rejected") or "").strip():
        lines.append(f"    ✗ {rej}")
    if acc := (ex.get("accepted") or "").strip():
        lines.append(f"    ✓ {acc}")
    if why := (ex.get("why") or "").strip():
        lines.append(f"    → {why}")
    return "\n".join(lines)


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
    "origin_strengths": _Injection(
        "origin_strengths",
        InjectionKind.MEASUREMENT,
        _r_origin_strengths,
        "Round-0 origin's per-sample hits — the floor variants must preserve.",
    ),
    "intractable_samples": _Injection(
        "intractable_samples",
        InjectionKind.MEASUREMENT,
        _r_intractable_samples,
        "Cumulative cycle-wide miss set — samples no candidate has solved yet this cycle.",
    ),
    "archive_top_runs": _Injection(
        "archive_top_runs",
        InjectionKind.MEASUREMENT,
        _r_archive_top_runs,
        "Top-K historical runs across the dataset's archive — anchor the optimizer "
        "against the best composite ever scored instead of re-discovering it.",
    ),
    "rare_hit_samples": _Injection(
        "rare_hit_samples",
        InjectionKind.MEASUREMENT,
        _r_rare_hit_samples,
        "Samples cracked by ≤3 of ≥10 attempts — names the run(s) that hit them "
        "(recipe pointers). Zero-hit samples surface as capacity-bound.",
    ),
    "l1_supplemental_rules": _Injection(
        "l1_supplemental_rules",
        InjectionKind.DIRECTIVE,
        _r_l1_supplemental_rules,
        "Situational rules appended to L1's instruction — auto-triggered from "
        "bundle state (PEAKED axes, runtime failures, chain-bind, continuous-axis, "
        "L2 stall, LaTeX corruption) plus L2-authored entries on opt_sp.",
    ),
    "l1_situational_examples": _Injection(
        "l1_situational_examples",
        InjectionKind.DIRECTIVE,
        _r_l1_situational_examples,
        "Worked examples pinned to currently-active triggers — built-ins shipped "
        "in auto_rules.py plus L2-authored entries on opt_sp. Examples whose "
        "trigger is not active this round are silently filtered.",
    ),
}


__all__ = ["INJECTIONS", "format_l1_critique_for_prompt"]
