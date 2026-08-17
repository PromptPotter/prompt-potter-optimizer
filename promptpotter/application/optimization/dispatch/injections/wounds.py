"""Wound-channel renderers — self-healing evidence into optimizer prompts, uniform ``(InjectionBundle) -> str``.
Fenced wherever untrusted content (LLM-proposed values, pipeline warnings) is echoed."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from promptpotter.application.optimization.dispatch.bundle import (
    RUNTIME_FAILURE_RECENCY_WINDOW,
    VALIDATION_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    fence_untrusted,
    signal,
)
from promptpotter.domain.escalation_signals import RuntimeFailure
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.validators import ValidatorOutcome


def _rf_matches_current_config(
    rf: RuntimeFailure, pipeline_params: dict[str, dict[str, Any]]
) -> bool:
    """Filter ACCUMULATED failures by current backend config: one observed under a superseded provider/model is stale
    evidence that mis-steers L2's framing."""
    node = rf.dominant_warning.split(":", 1)[0]
    if not node:
        return True
    current = pipeline_params.get(node)
    if not isinstance(current, dict):
        return False
    observed = rf.observed_config
    return all(observed.get(k) == current.get(k) for k in PARAM_FORBIDDEN_KEYS if k in observed)


def _validation_block(b: InjectionBundle) -> str:
    """Parse-time validation failures (all owner=L1 — L1's own invalid variants). Bounded to the
    most RECENT ``VALIDATION_RENDER_CAP``, which is where the fixable ones are: the list
    accumulates on the searchpoint, so an unbounded render grew with the cycle until the cap
    downstream cut it — and cut the newest, the round L1 is being asked to heal."""
    failures = b.opt_sp.memory.wounds.validation_failures
    if not failures:
        return ""
    shown = failures[-VALIDATION_RENDER_CAP:]
    sec = ["L1 VALIDATION FAILURES (last round produced invalid variants):"]
    for vf in shown:
        allowed_str = ", ".join(vf.allowed[:5])
        sec.append(
            f"  axis={vf.axis} value={vf.value!r} reason={vf.reason}"
            + (f" allowed=[{allowed_str}]" if allowed_str else "")
        )
    if len(failures) > len(shown):
        sec.append(f"  … {len(failures) - len(shown)} older failures suppressed.")
    return "\n".join(sec)


def _runtime_block(b: InjectionBundle) -> str:
    """Mid-eval runtime failures, owner-tagged. ACCUMULATED entries filter through the config match; NEW ones always pass —
    they describe the failure being heard right now."""
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
    sec = ["RUNTIME FAILURES (owner=l1 → fix it; owner=operator → flagged, not in-loop fixable):"]
    if new_rfs:
        sec.append("  NEW:")
        sec.extend(_format_runtime_failure_group(new_rfs))
    if acc_rfs:
        sec.append(f"  ACCUMULATED ({len(acc_rfs)} from earlier rounds):")
        sec.extend(_format_runtime_failure_group(acc_rfs))
    if dropped:
        sec.append(f"  … {dropped} older suppressed (>{RUNTIME_FAILURE_RECENCY_WINDOW} rounds).")
    return "\n".join(sec)


@signal(
    "l1_wounds",
    kind=InjectionKind.MEASUREMENT,
    # Fenced (echoes LLM-proposed values + pipeline warnings). Cap fits one
    # RUNTIME_FAILURE_RECENCY_WINDOW of runtime + the validation list; runtime is
    # already window-bounded ("… N older suppressed"). Truncating runtime mid-list
    # would invite L1 to re-propose a dropped config (the validator still blocks it).
    char_cap=2500,
    citable=True,
)
def _r_l1_wounds(b: InjectionBundle) -> str:
    # Fenced PER BLOCK. One fence around the joined pair left its separator inside the fence,
    # so `_truncate_to_cap` split there and dropped the closing tag — the failure the fence
    # note in `bundle.py` describes.
    return "\n\n".join(
        fence_untrusted(blk) for blk in (_validation_block(b), _runtime_block(b)) if blk
    )


def _render_guard_breaches(outcomes: list[ValidatorOutcome], layer: str) -> str:
    """Post-parse guard-breach list — programmatic guards on a layer's LLM output, distinct from
    `validation_failures` / `runtime_failures` (pipeline evidence). No untrusted content.
    """
    if not outcomes:
        return ""
    lines = [f"{layer} GUARD BREACHES (post-parse guards on {layer}'s output caught thrashing):"]
    lines.extend(f"  • {o.validator_id}" for o in outcomes)
    return "\n".join(lines)


@signal(
    "guard_breaches",
    kind=InjectionKind.MEASUREMENT,
    char_cap=400,
    citable=True,
)
def _r_guard_breaches(b: InjectionBundle) -> str:
    """L2 + L3 post-parse guard outcomes in one block; both route to L3, which reads its own past breaches to avoid
    repeating them. ``escalate_l2`` force-triggers off the stream directly, not this render."""
    wounds = b.opt_sp.memory.wounds
    blocks = [
        blk
        for blk in (
            _render_guard_breaches(wounds.l2_guard_breaches, "L2"),
            _render_guard_breaches(wounds.l3_guard_breaches, "L3"),
        )
        if blk
    ]
    return "\n".join(blocks)


def _format_runtime_failure_lines(rf: RuntimeFailure) -> list[str]:
    rate_pct = round(float(rf.degraded_rate) * 100)
    cfg_parts = [f"{k}={v}" for k, v in rf.observed_config.items() if k != "prompt"]
    cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
    label = rf.candidate_label[:60]
    owner = rf.owner.value
    head = (
        f"    [owner={owner}] {label} — {rate_pct}% degraded on {rf.total_scored}, dom={rf.dominant_warning}"
        if label
        else f"    [owner={owner}] {rf.dominant_warning} — {rate_pct}% degraded on {rf.total_scored}"
    )
    return [head, f"      cfg: {cfg_str}"]


def _format_runtime_failure_group(rfs: list[RuntimeFailure]) -> list[str]:
    """Cluster by (warning, provider, model). N failures with the same backend on a varying param axis are ONE discovery,
    not N — collapsed to a single line with the varying params enumerated."""
    clusters: dict[tuple[str, str, str], list[RuntimeFailure]] = defaultdict(list)
    for rf in rfs:
        cfg = rf.observed_config
        key = (
            rf.dominant_warning,
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
        owner = group[0].owner.value
        out.append(f"    [owner={owner}] x{len(group)} — dom={warning}, model={backend}")
        varied: dict[str, set[str]] = defaultdict(set)
        for rf in group:
            for k, v in rf.observed_config.items():
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
