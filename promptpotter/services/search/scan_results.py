"""Post-scan processing: diagnostic resume, winner selection, context preparation,
and scan baseline preparation.

Pure functions for working with scan results — no backend calls, no eval
(except ``decompose_scan_baseline`` which delegates to decompose).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.search_point import JobSearchPoint
from promptpotter.shared.constants import LAYER1_STRING_FIELDS, PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    import pandas as pd

    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign.bootstrap import BackendContext
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.llm_client import LLMClientBase
    from promptpotter.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


@dataclass
class ScanBaselineResult:
    """Result from ``decompose_scan_baseline()``."""

    baseline_jsp: JobSearchPoint
    search_baseline: OptSearchPoint
    scan_diag: dict | None
    was_cached: bool
    restructured_fields: dict[str, str]
    prompt_index: dict | None = field(default=None)


async def decompose_scan_baseline(
    baseline: OptSearchPoint,
    campaign_config: CampaignConfig,
    llm_client: LLMClientBase,
    llm_model: str,
    *,
    pipeline_params: dict | None = None,
    session: BackendContext | None = None,
    scan_variants: dict | None = None,
    force_restructure: bool = False,
    pipeline_schema: PipelineSchema | None = None,
) -> ScanBaselineResult:
    """Restructure baseline instruction into PromptPotter's internal fields.

    Uses alias-aware disk caching so repeated runs reuse the same
    decomposition (stable content hashes -> scan cache hits).

    Returns:
        ScanBaselineResult with the JobSearchPoint baseline, OptSearchPoint
        for display/prompt fields, scan variant diagnosis, cache flag,
        and restructured field values.
    """
    from promptpotter.services.search.context import decompose_prompt_fields_cached

    # Unpack session
    store = session.store if session else None
    backend_id = session.backend_id if session else ""
    pipeline_schema = pipeline_schema or (session.pipeline_schema if session else None)

    # Resolve alias group for cache lookup
    can_cache = bool(store and backend_id)
    alias_hashes: set[str] | None = None
    original_hash = ""
    if can_cache:
        original_hash = hashlib.sha256(
            baseline.render().encode(),
        ).hexdigest()[:16]
        alias_hashes = store.dataset_runs.resolve_aliases(
            backend_id,
            original_hash,
        )

    layer1_fields, was_cached = await decompose_prompt_fields_cached(
        baseline.instruction,
        llm_client,
        model=llm_model,
        store_base_dir=store.base_dir if can_cache else None,
        backend_id=backend_id,
        alias_hashes=alias_hashes,
        rp_hash=original_hash if can_cache else "",
        force=force_restructure,
    )

    search_baseline = baseline.derive_candidate(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="search_baseline (decomposed)",
    )

    # Build restructured fields dict for display
    restructured_fields = {}
    for f in LAYER1_STRING_FIELDS:
        restructured_fields[f] = getattr(search_baseline, f, "") or ""

    # Build JobSearchPoint for evaluation
    baseline_jsp = search_baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )

    # Historical data diagnostic via sp_hash matching
    scan_diag = None
    prompt_index = None
    if can_cache:
        from promptpotter.services.search.coverage import (
            build_prompt_result_index,
            diagnose_scan_variants,
        )

        # Register semantic equivalence
        restructured_hash = hashlib.sha256(
            search_baseline.render().encode(),
        ).hexdigest()[:16]
        store.dataset_runs.register_alias(
            backend_id,
            original_hash,
            restructured_hash,
        )

        prompt_index = build_prompt_result_index(store, backend_id)

        if scan_variants:
            scan_diag = diagnose_scan_variants(
                store,
                backend_id,
                scan_variants,
                baseline_jsp,
                pipeline_schema=pipeline_schema,
            )

    return ScanBaselineResult(
        baseline_jsp=baseline_jsp,
        search_baseline=search_baseline,
        scan_diag=scan_diag,
        was_cached=was_cached,
        restructured_fields=restructured_fields,
        prompt_index=prompt_index,
    )


@dataclass
class ScanContext:
    """Structured scan context for the LLM meta-prompt."""

    leaderboard_text: str = ""
    sensitivity_text: str = ""
    difficulty_text: str = ""
    improving_axes: list[str] = field(default_factory=list)
    has_prompt_axes: bool = False
    tested_values: str = ""
    baseline_accuracy: float = 0.0


@dataclass
class DiagnosticResult:
    """Return value from ``resume_or_build_diagnostic()``."""

    plan_id: str
    search_baseline: OptSearchPoint
    diagnostic: list
    summary: dict
    axis_profiles: list


async def resume_or_build_diagnostic(
    campaign_config: CampaignConfig,
    baseline: OptSearchPoint,
    baseline_results: list,
    llm_client: Any,
    model: str,
    store: ProjectStore,
    backend_id: str,
    dataset: list,
    variant_library: dict | None = None,
) -> DiagnosticResult:
    """Resume or build smart search diagnostic set.

    Returns:
        (plan_id, search_baseline, diagnostic, diag_summary, axis_profiles_or_empty)

    If a plan already exists on disk, skips LLM restructure and diagnostic
    building. If the plan status is ``scan_complete`` or later, also returns
    cached axis profiles.
    """
    import hashlib as _hashlib
    import json as _json

    from promptpotter.config.settings import load_variant_library
    from promptpotter.services.search.context import decompose_prompt_fields
    from promptpotter.services.search.smart_search import (
        build_axis_profiles,
        build_diagnostic_set,
        deserialize_smart_search_plan,
        serialize_smart_search_plan,
        smart_search_plan_identity,
    )

    ss = campaign_config.get("smart_search", {})
    if variant_library is None:
        variant_library = load_variant_library()

    plan_id = smart_search_plan_identity(
        baseline.instruction,
        variant_library,
        ss,
        seed=ss.get("seed", 42),
    )

    existing = store.smart_search.load(backend_id, plan_id)
    if existing:
        status = existing.get("status", "?")
        plan = deserialize_smart_search_plan(existing)

        if plan["scan_results"] and status in ("scan_complete", "search_complete"):
            cached_profiles = plan["scan_results"].get("axis_profiles", [])
            logger.info("Scan complete in plan %s, reusing profiles", plan_id)
            return DiagnosticResult(
                plan_id=plan_id,
                search_baseline=plan["search_baseline_ps"],
                diagnostic=plan["diagnostic"],
                summary=plan["diag_summary"],
                axis_profiles=cached_profiles,
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
            partial_profiles = build_axis_profiles(
                partial_rows,
                _axes,
                len(plan["diagnostic"]),
            )
            logger.info(
                "Scan partial in plan %s: %d axes done, %d profiles",
                plan_id,
                len(partial_completed),
                len(partial_profiles),
            )
            return DiagnosticResult(
                plan_id=plan_id,
                search_baseline=plan["search_baseline_ps"],
                diagnostic=plan["diagnostic"],
                summary=plan["diag_summary"],
                axis_profiles=partial_profiles,
            )

        # diagnostic_built -> reuse saved baseline; prefer sibling with scan data
        siblings = [
            s
            for s in store.smart_search.list_all(backend_id)
            if s["plan_id"] != plan_id
            and s["status"] in ("scan_complete", "search_complete")
            and s.get("variant_library_hash") == existing.get("variant_library_hash", "")
            and s.get("n_axis_profiles", 0) > 0
        ]
        if siblings:
            current_n_diag = plan.get("config", {}).get("n_diagnostic", 6)
            siblings.sort(
                key=lambda s: (
                    s.get("n_diagnostic") != current_n_diag,
                    s["status"] != "scan_complete",
                )
            )
            sib_data = store.smart_search.load(backend_id, siblings[0]["plan_id"])
            assert sib_data is not None
            sib_plan = deserialize_smart_search_plan(sib_data)
            sib_profiles = (sib_plan.get("scan_results") or {}).get("axis_profiles", [])
            logger.info(
                "Adopting scan data from sibling plan %s (%d profiles)",
                siblings[0]["plan_id"],
                len(sib_profiles),
            )
            return DiagnosticResult(
                plan_id=plan_id,
                search_baseline=sib_plan["search_baseline_ps"],
                diagnostic=sib_plan["diagnostic"],
                summary=sib_plan["diag_summary"],
                axis_profiles=sib_profiles,
            )

        logger.info("Plan %s (status: %s), reusing saved diagnostic", plan_id, status)
        return DiagnosticResult(
            plan_id=plan_id,
            search_baseline=plan["search_baseline_ps"],
            diagnostic=plan["diagnostic"],
            summary=plan["diag_summary"],
            axis_profiles=[],
        )

    # Build new plan: LLM restructure + diagnostic set
    logger.info("Building new smart search plan: %s", plan_id)
    layer1_fields = await decompose_prompt_fields(
        baseline.instruction,
        llm_client,
        model=model,
    )
    search_baseline = baseline.derive_candidate(
        **{k: v for k, v in layer1_fields.items() if v},
        changes_description="search_baseline (decomposed)",
    )

    diagnostic, diag_summary = build_diagnostic_set(
        dataset,
        baseline_results,
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
        plan_id,
        config,
        baseline,
        search_baseline,
        layer1_fields,
        diagnostic,
        diag_summary,
        vl_hash,
    )
    store.smart_search.save(backend_id, plan_id, plan_data)

    return DiagnosticResult(
        plan_id=plan_id,
        search_baseline=search_baseline,
        diagnostic=diagnostic,
        summary=diag_summary,
        axis_profiles=[],
    )


def select_scan_winner(
    scan_df: pd.DataFrame,
    axis_profiles: list[dict],
    baseline: JobSearchPoint,
    scan_variants: dict[str, list],
    *,
    baseline_opt: OptSearchPoint | None = None,
    pipeline_schema: PipelineSchema | None = None,
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
        p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
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
                value_idx,
                axis_name,
                len(values),
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
            **prompt_changes,
            changes_description="scan_winner",
        )
        best = best_opt.to_job_search_point(
            base_pipeline_params=baseline.pipeline_params,
            schema=pipeline_schema,
        )
    if param_changes:
        best = best.derive(
            pipeline_params={**(best.pipeline_params or {}), **param_changes},
        )
    logger.debug(
        "select_scan_winner: %d prompt changes, %d param changes from %d improving axes",
        len(prompt_changes),
        len(param_changes),
        len(improving),
    )
    return best


def compute_difficulty_summary(difficulty_df: Any) -> dict[str, int] | None:
    """Aggregate query difficulty classification counts.

    Returns None if the DataFrame is empty or None.
    """
    if difficulty_df is None or len(difficulty_df) == 0:
        return None
    summary: dict[str, int] = difficulty_df["classification"].value_counts().to_dict()
    summary["total"] = len(difficulty_df)
    return summary


def prepare_scan_context(
    scan_df: pd.DataFrame,
    axis_profiles: list[dict],
    scan_variants: dict[str, list],
    baseline_accuracy: float,
    *,
    difficulty_summary: dict | None = None,
) -> ScanContext:
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
        p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]
    improving_axes = [p["axis"] for p in _improving]
    has_prompt_axes = any(p["axis_type"] == "prompt_field" for p in _improving)

    tested_lines = []
    for axis_name in improving_axes:
        axis_rows = scan_df[scan_df["axis"] == axis_name].sort_values(
            "accuracy",
            ascending=False,
        )
        vals = []
        for _, r in axis_rows.iterrows():
            bl = " *baseline*" if r["delta"] == 0.0 else ""
            vals.append(
                f"    [{r['value_idx']}] {r['value_preview'][:50]}  acc={r['accuracy']:.1%}{bl}"
            )
        tested_lines.append(f"  {axis_name} ({len(axis_rows)} values tested):")
        tested_lines.extend(vals)
    tested_values = "\n".join(tested_lines)

    return ScanContext(
        leaderboard_text=leaderboard_text,
        sensitivity_text=sensitivity_text,
        difficulty_text=difficulty_text,
        improving_axes=improving_axes,
        has_prompt_axes=has_prompt_axes,
        tested_values=tested_values,
        baseline_accuracy=baseline_accuracy,
    )


@dataclass
class SeedResult:
    """Result from ``seed_campaign_from_scan()``."""

    best_sp: JobSearchPoint
    merged_pipeline_params: dict | None
    round_entry: dict
    improving_axes: list[dict]


def seed_campaign_from_scan(
    scan_df: pd.DataFrame,
    axis_profiles: list[dict],
    baseline: JobSearchPoint,
    scan_variants: dict[str, list],
    campaign_rounds: list[dict],
    campaign_config: CampaignConfig,
) -> SeedResult:
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Mutates ``campaign_config["pipeline_params"]`` and appends to
    ``campaign_rounds``.

    Returns:
        SeedResult with best_sp, merged params, new round entry, and
        improving axes for display.
    """
    best_sp = select_scan_winner(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
    )

    improving = [
        p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]

    merged_pp = None
    if best_sp.pipeline_params:
        existing_pp = campaign_config.get("pipeline_params", {})
        merged = {**existing_pp, **best_sp.pipeline_params}
        if "steps" in existing_pp:
            merged["steps"] = existing_pp["steps"]
            excluded = set(campaign_config.get("exclude_nodes", []))
            for node_name in excluded:
                merged.pop(node_name, None)
        campaign_config["pipeline_params"] = merged
        merged_pp = merged

    # Compute scan winner accuracy from scan_df (best single-variant result)
    best_row = scan_df.loc[scan_df["accuracy"].idxmax()] if not scan_df.empty else None
    scan_winner_acc = float(best_row["accuracy"]) if best_row is not None else 0.0

    search_opt = OptSearchPoint(
        instruction=best_sp.render(),
        changes_description=f"scan_winner (sp_hash={best_sp.sp_hash()[:12]})",
    )
    round_entry = {
        "round": "search",
        "label": f"smart_search ({search_opt.changes_description or search_opt.id[:12]})",
        "prompt_fields": search_opt,
        "accuracy": scan_winner_acc,
        "hits": int(best_row["hits"]) if best_row is not None else 0,
        "total": int(best_row["total"]) if best_row is not None else 0,
        "results": [],
    }
    campaign_rounds.append(round_entry)

    return SeedResult(
        best_sp=best_sp,
        merged_pipeline_params=merged_pp,
        round_entry=round_entry,
        improving_axes=improving,
    )
