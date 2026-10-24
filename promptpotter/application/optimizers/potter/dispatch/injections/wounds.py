from __future__ import annotations

from collections import defaultdict
from typing import Any

from promptpotter.application.optimizers.potter.dispatch.bundle import (
    RUNTIME_FAILURE_RECENCY_WINDOW,
    VALIDATION_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import ParamKind
from promptpotter.domain.search_point import WHO_ANSWERS_KEYS
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import NurseOwner, RuntimeFailure

# Closed vocabularies, safe in an UNFENCED panel; any other LLM-authored value reports its size.
_PLAIN_EVIDENCE_VALUES = NODE_LAYOUTS["l1_generate"].possible | frozenset(
    OptimizerPromptTemplate.RENDER_ORDER
)
# A key of these kinds is a lever L1 turns by picking a value; a free-text key is a prompt edit.
_PLAIN_PARAM_KINDS: frozenset[ParamKind] = frozenset({"model", "enum", "number", "bool"})


def _rf_matches_current_config(
    rf: RuntimeFailure, pipeline_params: dict[str, dict[str, Any]]
) -> bool:
    """A failure observed under a superseded provider/model is stale evidence that mis-steers L2."""
    if not rf.node:
        return True
    current = pipeline_params.get(rf.node)
    if not isinstance(current, dict):
        return False
    observed = rf.observed_config
    return all(observed.get(k) == current.get(k) for k in WHO_ANSWERS_KEYS if k in observed)


def _validation_block(b: InjectionBundle) -> str:
    """One round deep, because a wound heals in the round after it was made."""
    if not b.measured_rounds:
        return ""
    last = b.measured_rounds[-1]
    lines: dict[str, None] = {}
    for vf in (vf for arm in last.arms.values() for vf in arm.validation_failures):
        allowed_str = ", ".join(vf.allowed[:5])
        lines[
            f"  axis={vf.axis} value={vf.value!r} reason={vf.reason}"
            + (f" allowed=[{allowed_str}]" if allowed_str else "")
        ] = None
    if (parse := b.digest.l1_parse_failure) is not None:
        lines[f"  axis=l1_generate.output reason={parse}"] = None
    if not lines:
        return ""
    shown = list(lines)[:VALIDATION_RENDER_CAP]
    sec = ["L1 VALIDATION FAILURES (last round produced invalid variants):", *shown]
    if len(lines) > len(shown):
        sec.append(f"  … {len(lines) - len(shown)} more suppressed.")
    return "\n".join(sec)


def _writable(b: InjectionBundle) -> dict[str, set[str]]:
    schema = b.pipeline_schema
    if schema is None:
        return {}
    by_name = {n.name: n for n in schema.nodes}
    return {
        node: {k for k in keys if by_name[node].param_kind(k) in _PLAIN_PARAM_KINDS}
        for node, keys in schema.node_param_keys().items()
        if node in by_name
    }


def _runtime_block(b: InjectionBundle) -> str:
    """NEW entries skip the config match: they describe the failure being heard right now."""
    runtime_failures = b.runtime_failures
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
    writable = _writable(b)
    sec = ["RUNTIME FAILURES (owner=l1 → fix it; owner=operator → flagged, not in-loop fixable):"]
    if new_rfs:
        sec.append("  NEW:")
        sec.extend(_format_runtime_failure_group(new_rfs, writable))
    if acc_rfs:
        sec.append(f"  ACCUMULATED ({len(acc_rfs)} from earlier rounds):")
        sec.extend(_format_runtime_failure_group(acc_rfs, writable))
    if dropped:
        sec.append(f"  … {dropped} older suppressed (>{RUNTIME_FAILURE_RECENCY_WINDOW} rounds).")
    return "\n".join(sec)


@signal(
    "l1_wounds",
    kind=InjectionKind.MEASUREMENT,
    # Uncapped: truncating mid-list would invite L1 to re-propose a dropped config.
    char_cap=None,
    citable=True,
)
def _r_l1_wounds(b: InjectionBundle) -> list[Item]:
    # One item per block: the composition fences the run, so no selection splits the pair across a tag.
    return [Item(blk, trusted=False) for blk in (_validation_block(b), _runtime_block(b)) if blk]


def _guard_evidence(evidence: dict[str, Any]) -> str:
    """WHICH signals breached: on the id alone L2 cannot see what its edit tripped, and re-sends it."""
    parts: list[str] = []
    for key, value in evidence.items():
        items = list(value) if isinstance(value, list | tuple) else [value]
        if all(
            isinstance(v, bool | int | float)
            or (isinstance(v, str) and v in _PLAIN_EVIDENCE_VALUES)
            for v in items
        ):
            parts.append(f"{key}: {', '.join(str(v) for v in items)}")
        else:
            size = len(items) if isinstance(value, list | tuple) else f"{len(str(value))} chars"
            parts.append(f"{key}: ({size} withheld)")
    return "; ".join(parts)


def _render_guard_breaches(outcomes: list[ValidatorOutcome], layer: str) -> str:
    if not outcomes:
        return ""
    lines = [f"{layer} GUARD BREACHES (post-parse guards on {layer}'s output caught thrashing):"]
    for o in outcomes:
        detail = _guard_evidence(o.evidence)
        lines.append(f"  • {o.validator_id}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


@signal(
    "guard_breaches",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_guard_breaches(b: InjectionBundle) -> list[Item]:
    """Prompt evidence only: no escalation rule reads the stream (``escalation/firing.py``)."""
    blocks = [
        blk
        for blk in (
            _render_guard_breaches(b.l2.guard_breaches, "L2"),
            _render_guard_breaches(b.l3.guard_breaches, "L3"),
        )
        if blk
    ]
    return [Item("\n".join(blocks))]


def _format_runtime_failure_lines(rf: RuntimeFailure, keys: set[str]) -> list[str]:
    rate_pct = round(float(rf.degraded_rate) * 100)
    head = (
        f"    [owner={rf.owner.value}] {rf.dominant_warning} — {rate_pct}% degraded on "
        f"{rf.total_scored}"
    )
    # The provider is the operator's lever, never L1's: shown only where the operator owns the fix.
    shown = keys | {"provider"} if rf.owner is NurseOwner.OPERATOR else keys
    cfg_parts = [f"{k}={v}" for k, v in rf.observed_config.items() if k in shown]
    return [head, f"      cfg: {', '.join(cfg_parts)}"] if cfg_parts else [head]


def _format_runtime_failure_group(
    rfs: list[RuntimeFailure], writable: dict[str, set[str]]
) -> list[str]:
    """N failures on one backend across a varying param axis are ONE discovery, collapsed to one line."""
    clusters: dict[tuple[str | None, str, str, str], list[RuntimeFailure]] = defaultdict(list)
    for rf in rfs:
        cfg = rf.observed_config
        key = (
            rf.node,
            rf.dominant_warning,
            str(cfg.get("provider", "")),
            str(cfg.get("model", "")),
        )
        clusters[key].append(rf)

    out: list[str] = []
    for (node, warning, provider, model), group in clusters.items():
        first = group[0]
        # An abort names no node, so none of its captured config is a key L1 could have chosen.
        keys = writable.get(node, set()) if node is not None else set()
        if len(group) == 1:
            out.extend(_format_runtime_failure_lines(first, keys))
            continue
        on = f", model={model}" if model and "model" in keys else ""
        if provider and first.owner is NurseOwner.OPERATOR:
            on += f", provider={provider}"
        out.append(f"    [owner={first.owner.value}] x{len(group)} — dom={warning}{on}")
        varied: dict[str, set[str]] = defaultdict(set)
        for rf in group:
            for k, v in rf.observed_config.items():
                if k in keys and k != "model":
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
