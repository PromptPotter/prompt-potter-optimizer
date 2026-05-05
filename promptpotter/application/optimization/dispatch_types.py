"""Layer dispatch type primitives — shared by surface modules and dispatch.

Leaf module: surface modules (``l1_surface``, ``l2_surface``) import these
types without going back through ``dispatch`` — that's the structural reason
the ``LAYER_CONFIGS`` registry can be a plain module-level dict in
``dispatch_registry`` instead of a lazy global.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult

__all__ = [
    "SECTION_L2_BRIEF",
    "SECTION_PLAN",
    "CritiqueContext",
    "DispatchState",
    "Layer",
    "LayerConfig",
]


class Layer(enum.StrEnum):
    """Optimizer layer that consumes a dispatch_msg."""

    L1_GENERATE = "L1_GENERATE"
    L1_CRITIQUE = "L1_CRITIQUE"
    L2 = "L2"
    L3 = "L3"


@dataclass
class CritiqueContext:
    """L1_CRITIQUE pre-pass — cross-cutting facts computed once per call."""

    prompt_chars: int = 0
    candidate_keys: list[str] | None = None
    nm_queries: set[str] = field(default_factory=set)
    anomalies: list[str] = field(default_factory=list)
    rank_text: str = ""
    evolution_text: str = ""


@dataclass
class DispatchState:
    """Single in-memory state container for one optimizer LLM call.

    Every section renderer reads from this — nothing else. Built once per
    transition by :func:`build_dispatch_state` from the cycle, scoring
    result, and (cached) axis digest; consumed by the layer's section
    table to produce ``{template_var → str}`` for the prompt template.

    Carries explicit slices of cycle state (``opt_sp``, ``l1_stall_count``,
    ``best_accuracy`` / ``best_round``, ``rounds``, ``probe_next_round``)
    rather than a ``Cycle`` reference — renderers no longer transitively
    depend on the orchestration state, so they can be unit-tested with a
    plain dataclass and moved between modules without dragging Cycle.
    """

    opt_sp: OptSearchPoint
    layer: Layer
    round_num: int = 0
    pipeline_schema: PipelineSchema | None = None
    pipeline_params: dict | None = None
    candidate_scores: list[dict] | None = None
    escalation_check_result: dict | None = None
    round_result: RoundResult | None = None
    axis_digest: dict[str, str] | None = None
    critique: CritiqueContext | None = None
    # Cycle slices renderers reach into.
    l1_stall_count: int = 0
    best_accuracy: float = 0.0
    best_round: int = -1
    rounds: list[Any] = field(default_factory=list)
    probe_next_round: bool = False


@dataclass(frozen=True)
class LayerConfig:
    """How a layer fills its prompt template.

    ``sections`` maps each ``{{template_var}}`` to a renderer reading a
    :class:`DispatchState`. ``read_overrides``, when set, extracts
    ``(visibility, text)`` from the OSP so L2 can gate or replace
    individual sections (currently only L1-generate uses this).
    """

    sections: dict[str, Callable[[DispatchState], str]]
    read_overrides: Callable[[OptSearchPoint], tuple[dict[str, bool], dict[str, str]]] | None = None


# ---------------------------------------------------------------------------
# Shared section renderers — `_section_l2_brief` and `_section_plan` are
# rendered by BOTH L1_GENERATE and L2, so they live with the type primitives
# instead of in either surface module.
# ---------------------------------------------------------------------------


def _section_l2_brief(ctx: DispatchState) -> str:
    v = ctx.opt_sp.l2_brief
    if not v:
        return ""
    label = "BRIEF:" if ctx.layer is Layer.L1_GENERATE else "PREVIOUS BRIEF:"
    return f"{label}\n{v}"


def _section_plan(ctx: DispatchState) -> str:
    v = ctx.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


SECTION_L2_BRIEF = _section_l2_brief
SECTION_PLAN = _section_plan
