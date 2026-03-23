"""Grid search: plan discovery, building, execution, results display."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from api.models.pipeline_schema import PipelineSchema

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore

from api.services.search import (
    validate_grid_config,
    build_grid_points as _build_grid_points,
    build_combined_state_lookup as _build_combined_state_lookup,
    merge_grid_results as _merge_grid_results,
    run_grid_search as _run_grid_search,
    analyze_grid_results as _analyze_grid_results,
    build_grid_analysis_prompt as _build_grid_analysis_prompt,
    select_grid_winner,
    resolve_point_evals as _resolve_point_evals,
    resume_or_build_grid as _resume_or_build_grid,
    load_grid_plan_results as _load_grid_plan_results,
)

from ._display import _fmt_query_result, _print_interrupt_banner, display_progress
from ._eval import load_eval_dataset

__all__ = [
    # Grid search
    "validate_grid_config", "build_grid_points", "run_grid_search",
    "display_grid_results", "select_grid_winner", "analyze_grid_results",
    "resume_or_build_grid", "merge_grid_results",
    # Grid plan discovery
    "list_grid_plans", "load_grid_plan_results",
    # Grid seeding
    "select_and_seed_grid_winner",
    # Notebook-facing wrappers
    "show_grid_overview",
]


# ---------------------------------------------------------------------------
# Grid Plan Discovery
# ---------------------------------------------------------------------------


def list_grid_plans(store: ProjectStore, backend_id: str) -> list:
    """List all grid search plans with their status."""
    plans = store.grid_plans.list_all(backend_id)
    if not plans:
        print("No grid plans found.")
        return plans

    print(f"Grid plans ({len(plans)}):")
    for p in plans:
        status_icon = {
            "completed": "[done]",
            "in_progress": "[run..]",
        }.get(p["status"], f"[{p['status']}]")

        print(
            f"  {status_icon} {p['plan_id']}  "
            f"{p['n_points']} points  "
            f"(space={p['total_space']}, axes={','.join(p['axes'])})"
        )

    return plans


def load_grid_plan_results(
    store: ProjectStore,
    backend_id: str,
    plan_id: str,
    eval_data: list,
    sample_size: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
    pipeline_params: dict | None = None,
) -> "pd.DataFrame | None":
    """Load stored eval results for a grid plan and return a results DataFrame."""
    df = _load_grid_plan_results(
        store, backend_id, plan_id, eval_data,
        sample_size, shared_queries, seed,
        pipeline_params=pipeline_params,
    )
    if df is not None:
        plan_data = store.grid_plans.load(backend_id, plan_id)
        n_points = len(plan_data.get("grid_points", [])) if plan_data else "?"
        print(f"  {plan_id}: {len(df)}/{n_points} stored")
    return df


def merge_grid_results(*dataframes: "pd.DataFrame") -> "pd.DataFrame":
    """Merge multiple grid result DataFrames, keeping the best accuracy per prompt_state_id."""
    combined = _merge_grid_results(*dataframes)
    print(f"Merged: {len(combined)} unique grid points from {len(dataframes)} plans")
    return combined


def show_grid_overview(
    svc: dict,
    campaign_config: dict,
    merge_plans: bool = False,
) -> dict:
    """List grid plans and load cached results for each.

    Returns:
        Dict with keys: plans, plan_dfs, merged_grid_df (or None).
    """
    store = svc["store"]
    backend_id = svc["backend_id"]

    plans = list_grid_plans(store, backend_id)

    gs = campaign_config["grid_search"]
    _sample_size = gs.get("sample_size", 0)
    shared_queries_flag = gs.get("shared_queries", True)
    seed = gs.get("seed", 42)
    _pp = campaign_config.get("pipeline_params")

    plan_dfs: dict = {}
    if plans:
        eval_data = load_eval_dataset(
            store, backend_id, svc["experiment_id"],
        )
        for p in plans:
            df = load_grid_plan_results(
                store, backend_id, p["plan_id"], eval_data,
                sample_size=_sample_size,
                shared_queries=shared_queries_flag,
                seed=seed,
                pipeline_params=_pp,
            )
            if df is not None and len(df) > 0:
                plan_dfs[p["plan_id"]] = df
                print(f"    best acc={df['accuracy'].max():.1%}")

        if len(plan_dfs) > 1:
            print("\nMultiple plans have results. Set merge_plans=True to combine.")
        elif len(plan_dfs) == 1:
            print("\nOne plan has results. Proceed to 4.5a.")
        else:
            print("\nNo stored results yet. Run 4.5a then 4.5c.")

    merged_grid_df = None
    if merge_plans and len(plan_dfs) > 1:
        merged_grid_df = merge_grid_results(*plan_dfs.values())
        print(f"\nMerged grid results: {len(merged_grid_df)} unique points")

    return {
        "plans": plans,
        "plan_dfs": plan_dfs,
        "merged_grid_df": merged_grid_df,
    }


def build_grid_points(
    grid_config: dict,
    baseline: PromptState,
    grid_budget: int = 0,
    exploration_rate: float = 0.5,
    seed: int = 42,
):
    """Build cartesian product of grid axes as PromptState variants."""
    points, lookup, meta = _build_grid_points(
        grid_config, baseline, grid_budget, exploration_rate, seed,
    )

    if meta["is_uncapped"]:
        print(
            f"Built {meta['n_selected']} grid points "
            f"(requested {grid_budget}, capped at full space of {meta['total_space']})"
        )
    elif grid_budget > 0:
        print(
            f"Sampled {meta['n_selected']}/{meta['total_space']} grid points "
            f"(exploration_rate={meta['exploration_rate']:.2f})"
        )
    else:
        print(f"Built {meta['n_selected']} grid points (full grid)")

    if meta["distance_distribution"]:
        parts = [f"d{d}={c}" for d, c in sorted(meta["distance_distribution"].items())]
        print(f"  Distance distribution: {', '.join(parts)}")

    return points, lookup


# ---------------------------------------------------------------------------
# Grid search (thin wrappers over service functions)
# ---------------------------------------------------------------------------


async def resume_or_build_grid(
    campaign_config: dict,
    baseline: PromptState,
    llm_client: "LLMClientBase",
    model: str,
    store: ProjectStore,
    backend_id: str,
    improvement_areas: str = "",
    pipeline_schema: "PipelineSchema | None" = None,
    pipeline_params: dict | None = None,
) -> tuple:
    """Resume an existing grid plan or build a new one.

    Returns:
        (plan_id, grid_points, grid_state_lookup, grid_axes,
         layer1_fields, grid_baseline)
    """
    result = await _resume_or_build_grid(
        campaign_config, baseline, llm_client, model,
        store, backend_id, improvement_areas=improvement_areas,
        pipeline_schema=pipeline_schema, pipeline_params=pipeline_params,
    )
    # Service returns 7-tuple (plan_id, points, lookup, axes, fields, baseline, resumed)
    (plan_id, grid_points, grid_state_lookup,
     grid_axes, layer1_fields, grid_baseline, resumed) = result

    if resumed:
        print(f"[RESUME] Found existing grid plan: {plan_id}")
        print(f"  Grid points: {len(grid_points)}")
    else:
        print(f"[NEW] Built grid plan: {plan_id}")
        print(f"  Grid points: {len(grid_points)}")
        print(f"  Saved grid plan to disk: {plan_id}")

    return plan_id, grid_points, grid_state_lookup, grid_axes, layer1_fields, grid_baseline


async def run_grid_search(
    grid_points: list,
    state_lookup: dict,
    eval_data: list,
    eval_llm: dict,
    *,
    plan_id: str = "",
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    backend_client=None,
    session_terms: "list | None" = None,
    pipeline_params: "dict | None" = None,
    sample_size: int = 0,
    shared_queries: bool = True,
    grid_seed: int = 42,
    experiment_id: str = "",
    svc: dict | None = None,
) -> pd.DataFrame:
    """Evaluate each grid point on eval_data via the backend."""
    # svc shorthand
    if svc is not None:
        backend_client = backend_client or svc.get("backend_client")
        store = store or svc.get("store")
        backend_id = backend_id or svc.get("backend_id", "")
        session_terms = session_terms or svc.get("session_terms")

    if backend_client is None:
        raise ValueError(
            "backend_client is required. Start the TermNorm backend and pass "
            "svc.get('backend_client') from init_services()."
        )

    # Count stored results for resume display
    eval_plan = _resolve_point_evals(
        grid_points, state_lookup, eval_data,
        sample_size, shared_queries, grid_seed,
        pipeline_params=pipeline_params,
    )
    n_stored = 0
    if store and backend_id:
        for info in eval_plan:
            if store.dataset_runs.load_by_hash(backend_id, info.content_hash):
                n_stored += 1

    n_total = len(grid_points)
    n_remaining = n_total - n_stored
    q_label = (
        f"{sample_size} quer{'y' if sample_size == 1 else 'ies'}"
        if sample_size > 0
        else f"{len(eval_data)} queries"
    )
    if n_stored > 0:
        print(
            f"[resume] Skipping {n_stored}/{n_total} stored grid points, "
            f"evaluating {n_remaining} remaining"
        )
    else:
        print(f"Evaluating {n_total} grid points x {q_label} each")

    _point_counter = [0]

    def on_query_done(point_idx, qi, total_q, result):
        is_cached = result.get("cached", False)
        print(_fmt_query_result(result, cached=is_cached), flush=True)

    def on_point_done(idx, row):
        _point_counter[0] += 1
        print(
            f"  [{_point_counter[0]}/{n_total}] "
            f"acc={row['accuracy']:.1%} ({row['hits']}/{row['total']})"
        )

    def on_point_reused(idx, row):
        pass

    try:
        df = await _run_grid_search(
            grid_points, state_lookup, eval_data, backend_client,
            on_point_done=on_point_done,
            on_query_done=on_query_done,
            on_point_reused=on_point_reused,
            request_delay=eval_llm.get("request_delay", 1.0),
            store=store,
            backend_id=backend_id,
            session_terms=session_terms,
            pipeline_params=pipeline_params,
            sample_size=sample_size,
            shared_queries=shared_queries,
            seed=grid_seed,
            plan_id=plan_id,
            experiment_id=experiment_id,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        import pandas as _pd
        _print_interrupt_banner(
            "Grid search",
            completed=f"{_point_counter[0]}/{n_total} grid points",
            saved="completed points saved via evaluate_prompt_cached (content-hash dedup)",
            resume_hint="re-run this cell -- stored points will be skipped automatically",
        )
        return _pd.DataFrame(columns=[
            "prompt_state_id", "hits", "total", "accuracy", "errors",
        ])

    if plan_id:
        print(f"Grid plan {plan_id} marked as completed.")

    return df


def display_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    top_k: int = 5,
) -> None:
    """Display ranked table, marginal stats, and pairwise heatmaps."""
    from IPython.display import display as ipy_display

    axis_names = list(grid_config.keys())

    print(f"\n{'=' * 70}")
    print(f"GRID RESULTS -- TOP {top_k}")
    print(f"{'=' * 70}")
    display_cols = axis_names + ["accuracy", "hits", "total", "errors"]
    ipy_display(grid_df[display_cols].head(top_k))

    print(f"\n{'=' * 70}")
    print("MARGINAL STATS (mean accuracy per axis value)")
    print(f"{'=' * 70}")
    for name in axis_names:
        marginal = grid_df.groupby(name)["accuracy"].mean().sort_values(ascending=False)
        print(f"\n  {name}:")
        for idx, acc in marginal.items():
            value = grid_config[name][idx]
            label = value[:60] if value else "(empty)"
            print(f"    [{idx}] {acc:.1%}  {label}")

    if len(axis_names) >= 2:
        print(f"\n{'=' * 70}")
        print("PAIRWISE INTERACTION HEATMAPS")
        print(f"{'=' * 70}")
        for i, name_a in enumerate(axis_names):
            for name_b in axis_names[i + 1:]:
                pivot = grid_df.pivot_table(
                    values="accuracy",
                    index=name_a,
                    columns=name_b,
                    aggfunc="mean",
                )
                print(f"\n  {name_a} vs {name_b}:")
                styled = pivot.style.background_gradient(
                    cmap="RdYlGn", vmin=0, vmax=1
                ).format("{:.1%}")
                ipy_display(styled)


def select_and_seed_grid_winner(
    grid_df: "pd.DataFrame | None",
    merged_grid_df: "pd.DataFrame | None",
    grid_state_lookup: dict,
    plan_dfs: dict,
    svc: dict,
    campaign_rounds: list,
) -> dict:
    """Select grid winner, build combined lookup if needed, seed campaign."""
    winner_df = merged_grid_df if merged_grid_df is not None else grid_df

    if merged_grid_df is not None and plan_dfs:
        combined_lookup = _build_combined_state_lookup(
            svc["store"], svc["backend_id"], list(plan_dfs.keys()),
        )
        grid_winner = select_grid_winner(winner_df, combined_lookup)
    else:
        grid_winner = select_grid_winner(winner_df, grid_state_lookup)

    campaign_rounds.append(grid_winner)

    winner_ps = grid_winner["prompt_state"]
    print("\nLayer 1 breakdown of grid winner:")
    for field in (
        "persona", "task_intent", "problem_description",
        "instruction", "thinking_style", "answer_format",
    ):
        val = getattr(winner_ps, field)
        if val:
            print(f"  {field}: {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"  {field}: (empty)")

    rendered = winner_ps.render()
    print(f"\nRendered prompt preview ({len(rendered)} chars):")
    print(rendered[:500])
    if len(rendered) > 500:
        print("...")

    display_progress(campaign_rounds)

    print(
        f"\nGrid winner appended to campaign_rounds as round "
        f"'{grid_winner['round']}'. Proceed to Section 5 for optimization."
    )

    return grid_winner


async def analyze_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    llm_client: "LLMClientBase",
    model: str = "",
) -> dict:
    """LLM analysis of grid search results."""
    prompt = _build_grid_analysis_prompt(grid_df, grid_config)
    print("LLM ANALYSIS PROMPT")
    print("=" * 70)
    print(prompt)
    print("=" * 70)

    print(f"\nCalling {model or '?'} ...")

    analysis = await _analyze_grid_results(
        grid_df, grid_config, llm_client, model=model,
    )

    print(f"\n{'=' * 70}")
    print("GRID ANALYSIS (LLM response)")
    print(f"{'=' * 70}")
    for finding in analysis.get("key_findings", []):
        print(f"  - {finding}")
    print(f"\n  Strongest fields: {analysis.get('strongest_fields', [])}")
    print(f"  Recommended focus: {analysis.get('recommended_focus', '')}")
    print(f"  Advice: {analysis.get('campaign_advice', '')}")

    return analysis
