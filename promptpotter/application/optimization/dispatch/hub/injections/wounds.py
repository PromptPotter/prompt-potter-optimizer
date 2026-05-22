"""Wound-channel injection renderers — the self-healing evidence surfaces.

Each renderer carries one wound channel's evidence into the optimizer
prompts: Wound 1 (L1 parse-time validation failures), Wound 2
(``DegradationCheck`` runtime failures), Wound 4 (L2/L3 post-parse guard
breaches). All share the uniform ``(InjectionBundle) -> str`` renderer
signature and are fenced where they echo untrusted LLM-proposed values
or pipeline warning strings.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from promptpotter.application.optimization.dispatch.hub.bundle import (
    RUNTIME_FAILURE_RECENCY_WINDOW,
    InjectionBundle,
    fence_untrusted,
)
from promptpotter.domain.escalation_signals import RuntimeFailure
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.validators import ValidatorOutcome


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


def _render_guard_breaches(outcomes: list[ValidatorOutcome], layer: str) -> str:
    """Post-parse guard-breach list for one layer.

    "Guard breach" — programmatic guards on a layer's LLM output, distinct
    from ``validation_failures`` / ``runtime_failures`` (pipeline evidence
    from L1 candidate runs). Plain: only ``validator_id`` (controlled
    registry) + ``score`` float, no untrusted content.
    """
    if not outcomes:
        return ""
    lines = [f"{layer} GUARD BREACHES (post-parse guards on {layer}'s output caught thrashing):"]
    lines.extend(f"  • {o.validator_id} (score={o.score:.2f})" for o in outcomes)
    return "\n".join(lines)


def _r_l2_guard_breaches(b: InjectionBundle) -> str:
    """Wound 4 — L2_CONTEXT guard outcomes; non-empty force-triggers an L3 heal."""
    return _render_guard_breaches(b.opt_sp.wounds.l2_guard_breaches, "L2")


def _r_l3_guard_breaches(b: InjectionBundle) -> str:
    """L3_PLAN guard outcomes — L3 reads its own past breaches to avoid repeating them."""
    return _render_guard_breaches(b.opt_sp.wounds.l3_guard_breaches, "L3")


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
