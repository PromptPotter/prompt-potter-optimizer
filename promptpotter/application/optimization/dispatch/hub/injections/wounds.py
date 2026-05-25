"""Wound-channel renderers — self-healing evidence into optimizer prompts.

Wound 1 (L1 parse-time validation), Wound 2 (DegradationCheck runtime), Wound 4 (L2/L3
post-parse guard breaches). Uniform `(InjectionBundle) -> str`. Fenced where untrusted content
(LLM-proposed values, pipeline warnings) is echoed.
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


def _rf_matches_current_config(
    rf: RuntimeFailure, pipeline_params: dict[str, dict[str, Any]]
) -> bool:
    """Filter ACCUMULATED failures by current backend config — a failure observed under a
    superseded overlay (e.g. yesterday's provider/model) becomes stale evidence that mis-steers
    L2's framing. Compares on PARAM_FORBIDDEN_KEYS (provider, model).
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
    """Wound 1 — L1 parse-time validator. Fenced (echoes LLM-proposed values)."""
    failures = b.opt_sp.memory.wounds.validation_failures
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
    """Wound 2 — DegradationCheck mid-eval evidence. Fenced (echoes pipeline warnings).
    ACCUMULATED entries filter through `_rf_matches_current_config`; NEW (first-seen this round)
    always pass — they describe the failure being heard right now.
    """
    runtime_failures = b.opt_sp.memory.wounds.runtime_failures
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
    """Post-parse guard-breach list — programmatic guards on a layer's LLM output, distinct from
    `validation_failures` / `runtime_failures` (pipeline evidence). No untrusted content.
    """
    if not outcomes:
        return ""
    lines = [f"{layer} GUARD BREACHES (post-parse guards on {layer}'s output caught thrashing):"]
    lines.extend(f"  • {o.validator_id} (score={o.score:.2f})" for o in outcomes)
    return "\n".join(lines)


def _r_l2_guard_breaches(b: InjectionBundle) -> str:
    """Wound 4 — L2_CONTEXT guard outcomes; non-empty force-triggers an L3 heal."""
    return _render_guard_breaches(b.opt_sp.memory.wounds.l2_guard_breaches, "L2")


def _r_l3_guard_breaches(b: InjectionBundle) -> str:
    """L3_PLAN guard outcomes — L3 reads its own past breaches to avoid repeating them."""
    return _render_guard_breaches(b.opt_sp.memory.wounds.l3_guard_breaches, "L3")


def _format_runtime_failure_lines(rf: Any) -> list[str]:
    """Two-line render of one RuntimeFailure — for single-entry groups (multi-entry compresses)."""
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
    """Cluster by (warning, provider, model). N failures with same backend on varying param-axis
    is ONE discovery, not N — collapse to a single line with the varying params enumerated.
    Single-entry groups passthrough.
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
