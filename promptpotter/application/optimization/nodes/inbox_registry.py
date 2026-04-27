"""Inbox registry — single declarative catalogue of optimizer-prompt inputs.

Every L1/L2 prompt receives an ``{{inbox}}`` block assembled from the
fields declared here. Each :class:`InboxField` owns: which layer(s) consume
it, how to source its raw value from a :class:`Cycle` (+ per-call
:class:`InboxTransients`), how to render that value into a section string,
and the header prefix(es) that section starts with.

:func:`assemble_inbox` is the one function callers need. It walks
:data:`LAYER_ORDER` for the target layer, sources values, renders them,
and joins. On L1 the ``l2_directive`` replaces ``l1_critique_text`` when
both are populated (L2's digested view supersedes the raw critique).

The critique layer keeps its own assembler (see ``critique.py``) because
its sections share cross-cutting state (``anomalies``, ``near_miss``).
L3 keeps its multi-hole template (``current_plan``, ``l2_summary``,
``rendered_prompt``, ``pipeline_section``) and only the intelligence
block flows through this registry.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes._inbox_helpers import (
    _r_escalation_alert,
    _r_escalation_probe,
    _r_escalation_section,
    _r_failure_analysis,
    _r_identity,
    _r_l1_critique,
    _r_l2_directive,
    _r_plan,
    _r_runtime_failures_l2,
    _r_search_memory_l1,
    _r_search_memory_l2,
    _r_task_context,
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

    MEMORY = "memory"  # flattened onto opt_sp — see OptSearchPoint.MEMORY_FIELDS
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
    # Section header prefix(es) the renderer emits; observers (e.g. meta-prompt
    # size logging) match against these to attribute bytes back to the field.
    header_prefixes: tuple[str, ...] = ()


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
        header_prefixes=(
            "VALID PIPELINE NODES AND PARAMETERS",
            "AVAILABLE MODELS",
            "OUTPUT SCHEMA MUTATIONS",
        ),
    ),
    InboxField(
        name="failure_analysis",
        layers=frozenset({Layer.L1}),
        source=_src_failure_analysis,
        render=_r_failure_analysis,
        retention=Retention.TRANSIENT,
        docstring="Top-3 clustered failure patterns with example queries.",
        header_prefixes=("FAILURE ANALYSIS",),
    ),
    InboxField(
        name="search_memory_l1",
        layers=frozenset({Layer.L1}),
        source=_src_l1_search_memory,
        render=_r_search_memory_l1,
        retention=Retention.SEARCH_MEMORY,
        docstring="Cross-campaign digest: failure clusters, dead queries, top axes / values.",
        header_prefixes=("HISTORICAL INTELLIGENCE:",),
    ),
    InboxField(
        name="task_context",
        layers=frozenset({Layer.L1}),
        source=_src_task_context,
        render=_r_task_context,
        retention=Retention.OPT_SP,
        docstring="Structured domain context (read-only from L1's view; L2 edits).",
        header_prefixes=("CONTEXT:",),
    ),
    InboxField(
        name="escalation_probe",
        layers=frozenset({Layer.L1}),
        source=_src_escalation_probe,
        render=_r_escalation_probe,
        retention=Retention.MEMORY,
        docstring="Probe-round per-query warning dump — fires only when L2 requests a probe.",
        header_prefixes=("PROBE ROUND",),
    ),
    InboxField(
        name="escalation_alert",
        layers=frozenset({Layer.L1}),
        source=_src_escalation_alert,
        render=_r_escalation_alert,
        retention=Retention.MEMORY,
        docstring="Non-probe aggregated escalation alert — suppressed by an active l2_directive.",
        header_prefixes=("PIPELINE ISSUE:",),
    ),
    # On L1 the directive replaces the critique when both are present (sliding window of 1).
    InboxField(
        name="l2_directive",
        layers=frozenset({Layer.L1, Layer.L2}),
        source=_src_memory("l2_directive"),
        render=_r_l2_directive,
        retention=Retention.MEMORY,
        docstring="L2's one-round guidance window; clears on improvement.",
        header_prefixes=("DIRECTIVE:", "PREVIOUS DIRECTIVE:"),
    ),
    InboxField(
        name="l1_critique_text",
        layers=frozenset({Layer.L1, Layer.L2}),
        source=_src_memory("l1_critique_text"),
        render=_r_l1_critique,
        retention=Retention.MEMORY,
        docstring="Latest L1 critique output; L2 digests into a directive before L1 sees it.",
        header_prefixes=("CRITIQUE:",),
    ),
    InboxField(
        name="plan",
        layers=frozenset({Layer.L1}),
        source=_src_plan,
        render=_r_plan,
        retention=Retention.OPT_SP,
        docstring="L3's strategic plan (read-only from L1's view).",
        header_prefixes=("PLAN:",),
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


def header_prefixes_for_layer(layer: Layer) -> tuple[str, ...]:
    """Section-header prefixes a layer's rendered inbox can emit (observer aid)."""
    out: list[str] = []
    for f in INBOX:
        if layer in f.layers:
            out.extend(f.header_prefixes)
    return tuple(out)


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


def _by_name() -> dict[str, InboxField]:
    return {f.name: f for f in INBOX}


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
    """Walk the registry for *layer*, source, render, join.

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

    # On L1 the L2 directive replaces the critique whenever both are present
    # — the directive is L2's digested view of the critique (sliding window of 1).
    if layer is Layer.L1 and "l2_directive" in raws:
        raws.pop("l1_critique_text", None)

    sections = [
        text
        for fname in order
        if fname in raws
        for text in (by_name[fname].render(raws[fname], cycle, t, layer),)
        if text
    ]
    return "\n\n".join(sections)


__all__ = [
    "INBOX",
    "LAYER_ORDER",
    "InboxField",
    "InboxTransients",
    "Layer",
    "Retention",
    "assemble_inbox",
    "header_prefixes_for_layer",
]
