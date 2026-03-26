"""Post-scan processing: diagnostic resume, winner selection, and context preparation.

Pure functions for working with scan results — no backend calls, no eval.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

from api.models.opt_search_point import OptSearchPoint, PROMPT_STRING_FIELDS
from api.models.search_point import JobSearchPoint

if TYPE_CHECKING:
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diagnostic set resume / build
# ---------------------------------------------------------------------------


async def resume_or_build_diagnostic(
    campaign_config: dict,
    baseline: OptSearchPoint,
    baseline_results: list,
    llm_client: Any,
    model: str,
    store: "ProjectStore",
    backend_id: str,
    eval_data: list,
    improvement_areas: str = "",
    variant_library: dict | None = None,
) -> tuple[str, OptSearchPoint, list, dict, list]:
    """Resume or build smart search diagnostic set.

    Returns:
        (plan_id, search_baseline, diagnostic, diag_summary, axis_profiles_or_empty)

    If a plan already exists on disk, skips LLM restructure and diagnostic
    building. If the plan status is ``scan_complete`` or later, also returns
    cached axis profiles.
    """
    import hashlib as _hashlib
    import json as _json

    from api.config.settings import load_variant_library
    from api.services.search.context import restructure_context
    from api.services.search.plan_persistence import (
        deserialize_smart_search_plan,
        serialize_smart_search_plan,
        smart_search_plan_identity,
    )
    from api.services.search.smart_search import _profiles_from_rows, build_diagnostic_set

    ss = campaign_config.get("smart_search", {})
    if variant_library is None:
        variant_library = load_variant_library()

    plan_id = smart_search_plan_identity(
        baseline.instruction,
        variant_library,
        ss,
        improvement_areas,
        seed=ss.get("seed", 42),
    )

    existing = store.smart_search.load(backend_id, plan_id)
    if existing:
        status = existing.get("status", "?")
        plan = deserialize_smart_search_plan(existing)

        if plan["scan_results"] and status in ("scan_complete", "search_complete"):
            cached_profiles = plan["scan_results"].get("axis_profiles", [])
            logger.info("Scan complete in plan %s, reusing profiles", plan_id)
            return (
                plan_id,
                plan["search_baseline_ps"],
                plan["diagnostic"],
                plan["diag_summary"],
                cached_profiles,
            )
        if plan["scan_results"] and status == "scan_partial":
            partial_rows = plan["scan_results"].get("rows", [])
            partial_completed = plan["scan_results"].get("completed_axes", [])
            # Rebuild axes list to compute partial profiles
            _vl = load_variant_library()
            _axes: list[tuple[str, str, list]] = []
            for name, vals in _vl.get("prompt_fields", {}).items():
                if len(vals) > 1:
                    _axes.append((name, "prompt_field", vals))
            for name, vals in _vl.get("pipeline_params", {}).items():
                if len(vals) > 1:
                    _axes.append((name, "pipeline_param", vals))
            partial_profiles = _profiles_from_rows(
                partial_rows, _axes, len(plan["diagnostic"]),
            )
            logger.info(
                "Scan partial in plan %s: %d axes done, %d profiles",
                plan_id, len(partial_completed), len(partial_profiles),
            )
            return (
                plan_id,
                plan["search_baseline_ps"],
                plan["diagnostic"],
                plan["diag_summary"],
                partial_profiles,
            )

        # diagnostic_built -> reuse saved baseline; prefer sibling with scan data
        siblings = [
            s for s in store.smart_search.list_all(backend_id)
            if s["plan_id"] != plan_id
            and s["status"] in ("scan_complete", "search_complete")
            and s.get("variant_library_hash") == existing.get("variant_library_hash", "")
            and s.get("n_axis_profiles", 0) > 0
        ]
        if siblings:
            current_n_diag = plan.get("config", {}).get("n_diagnostic", 6)
            siblings.sort(key=lambda s: (
                s.get("n_diagnostic") != current_n_diag,
                s["status"] != "scan_complete",
            ))
            sib_data = store.smart_search.load(backend_id, siblings[0]["plan_id"])
            sib_plan = deserialize_smart_search_plan(sib_data)
            sib_profiles = (sib_plan.get("scan_results") or {}).get("axis_profiles", [])
            logger.info(
                "Adopting scan data from sibling plan %s (%d profiles)",
                siblings[0]["plan_id"], len(sib_profiles),
            )
            return (
                plan_id,
                sib_plan["search_baseline_ps"],
                sib_plan["diagnostic"],
                sib_plan["diag_summary"],
                sib_profiles,
            )

        logger.info("Plan %s (status: %s), reusing saved diagnostic", plan_id, status)
        return (
            plan_id,
            plan["search_baseline_ps"],
            plan["diagnostic"],
            plan["diag_summary"],
            [],
        )

    # Build new plan: LLM restructure + diagnostic set
    logger.info("Building new smart search plan: %s", plan_id)
    layer1_fields = await restructure_context(
        baseline.instruction, llm_client,
        model=model,
        improvement_areas=improvement_areas,
    )
    search_baseline = baseline.derive_candidate(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="search_baseline (decomposed)",
    )

    diagnostic, diag_summary = build_diagnostic_set(
        eval_data, baseline_results,
        n_queries=ss.get("n_diagnostic", 6),
    )

    # Compute a short hash of the full variant library for traceability
    vl_json = _json.dumps(variant_library, sort_keys=True)
    vl_hash = _hashlib.sha256(vl_json.encode()).hexdigest()[:12]

    config = {
        "n_diagnostic": ss.get("n_diagnostic", 6),
        "max_rounds": ss.get("max_rounds", 3),
        "stop_threshold": ss.get("stop_threshold", 0.0),
    }
    plan_data = serialize_smart_search_plan(
        plan_id, config, baseline, search_baseline,
        layer1_fields, diagnostic, diag_summary, vl_hash,
    )
    store.smart_search.save(backend_id, plan_id, plan_data)

    return plan_id, search_baseline, diagnostic, diag_summary, []


# ---------------------------------------------------------------------------
# Scan winner selection
# ---------------------------------------------------------------------------


def select_scan_winner(
    scan_df: "pd.DataFrame",
    axis_profiles: list[dict],
    baseline: JobSearchPoint,
    scan_variants: dict[str, list],
    *,
    baseline_opt: OptSearchPoint | None = None,
) -> JobSearchPoint:
    """Pick best variant per sensitive axis from OAT scan results.

    Composes the best-performing value for each axis that showed positive
    improvement (best_delta > 0) into a single JobSearchPoint.

    Args:
        baseline_opt: Required when scan_variants contains prompt_field axes.
            Used to derive prompt-field perturbations via OptSearchPoint.
    """
    prompt_changes: dict[str, Any] = {}
    param_changes: dict[str, Any] = {}

    improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]

    for profile in improving:
        axis_name = profile["axis"]
        axis_rows = scan_df[scan_df["axis"] == axis_name]
        if axis_rows.empty:
            continue
        best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
        value_idx = int(best_row["value_idx"])
        values = scan_variants.get(axis_name, [])
        if value_idx >= len(values):
            logger.warning(
                "select_scan_winner: value_idx %d out of range for %s (len=%d)",
                value_idx, axis_name, len(values),
            )
            continue
        value = values[value_idx]
        if axis_name in PROMPT_STRING_FIELDS:
            prompt_changes[axis_name] = value
        else:
            param_changes[axis_name] = value

    best = baseline
    if prompt_changes:
        assert baseline_opt is not None, (
            "baseline_opt required for prompt_field perturbation in select_scan_winner"
        )
        best_opt = baseline_opt.derive_candidate(
            **prompt_changes, changes_description="scan_winner",
        )
        best = best_opt.to_job_search_point(
            model=baseline.model,
            temperature=baseline.temperature,
            base_pipeline_params=baseline.pipeline_params,
        )
    if param_changes:
        best = best.derive(
            pipeline_params={**(best.pipeline_params or {}), **param_changes},
        )
    logger.debug(
        "select_scan_winner: %d prompt changes, %d param changes from %d improving axes",
        len(prompt_changes), len(param_changes), len(improving),
    )
    return best


# ---------------------------------------------------------------------------
# Scan context preparation
# ---------------------------------------------------------------------------


def prepare_scan_context(
    scan_df: "pd.DataFrame",
    axis_profiles: list[dict],
    scan_variants: dict[str, list],
    baseline_accuracy: float,
    *,
    difficulty_summary: dict | None = None,
) -> dict:
    """Build structured scan context for the LLM meta-prompt.

    All formatting is deterministic — no LLM calls. Returns a dict with
    pre-formatted text sections plus structured data for downstream use.
    """
    sort_col = "composite" if "composite" in scan_df.columns else "accuracy"
    ranked = scan_df.sort_values(sort_col, ascending=False)

    lines = []
    for _, r in ranked.iterrows():
        label = r["value_preview"]
        bl_tag = " (baseline)" if r["delta"] == 0.0 else ""
        lines.append(
            f"  {r['axis']:<24s} {label:<40s} "
            f"acc={r['accuracy']:.1%}  delta={r['delta']:+.1%}"
            f"  hits={r['hits']}/{r['total']}{bl_tag}"
        )
    leaderboard_text = "\n".join(lines)

    sens_lines = []
    for i, p in enumerate(axis_profiles, 1):
        room = ""
        if p["best_delta"] > 0:
            room = f"  [room: best_delta={p['best_delta']:+.1%}]"
        sens_lines.append(
            f"  {i}. {p['axis']:<24s} type={p['axis_type']:<14s} "
            f"sensitivity={p['sensitivity_range']:.3f}  "
            f"card={p['cardinality']}  budget={p['exploration_budget']}"
            f"{room}"
        )
    sensitivity_text = "\n".join(sens_lines)

    if difficulty_summary:
        total = difficulty_summary.get("total", 0)
        parts = []
        for cat in ("easy", "discriminating", "hard", "error"):
            c = difficulty_summary.get(cat, 0)
            pct = f" ({c / total:.0%})" if total else ""
            parts.append(f"{cat}: {c}{pct}")
        difficulty_text = f"  Query classes: {' | '.join(parts)}"
    else:
        difficulty_text = "  (not available)"

    _improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]
    improving_axes = [p["axis"] for p in _improving]
    has_prompt_axes = any(p["axis_type"] == "prompt_field" for p in _improving)

    tested_lines = []
    for axis_name in improving_axes:
        axis_rows = scan_df[scan_df["axis"] == axis_name].sort_values(
            "accuracy", ascending=False,
        )
        vals = []
        for _, r in axis_rows.iterrows():
            bl = " *baseline*" if r["delta"] == 0.0 else ""
            vals.append(f"    [{r['value_idx']}] {r['value_preview'][:50]}"
                        f"  acc={r['accuracy']:.1%}{bl}")
        tested_lines.append(f"  {axis_name} ({len(axis_rows)} values tested):")
        tested_lines.extend(vals)
    tested_values = "\n".join(tested_lines)

    return {
        "leaderboard_text": leaderboard_text,
        "sensitivity_text": sensitivity_text,
        "difficulty_text": difficulty_text,
        "improving_axes": improving_axes,
        "has_prompt_axes": has_prompt_axes,
        "tested_values": tested_values,
        "baseline_accuracy": baseline_accuracy,
    }
