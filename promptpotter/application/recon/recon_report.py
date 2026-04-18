"""Post-scan processing: diagnostic resume, winner selection, context preparation,
and scan baseline preparation.

Pure functions for working with scan results — no backend calls, no eval
(except ``decompose_recon_baseline`` which delegates to decompose).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    import pandas as pd

    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.llm.client import LLMClientBase
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)


@dataclass
class ScanBaselineResult:
    """Result from ``decompose_recon_baseline()``."""

    baseline_jsp: JobSearchPoint
    search_baseline: OptSearchPoint
    was_cached: bool
    restructured_fields: dict[str, str]


async def decompose_recon_baseline(
    baseline: OptSearchPoint,
    campaign_config: CampaignConfig,
    llm_client: LLMClientBase,
    llm_model: str,
    *,
    pipeline_params: dict | None = None,
    session: SessionEnv | None = None,
    recon_variants: dict | None = None,
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
    from promptpotter.application.optimization.pipeline import decompose_prompt_fields_cached

    # Unpack session
    store = session.store if session else None
    backend_id = session.backend_id if session else ""
    pipeline_schema = pipeline_schema or (session.pipeline_schema if session else None)

    # Resolve alias group for cache lookup
    can_cache = bool(store and backend_id)
    alias_hashes: set[str] | None = None
    original_hash = ""
    if can_cache:
        assert store is not None
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
        store_base_dir=store.base_dir if store and can_cache else None,
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
    for f in PROMPT_STRING_FIELDS:
        restructured_fields[f] = getattr(search_baseline, f, "") or ""

    # Build JobSearchPoint for evaluation
    baseline_jsp = search_baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )

    # Register semantic equivalence between the raw and restructured prompts
    # so downstream cache lookups can find archived results under either form.
    if can_cache:
        assert store is not None
        restructured_hash = hashlib.sha256(
            search_baseline.render().encode(),
        ).hexdigest()[:16]
        store.dataset_runs.register_alias(
            backend_id,
            original_hash,
            restructured_hash,
        )

    return ScanBaselineResult(
        baseline_jsp=baseline_jsp,
        search_baseline=search_baseline,
        was_cached=was_cached,
        restructured_fields=restructured_fields,
    )


@dataclass
class ReconBrief:
    """Structured scan brief for the LLM meta-prompt."""

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


def _partial_profiles_from_rows(
    recon_results: dict,
    diagnostic_len: int,
) -> list:
    """Rebuild axis profiles from the rows of a partial scan."""
    from promptpotter.application.intelligence import load_variant_library
    from promptpotter.application.recon.adaptive_recon import build_axis_profiles

    vl = load_variant_library()
    axes: list[tuple[str, str, list]] = []
    for name, vals in vl.get("prompt_fields", {}).items():
        if len(vals) > 1:
            axes.append((name, "prompt_field", vals))
    for name, vals in vl.get("pipeline_params", {}).items():
        if len(vals) > 1:
            axes.append((name, "pipeline_param", vals))
    rows = recon_results.get("rows", [])
    return build_axis_profiles(rows, axes, diagnostic_len)


async def resume_or_build_diagnostic(
    campaign_config: CampaignConfig,
    baseline: OptSearchPoint,
    baseline_results: list,
    llm_client: Any,
    model: str,
    store: Stores,
    backend_id: str,
    dataset: list,
    variant_library: dict | None = None,
) -> DiagnosticResult:
    """Resume or build a smart-search diagnostic set.

    Asks ``store.recon_plans.find_reusable_plan`` for an on-disk match. On a
    hit, post-processes the match according to its ``kind`` (complete / partial
    / sibling / diagnostic_only). On a miss, runs the LLM restructure + builds
    a fresh diagnostic set and saves the new plan.
    """
    from promptpotter.application.intelligence import load_variant_library
    from promptpotter.application.optimization.pipeline import decompose_prompt_fields
    from promptpotter.application.recon.adaptive_recon import (
        adaptive_recon_plan_identity,
        build_diagnostic_set,
        deserialize_adaptive_recon_plan,
        serialize_adaptive_recon_plan,
    )

    ss = campaign_config.adaptive_recon.model_dump()
    if variant_library is None:
        variant_library = load_variant_library()

    plan_id = adaptive_recon_plan_identity(
        baseline.instruction,
        variant_library,
        ss,
        seed=campaign_config.adaptive_recon.seed,
    )

    match = store.recon_plans.find_reusable_plan(backend_id, plan_id)
    if match is not None:
        plan = deserialize_adaptive_recon_plan(match.data)
        recon_results = plan.get("recon_results") or {}
        if match.kind == "complete":
            profiles = recon_results.get("axis_profiles", [])
            logger.info("Scan complete in plan %s, reusing profiles", plan_id)
        elif match.kind == "partial":
            profiles = _partial_profiles_from_rows(recon_results, len(plan["diagnostic"]))
            logger.info(
                "Scan partial in plan %s: %d axes done, %d profiles",
                plan_id,
                len(recon_results.get("completed_axes", [])),
                len(profiles),
            )
        elif match.kind == "sibling":
            profiles = recon_results.get("axis_profiles", [])
            logger.info(
                "Adopting scan data from sibling plan %s (%d profiles)",
                plan["plan_id"],
                len(profiles),
            )
        else:  # diagnostic_only
            profiles = []
            logger.info("Plan %s (status: %s), reusing saved diagnostic", plan_id, plan["status"])
        return DiagnosticResult(
            plan_id=plan_id,
            search_baseline=plan["search_baseline_ps"],
            diagnostic=plan["diagnostic"],
            summary=plan["diag_summary"],
            axis_profiles=profiles,
        )

    # No reusable plan — build from scratch.
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
    vl_hash = hashlib.sha256(json.dumps(variant_library, sort_keys=True).encode()).hexdigest()[:12]
    config = {
        "n_diagnostic": ss.get("n_diagnostic", 6),
        "max_rounds": ss.get("max_rounds", 3),
        "stop_threshold": ss.get("stop_threshold", 0.0),
    }
    plan_data = serialize_adaptive_recon_plan(
        plan_id,
        config,
        baseline,
        search_baseline,
        layer1_fields,
        diagnostic,
        diag_summary,
        vl_hash,
    )
    store.recon_plans.save(backend_id, plan_id, plan_data)

    return DiagnosticResult(
        plan_id=plan_id,
        search_baseline=search_baseline,
        diagnostic=diagnostic,
        summary=diag_summary,
        axis_profiles=[],
    )


def _improving_axes(axis_profiles: list[dict]) -> list[dict]:
    """Axes with positive delta that weren't skipped — the single filter used
    by winner selection, brief formatting, and campaign seeding."""
    return [p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"]


def _compose_winner(
    recon_df: pd.DataFrame,
    improving: list[dict],
    baseline: JobSearchPoint,
    recon_variants: dict[str, list],
    baseline_opt: OptSearchPoint | None,
    pipeline_schema: PipelineSchema | None,
) -> JobSearchPoint:
    """Compose best-per-axis values into a single JobSearchPoint."""
    prompt_changes: dict[str, Any] = {}
    param_changes: dict[str, Any] = {}

    for profile in improving:
        axis_name = profile["axis"]
        axis_rows = recon_df[recon_df["axis"] == axis_name]
        if axis_rows.empty:
            continue
        best_row_axis = axis_rows.loc[axis_rows["accuracy"].idxmax()]
        value_idx = int(best_row_axis["value_idx"])
        values = recon_variants.get(axis_name, [])
        if value_idx >= len(values):
            logger.warning(
                "finalize_scan: value_idx %d out of range for %s (len=%d)",
                value_idx,
                axis_name,
                len(values),
            )
            continue
        target = prompt_changes if axis_name in PROMPT_STRING_FIELDS else param_changes
        target[axis_name] = values[value_idx]

    best_sp = baseline
    if prompt_changes:
        assert baseline_opt is not None, (
            "baseline_opt required for prompt_field perturbation in finalize_scan"
        )
        best_sp = baseline_opt.derive_candidate(
            **prompt_changes,
            changes_description="recon_winner",
        ).to_job_search_point(
            base_pipeline_params=baseline.pipeline_params,
            schema=pipeline_schema,
        )
    if param_changes:
        best_sp = best_sp.derive(
            pipeline_params={**(best_sp.pipeline_params or {}), **param_changes},
        )
    logger.debug(
        "finalize_scan: %d prompt changes, %d param changes from %d improving axes",
        len(prompt_changes),
        len(param_changes),
        len(improving),
    )
    return best_sp


def compute_difficulty_summary(difficulty_df: Any) -> dict[str, int] | None:
    """Aggregate query difficulty classification counts.

    Returns None if the DataFrame is empty or None.
    """
    if difficulty_df is None or len(difficulty_df) == 0:
        return None
    summary: dict[str, int] = difficulty_df["classification"].value_counts().to_dict()
    summary["total"] = len(difficulty_df)
    return summary


def prepare_recon_brief(
    recon_df: pd.DataFrame,
    axis_profiles: list[dict],
    recon_variants: dict[str, list],
    baseline_accuracy: float,
    *,
    difficulty_summary: dict | None = None,
) -> ReconBrief:
    """Build structured scan brief for the LLM meta-prompt.

    All formatting is deterministic — no LLM calls. Returns a dict with
    pre-formatted text sections plus structured data for downstream use.
    """
    sort_col = "composite" if "composite" in recon_df.columns else "accuracy"
    ranked = recon_df.sort_values(sort_col, ascending=False)

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

    improving = _improving_axes(axis_profiles)
    improving_axes = [p["axis"] for p in improving]
    has_prompt_axes = any(p["axis_type"] == "prompt_field" for p in improving)

    tested_lines = []
    for axis_name in improving_axes:
        axis_rows = recon_df[recon_df["axis"] == axis_name].sort_values(
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

    return ReconBrief(
        leaderboard_text=leaderboard_text,
        sensitivity_text=sensitivity_text,
        difficulty_text=difficulty_text,
        improving_axes=improving_axes,
        has_prompt_axes=has_prompt_axes,
        tested_values=tested_values,
        baseline_accuracy=baseline_accuracy,
    )


@dataclass
class FinalizedScan:
    """Result from ``finalize_scan()``.

    ``merged_pipeline_params`` and ``round_entry`` are ``None`` when called
    in read-only mode (no ``campaign_config`` / ``campaign_rounds``).
    """

    best_sp: JobSearchPoint
    improving_axes: list[dict]
    merged_pipeline_params: dict | None = None
    round_entry: dict | None = None


def finalize_scan(
    recon_df: pd.DataFrame,
    axis_profiles: list[dict],
    baseline: JobSearchPoint,
    recon_variants: dict[str, list],
    *,
    campaign_rounds: list[dict] | None = None,
    campaign_config: CampaignConfig | None = None,
    existing_pipeline_params: dict | None = None,
    baseline_opt: OptSearchPoint | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> FinalizedScan:
    """Pick scan winner and, if campaign state is supplied, seed the campaign.

    Composes the best per-axis value from positively-improving axes into a
    single ``JobSearchPoint``. Returns the merged pipeline_params on
    ``FinalizedScan.merged_pipeline_params`` — caller writes it to
    ``session.pipeline_params`` (we do NOT mutate user config).  Pass the
    current ``session.pipeline_params`` as ``existing_pipeline_params`` so
    the merge keeps already-filtered ``steps``.
    """
    improving = _improving_axes(axis_profiles)
    best_sp = _compose_winner(
        recon_df, improving, baseline, recon_variants, baseline_opt, pipeline_schema
    )

    if campaign_config is None or campaign_rounds is None:
        return FinalizedScan(best_sp=best_sp, improving_axes=improving)

    merged_pp = None
    if best_sp.pipeline_params:
        existing_pp: dict = existing_pipeline_params or {}
        merged = {**existing_pp, **best_sp.pipeline_params}
        if "steps" in existing_pp:
            merged["steps"] = existing_pp["steps"]
            for node_name in set(campaign_config.exclude_nodes or []):
                merged.pop(node_name, None)
        merged_pp = merged

    best_row = recon_df.loc[recon_df["accuracy"].idxmax()] if not recon_df.empty else None
    search_opt = OptSearchPoint(
        instruction=best_sp.render(),
        changes_description=f"recon_winner (sp_hash={best_sp.sp_hash()[:12]})",
    )
    round_entry = {
        "round": "search",
        "label": f"adaptive_recon ({search_opt.changes_description or search_opt.id[:12]})",
        "prompt_fields": search_opt,
        "accuracy": float(best_row["accuracy"]) if best_row is not None else 0.0,
        "hits": int(best_row["hits"]) if best_row is not None else 0,
        "total": int(best_row["total"]) if best_row is not None else 0,
        "results": [],
    }
    campaign_rounds.append(round_entry)

    return FinalizedScan(
        best_sp=best_sp,
        improving_axes=improving,
        merged_pipeline_params=merged_pp,
        round_entry=round_entry,
    )
