"""Layer dispatch — per-call payload + layer-fan-in registry.

Every optimizer LLM call follows the same flow:
``archive → AxisIndex (cached) → DispatchState (per-call state) →
LAYER_CONFIGS[layer] (per-layer {var: section_renderer} table) →
compile_prompt_vars (applies OSP overrides, merges per-call extras) → LLM``.

L1-generate is fan-in: its config wires ``plan`` (from L3, persistent on
OSP) and ``l2_directive`` (from L2, one-round window) alongside its other
sections, and L2 owns the section visibility / text / whole-template
overrides via ``OptSearchPoint`` fields. L2 / L3 / L1-critique have no
override channel — their non-section vars ride in as ``extras``.

The ``LAYER_CONFIGS`` dict is built lazily via ``get_layer_configs()`` so
section-renderer modules (``l1_surface``, ``l2_surface``) can import
``Layer`` / ``DispatchState`` / ``LayerConfig`` from this module without a
circular dep.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.optimization.elimination import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.scoring.metrics import find_rank
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.l1 import L1ScoringResult
    from promptpotter.domain.scoring import QueryResult

__all__ = [
    "CritiqueContext",
    "DispatchState",
    "Layer",
    "LayerConfig",
    "build_dispatch_state",
    "compile_critique_context",
    "compile_prompt_vars",
    "get_layer_configs",
]


class Layer(enum.StrEnum):
    """Optimizer layer that consumes a dispatch_msg."""

    L1_GENERATE = "L1_GENERATE"
    L1_CRITIQUE = "L1_CRITIQUE"
    L2 = "L2"
    L3 = "L3"


# Module-level constants shared across dispatch + section renderers.
_PROMPT_BLOAT_CHARS = 3000


@dataclass
class CritiqueContext:
    """L1_CRITIQUE pre-pass — cross-cutting facts computed once per call."""

    prompt_chars: int = 0
    candidate_keys: list[str] | None = None
    nm_queries: set[str] = field(default_factory=set)
    anomalies: list[str] = field(default_factory=list)
    rank_text: str = ""
    evolution_text: str = ""


def compile_critique_context(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
) -> CritiqueContext:
    candidate_keys = candidate_keys_from_schema(schema)
    prompt_chars = len(cycle.opt_sp.render())
    rank_text, nm_queries = _compute_rank_analysis(scoring_result.winner_results, candidate_keys)
    evolution_text, anomalies = _compute_round_evolution(cycle)
    return CritiqueContext(
        prompt_chars=prompt_chars,
        candidate_keys=candidate_keys,
        nm_queries=nm_queries,
        anomalies=anomalies,
        rank_text=rank_text,
        evolution_text=evolution_text,
    )


def _compute_rank_analysis(
    results: list[QueryResult], candidate_keys: list[str] | None
) -> tuple[str, set[str]]:
    """Return (section_text, near_miss_query_set)."""
    keys = candidate_keys or None
    rank_map: dict[int, int | None] = {
        i: find_rank(get_candidates(r, keys), r.get("ground_truth", ""))
        for i, r in enumerate(results)
        if not is_error_result(r)
    }
    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    for i, r in enumerate(results):
        if is_error_result(r):
            continue
        rank = rank_map.get(i)
        if rank == 1:
            rank_buckets["1"] += 1
        elif rank is not None and rank <= 10:
            rank_buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                {
                    "query": r["query"][:80],
                    "ground_truth": r.get("ground_truth", "")[:60],
                    "rank": rank,
                    "predicted": r.get("predicted", "?")[:60],
                }
            )
        elif rank is not None and rank <= 20:
            rank_buckets["11-20"] += 1
        else:
            rank_buckets["not_found"] += 1
    nm_queries = {nm["query"] for nm in near_misses}

    n_valid = sum(1 for r in results if not is_error_result(r))
    if not n_valid:
        return "", nm_queries
    lines = [
        "## CANDIDATE RANK ANALYSIS",
        "Where does ground truth appear in candidate list?",
    ]
    for bucket, count in rank_buckets.items():
        lines.append(f"  Rank {bucket}: {count}")
    for k in (1, 3, 5, 10):
        in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
        lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")
    if near_misses:
        lines.append(f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):")
        for nm in near_misses[:15]:
            lines.append(
                f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                f"(GT: {nm['ground_truth']})"
            )
    return "\n".join(lines), nm_queries


def _compute_round_evolution(cycle: Cycle) -> tuple[str, list[str]]:
    """Return (section_text, anomalies). Plateau detection emits a [MEDIUM] flag."""
    anomalies: list[str] = []
    rounds = cycle.rounds
    if not rounds:
        return "", anomalies
    lines = [
        "## ROUND EVOLUTION",
        "Round  Accuracy  Delta   Degraded  Candidates",
    ]
    prev_acc: float | None = None
    plateau_count = 0
    for r in rounds:
        acc = r.accuracy
        delta = acc - prev_acc if prev_acc is not None else 0.0
        lines.append(
            f"  {r.round:>5}  {acc:>7.1%}  {delta:>+6.1%}  "
            f"{getattr(r, 'degraded_queries', 0):>8}  {len(r.candidate_scores):>10}"
        )
        plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
        prev_acc = acc
    for i in range(1, len(rounds)):
        prev_pp = rounds[i - 1].pipeline_params or {}
        curr_pp = rounds[i].pipeline_params or {}
        changed = {
            k
            for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed:
            lines.append(
                f"  Round {rounds[i - 1].round}→{rounds[i].round}: {', '.join(sorted(changed))}"
            )
    if plateau_count >= 2:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds with <1% improvement."
        )
    return "\n".join(lines), anomalies


@dataclass
class DispatchState:
    """Single in-memory state container for one optimizer LLM call.

    Every section renderer reads from this — nothing else. Built once per
    transition by :func:`build_dispatch_state` from the cycle, scoring
    result, and (cached) axis digest; consumed by the layer's section
    table to produce ``{template_var → str}`` for the prompt template.
    """

    cycle: Cycle
    layer: Layer
    round_num: int = 0
    pipeline_schema: PipelineSchema | None = None
    pipeline_params: dict | None = None
    candidate_scores: list[dict] | None = None
    escalation_check_result: dict | None = None
    scoring_result: L1ScoringResult | None = None
    axis_digest: dict[str, str] | None = None
    critique: CritiqueContext | None = None


def _layer_axis_digest(layer: Layer, cycle: Cycle) -> dict[str, str] | None:
    """Pre-fetch the layer-appropriate axis digest from ``cycle.axes``."""
    if cycle.axes is None:
        return None
    if layer is Layer.L1_GENERATE:
        return cycle.axes.digest_for_l1_generate()
    if layer is Layer.L1_CRITIQUE:
        return cycle.axes.digest_for_l1_critique()
    if layer is Layer.L2:
        return cycle.axes.digest_for_l2()
    if layer is Layer.L3:
        return cycle.axes.digest_for_l3()
    return None


def build_dispatch_state(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema: PipelineSchema | None = None,
    pipeline_params: dict | None = None,
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
) -> DispatchState:
    """Build the per-call :class:`DispatchState` for *layer* over *cycle*."""
    critique: CritiqueContext | None = None
    if layer is Layer.L1_CRITIQUE and scoring_result is not None:
        critique = compile_critique_context(cycle, scoring_result, pipeline_schema)
    return DispatchState(
        cycle=cycle,
        layer=layer,
        round_num=round_num,
        pipeline_schema=pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        scoring_result=scoring_result,
        axis_digest=_layer_axis_digest(layer, cycle),
        critique=critique,
    )


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


_LAYER_CONFIGS_CACHE: dict[Layer, LayerConfig] | None = None


def get_layer_configs() -> dict[Layer, LayerConfig]:
    """Return the layer-fan-in registry; built lazily on first call.

    Built lazily so ``l1_surface`` / ``l2_surface`` (which contain the
    section renderers) can ``from .dispatch import Layer, LayerConfig,
    DispatchState`` without a circular dep. The first caller pays the
    one-time cost; subsequent calls return the cached dict.
    """
    global _LAYER_CONFIGS_CACHE
    if _LAYER_CONFIGS_CACHE is None:
        from promptpotter.application.optimization.l1_surface import L1_SECTION_RENDERERS
        from promptpotter.application.optimization.l2_surface import L2_SECTION_RENDERERS

        _LAYER_CONFIGS_CACHE = {
            Layer.L1_GENERATE: LayerConfig(
                sections=L1_SECTION_RENDERERS,
                read_overrides=lambda osp: (
                    osp.l1_section_overrides,
                    osp.l1_section_overrides_text,
                ),
            ),
            Layer.L2: LayerConfig(sections=L2_SECTION_RENDERERS),
            Layer.L3: LayerConfig(sections={}),  # L3 vars ride as extras.
            Layer.L1_CRITIQUE: LayerConfig(sections={}),  # single dispatch_msg via extras.
        }
    return _LAYER_CONFIGS_CACHE


def compile_prompt_vars(
    layer: Layer,
    state: DispatchState,
    opt_sp: OptSearchPoint,
    *,
    extras: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render every section in ``LAYER_CONFIGS[layer]``, apply overrides, merge extras.

    Override order per section (when ``read_overrides`` is set):
    1. ``visibility[name] is False`` → empty string.
    2. ``text[name]`` set → use that text verbatim.
    3. Otherwise → call the registered renderer.

    Non-empty section outputs gain a trailing ``\\n\\n`` so the template
    body stays inert when sections gate off. ``extras`` (per-call scalars
    like ``n_variants``) are merged in as-is — no override processing,
    no separator.
    """
    cfg = get_layer_configs()[layer]
    visible: dict[str, bool] = {}
    text: dict[str, str] = {}
    if cfg.read_overrides is not None:
        visible, text = cfg.read_overrides(opt_sp)

    out: dict[str, str] = {}
    for name, renderer in cfg.sections.items():
        if visible.get(name) is False:
            rendered = ""
        elif name in text:
            rendered = text[name]
        else:
            rendered = renderer(state)
        out[name] = (rendered + "\n\n") if rendered else ""

    if extras:
        out.update(extras)
    return out


# ---------------------------------------------------------------------------
# Shared section renderers — `_section_l2_directive` and `_section_plan` are
# rendered by BOTH L1_GENERATE and L2, so they live with the dispatch types
# instead of in either surface module.
# ---------------------------------------------------------------------------


def _section_l2_directive(ctx: DispatchState) -> str:
    v = ctx.cycle.opt_sp.l2_directive
    if not v:
        return ""
    label = "DIRECTIVE:" if ctx.layer is Layer.L1_GENERATE else "PREVIOUS DIRECTIVE:"
    return f"{label}\n{v}"


def _section_plan(ctx: DispatchState) -> str:
    v = ctx.cycle.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


# Re-exports so the surface modules can import shared section renderers
# from a single canonical home.
SECTION_L2_DIRECTIVE = _section_l2_directive
SECTION_PLAN = _section_plan
