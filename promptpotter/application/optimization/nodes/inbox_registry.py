"""Inbox registry — single declarative catalogue of optimizer-prompt inputs.

Every L1/L2 prompt receives an ``{{inbox}}`` block assembled from the
fields declared here. Each :class:`InboxField` owns:

    * which layer(s) consume it,
    * how to source its raw value from a :class:`Cycle` (+ per-call
      :class:`InboxTransients`),
    * how to render that value into a section string,
    * the section label/header shown in each consuming layer,
    * optional mutex membership for mutually-exclusive pairs (e.g.
      ``l2_directive`` wins over ``l1_critique_text`` on L1 only).

:func:`assemble_inbox` is the one function callers need. It walks
:data:`LAYER_ORDER` for the target layer, applies mutex resolution,
drops empty sections, and joins the rest.

:class:`Cycle` (from ``application/optimization/cycle.py``) is the read
view — it holds ``opt_sp`` (per-cycle mutable state + ``memory``),
``search_memory`` (cross-cycle aggregate), and ``session`` (infra,
including ``pipeline_schema``). :class:`InboxTransients` carries the
per-call inputs that have no persistent home
(``pipeline_schema_text``, ``candidate_scores``,
``escalation_check_result``, ``pipeline_params``, ``round_num``).

The critique layer keeps its own assembler (see ``critique.py``) because
its sections share cross-cutting state (``anomalies``, ``near_miss``).
L3 keeps its multi-hole template (``current_plan``, ``l2_summary``,
``rendered_prompt``, ``pipeline_section``) and only the intelligence
block flows through this registry.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes._inbox_helpers import (
    _r_escalation_alert,
    _r_escalation_probe,
    _r_escalation_section,
    _r_failure_analysis,
    _r_identity,
    _r_labeled,
    _r_plan,
    _r_runtime_failures_l2,
    _r_search_memory_l1,
    _r_search_memory_l2,
    _r_task_context,
    _r_thinking_styles,
    _r_validation_failures,
    _r_warning_inventory_l2,
    _src_escalation_alert,
    _src_escalation_probe,
    _src_escalation_section,
    _src_failure_analysis,
    _src_l1_search_memory,
    _src_l2_search_memory,
    _src_memory,
    _src_pipeline_schema_text,
    _src_plan,
    _src_runtime_failures,
    _src_task_context,
    _src_validation_failures,
    _src_warning_inventory_l2,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle


class Layer(enum.StrEnum):
    """Optimizer layer that consumes an inbox."""

    L1 = "L1"
    L2 = "L2"


class Retention(enum.StrEnum):
    """How a field's source value survives across rounds (docs-only)."""

    MEMORY = "memory"  # opt_sp.memory.* — persisted on OptSearchPoint
    OPT_SP = "opt_sp"  # opt_sp.<top-level> — persisted on OptSearchPoint
    TRANSIENT = "transient"  # computed per-round, not stored
    CONFIG = "config"  # pipeline_schema / precomputed static text
    SEARCH_MEMORY = "search_memory"  # cross-campaign aggregate


@dataclass(frozen=True)
class InboxTransients:
    """Per-assembly inputs that have no persistent home on :class:`Cycle`.

    Built once by :func:`assemble_inbox` from its kwargs and threaded into
    every source / render closure alongside the cycle.
    """

    round_num: int = 0
    pipeline_schema_text: str = ""
    candidate_scores: list[dict] | None = None
    escalation_check_result: dict | None = None
    pipeline_params: dict | None = None


@dataclass(frozen=True)
class InboxField:
    """Declarative spec for one piece of optimizer-prompt intelligence."""

    name: str
    layers: frozenset[Layer]
    source: Callable[[Cycle, InboxTransients], Any]
    render: Callable[[Any, Cycle, InboxTransients, Layer], str]
    retention: Retention
    docstring: str
    # Per-layer mutex: fields sharing a (layer, group) pick the highest priority.
    mutex: dict[Layer, tuple[str, int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry — one authoritative catalogue.
# ---------------------------------------------------------------------------


INBOX: tuple[InboxField, ...] = (
    # L1-only fields (section order preserved from legacy format_context_sections)
    InboxField(
        name="pipeline_schema_text",
        layers=frozenset({Layer.L1}),
        source=_src_pipeline_schema_text,
        render=_r_identity,
        retention=Retention.CONFIG,
        docstring="Precomputed pipeline node/param catalogue — teaches L1 what it may tune.",
    ),
    InboxField(
        name="failure_analysis",
        layers=frozenset({Layer.L1}),
        source=_src_failure_analysis,
        render=_r_failure_analysis,
        retention=Retention.TRANSIENT,
        docstring="Top-3 clustered failure patterns with example queries.",
    ),
    InboxField(
        name="search_memory_l1",
        layers=frozenset({Layer.L1}),
        source=_src_l1_search_memory,
        render=_r_search_memory_l1,
        retention=Retention.SEARCH_MEMORY,
        docstring="Cross-campaign digest: failure clusters, dead queries, top axes / values.",
    ),
    InboxField(
        name="task_context",
        layers=frozenset({Layer.L1}),
        source=_src_task_context,
        render=_r_task_context,
        retention=Retention.OPT_SP,
        docstring="Structured domain context (read-only from L1's view; L2 edits).",
    ),
    InboxField(
        name="escalation_probe",
        layers=frozenset({Layer.L1}),
        source=_src_escalation_probe,
        render=_r_escalation_probe,
        retention=Retention.MEMORY,
        docstring="Probe-round per-query warning dump — fires only when L2 requests a probe.",
    ),
    InboxField(
        name="escalation_alert",
        layers=frozenset({Layer.L1}),
        source=_src_escalation_alert,
        render=_r_escalation_alert,
        retention=Retention.MEMORY,
        docstring="Non-probe aggregated escalation alert — suppressed by an active l2_directive.",
    ),
    # Mutex pair on L1: directive wins over l1_critique when both populated.
    InboxField(
        name="l2_directive",
        layers=frozenset({Layer.L1, Layer.L2}),
        source=_src_memory("l2_directive"),
        render=_r_labeled(""),  # placeholder; overridden per-layer below
        retention=Retention.MEMORY,
        docstring="L2's one-round guidance window; clears on improvement.",
        mutex={Layer.L1: ("guidance", 2)},
    ),
    InboxField(
        name="l1_critique_text",
        layers=frozenset({Layer.L1, Layer.L2}),
        source=_src_memory("l1_critique_text"),
        render=_r_labeled(""),  # placeholder; overridden per-layer below
        retention=Retention.MEMORY,
        docstring="Latest L1 critique output; L2 digests into a directive before L1 sees it.",
        mutex={Layer.L1: ("guidance", 1)},
    ),
    InboxField(
        name="thinking_styles",
        layers=frozenset({Layer.L1}),
        source=_src_memory("thinking_styles"),
        render=_r_thinking_styles,
        retention=Retention.MEMORY,
        docstring="3 sampled thinking styles for L1 meta-prompt injection.",
    ),
    InboxField(
        name="plan",
        layers=frozenset({Layer.L1}),
        source=_src_plan,
        render=_r_plan,
        retention=Retention.OPT_SP,
        docstring="L3's strategic plan (read-only from L1's view).",
    ),
    # L2-only fields (section order preserved from legacy format_l2_intelligence)
    InboxField(
        name="escalation_section",
        layers=frozenset({Layer.L2}),
        source=_src_escalation_section,
        render=_r_escalation_section,
        retention=Retention.TRANSIENT,
        docstring="Aggregated pipeline stability report — composed from escalation_check_result.",
    ),
    InboxField(
        name="warning_inventory",
        layers=frozenset({Layer.L2}),
        source=_src_warning_inventory_l2,
        render=_r_warning_inventory_l2,
        retention=Retention.MEMORY,
        docstring="Per-query warning breakdown — L2 fallback when no escalation section.",
    ),
    InboxField(
        name="validation_failures",
        layers=frozenset({Layer.L2}),
        source=_src_validation_failures,
        render=_r_validation_failures,
        retention=Retention.TRANSIENT,
        docstring="L1 parse-time invariant violations — Rail 1 self-healing input.",
    ),
    InboxField(
        name="runtime_failures",
        layers=frozenset({Layer.L2}),
        source=_src_runtime_failures,
        render=_r_runtime_failures_l2,
        retention=Retention.MEMORY,
        docstring="Mid-eval degradation records — Rail 2 self-healing input for L2.",
    ),
    InboxField(
        name="search_memory_l2",
        layers=frozenset({Layer.L2}),
        source=_src_l2_search_memory,
        render=_r_search_memory_l2,
        retention=Retention.SEARCH_MEMORY,
        docstring="Cross-campaign strategic digest: axis rankings, bottlenecks, correlations.",
    ),
)


# ---------------------------------------------------------------------------
# Per-layer order and per-layer label overrides for mutex-winning fields.
# ---------------------------------------------------------------------------


LAYER_ORDER: dict[Layer, tuple[str, ...]] = {
    Layer.L1: (
        "pipeline_schema_text",
        "failure_analysis",
        "search_memory_l1",
        "task_context",
        "escalation_probe",
        "escalation_alert",
        "l2_directive",
        "l1_critique_text",
        "thinking_styles",
        "plan",
    ),
    Layer.L2: (
        "escalation_section",
        "warning_inventory",
        "l1_critique_text",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "search_memory_l2",
    ),
}


_LABEL_BY_LAYER: dict[tuple[Layer, str], str] = {
    (Layer.L1, "l2_directive"): "DIRECTIVE:",
    (Layer.L1, "l1_critique_text"): "CRITIQUE:",
    (Layer.L2, "l1_critique_text"): "CRITIQUE:",
    (Layer.L2, "l2_directive"): "PREVIOUS DIRECTIVE:",
}


def _by_name() -> dict[str, InboxField]:
    return {f.name: f for f in INBOX}


def _render_one(f: InboxField, raw: Any, cycle: Cycle, t: InboxTransients, layer: Layer) -> str:
    """Dispatch rendering — for labeled fields, use the per-layer label override."""
    label = _LABEL_BY_LAYER.get((layer, f.name))
    if label is not None:
        # Labeled scalar text — same shape as _r_labeled.
        return f"{label}\n{raw}" if raw else ""
    return f.render(raw, cycle, t, layer)


def _resolve_mutex(layer: Layer, raws: dict[str, Any]) -> dict[str, Any]:
    """Keep only the highest-priority populated field per (layer, mutex_group)."""
    by_name = _by_name()
    groups: dict[str, list[tuple[int, str]]] = {}
    for fname, raw in raws.items():
        if not raw:
            continue
        mutex = by_name[fname].mutex.get(layer)
        if mutex is None:
            continue
        group, pri = mutex
        groups.setdefault(group, []).append((pri, fname))

    drop: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(reverse=True)  # highest priority first
        for _pri, fname in members[1:]:
            drop.add(fname)

    return {k: v for k, v in raws.items() if k not in drop}


def assemble_inbox(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
) -> str:
    """Walk the registry for *layer*, resolve mutex, render, join.

    Reads persistent state from *cycle* (``cycle.opt_sp``,
    ``cycle.search_memory``, ``cycle.probe_next_round``,
    ``cycle.session.pipeline_schema``). Transient per-call inputs are passed
    as kwargs and bundled into :class:`InboxTransients` internally.

    Returns an empty string when no section produces content.
    """
    t = InboxTransients(
        round_num=round_num,
        pipeline_schema_text=pipeline_schema_text,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        pipeline_params=pipeline_params,
    )
    by_name = _by_name()
    order = LAYER_ORDER[layer]

    # Source every field in order; drop those whose source returned empty.
    raws: dict[str, Any] = {}
    for fname in order:
        f = by_name[fname]
        if layer not in f.layers:
            continue
        raw = f.source(cycle, t)
        if raw:
            raws[fname] = raw

    raws = _resolve_mutex(layer, raws)

    # Render in layer order, drop empty section strings.
    sections: list[str] = []
    for fname in order:
        if fname not in raws:
            continue
        f = by_name[fname]
        text = _render_one(f, raws[fname], cycle, t, layer)
        if text:
            sections.append(text)

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Docs helper — emits the per-layer rows for information-flow.md.
# ---------------------------------------------------------------------------


def registry_rows_for_docs() -> list[dict[str, str]]:
    """One row per (field, layer) — feeds the table in information-flow.md."""
    rows: list[dict[str, str]] = []
    for fname in LAYER_ORDER[Layer.L1] + LAYER_ORDER[Layer.L2]:
        f = _by_name()[fname]
        for layer in (Layer.L1, Layer.L2):
            if fname not in LAYER_ORDER[layer]:
                continue
            label = _LABEL_BY_LAYER.get((layer, fname), "")
            mutex = f.mutex.get(layer)
            rows.append(
                {
                    "field": fname,
                    "layer": str(layer),
                    "label": label,
                    "retention": str(f.retention),
                    "mutex": (f"{mutex[0]} (pri {mutex[1]})" if mutex else ""),
                    "docstring": f.docstring,
                }
            )
    return rows


__all__ = [
    "INBOX",
    "LAYER_ORDER",
    "InboxField",
    "InboxTransients",
    "Layer",
    "Retention",
    "assemble_inbox",
    "registry_rows_for_docs",
]
