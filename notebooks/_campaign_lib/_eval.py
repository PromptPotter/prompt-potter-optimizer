"""Diagnostics, baseline eval, eval dataset loading, and LLM re-exports."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tqdm.auto import tqdm

if TYPE_CHECKING:
    import pandas as pd

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore

from api.services.campaign.campaign_init import run_baseline_eval as _run_baseline_eval
from api.services.prompt_eval import load_baseline_prompt
from api.services.search.coverage import (
    analyze_candidate_coverage as _analyze_candidate_coverage,
)
from api.services.prompt_optimizer import (
    generate_candidates,
    generate_suggestions,
)
from api.services.search import (
    load_eval_dataset as _load_eval_dataset,
)

from ._display import _fmt_query_result, _print_interrupt_banner, display_progress

__all__ = [
    # Baseline & eval
    "load_baseline_prompt", "run_baseline_eval",
    "analyze_candidate_coverage", "load_eval_dataset",
    "run_coverage_diagnostic",
    # Candidates & suggestions
    "generate_candidates", "generate_suggestions",
    # Entity profiles
    "show_entity_profiles",
]


# ---------------------------------------------------------------------------
# Entity profiles display
# ---------------------------------------------------------------------------


def show_entity_profiles(eval_data: list, n_samples: int = 3) -> None:
    """Display sample entity profiles from eval data for qualitative review."""
    samples = [
        r for r in eval_data
        if r.get("pipeline_data", {}).get("entity_profile")
    ][:n_samples]

    if not samples:
        print("No entity profiles found in eval data.")
        return

    for i, s in enumerate(samples):
        profile = s["pipeline_data"]["entity_profile"]
        print(f"--- Sample {i + 1}: {s['query'][:60]} ---")
        print(f"  Core concept: {profile.get('core_concept', '?')}")
        print(f"  Profile keys: {list(profile.keys())}")
        print(f"  Ground truth: {s['ground_truth']}")
        candidates = s.get("pipeline_data", {}).get(
            "token_matched_candidates", [],
        )[:5]
        print(
            f"  Top 5 candidates: "
            f"{[c[0] if isinstance(c, (list, tuple)) else c for c in candidates]}"
        )
        print()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def analyze_candidate_coverage(replay_results: list) -> pd.DataFrame:
    """Analyze candidate coverage and print diagnostic summary."""
    import pandas as pd

    result = _analyze_candidate_coverage(replay_results)
    rows = result["rows"]
    covered = result["covered"]
    total_cov = result["total"]
    coverage_pct = result["coverage_pct"]
    rank_dist = result["rank_distribution"]

    cov_df = pd.DataFrame(rows)

    if total_cov == 0 and replay_results:
        print("CANDIDATE COVERAGE")
        print("=" * 50)
        print(f"  No pipeline data in {len(replay_results)} items.")
        print("  Run baseline eval first to get coverage analysis.")
        return cov_df

    print("CANDIDATE COVERAGE")
    print("=" * 50)
    print(f"  Ground truth in candidates: {covered}/{total_cov} ({coverage_pct:.1f}%)")
    print(f"  Missing from candidates:    {total_cov - covered}/{total_cov}")
    print()

    if rank_dist:
        print("Rank distribution (ground truth position in candidate list):")
        print(f"  Rank 1 (already top):  {rank_dist['rank_1']}")
        print(f"  Rank 2-5:              {rank_dist['rank_2_5']}")
        print(f"  Rank 6-10:             {rank_dist['rank_6_10']}")
        print(f"  Rank 11-20:            {rank_dist['rank_11_20']}")
        print(f"  Rank >20:              {rank_dist['rank_gt_20']}")
        print(f"  Mean rank:             {rank_dist['mean_rank']:.1f}")
        print(f"  Median rank:           {rank_dist['median_rank']:.0f}")

    print()
    if result["viable"]:
        print(
            f"DECISION: Coverage {coverage_pct:.0f}% > 50% threshold "
            "-> Reranker optimization is VIABLE."
        )
        print(
            "  The ground truth exists in the candidate set; "
            "a better reranker prompt can promote it."
        )
    else:
        print(
            f"DECISION: Coverage {coverage_pct:.0f}% <= 50% threshold "
            "-> Reranker optimization has LIMITED value."
        )
        print(
            "  The ground truth is missing from candidates too often. "
            "Consider improving token matching first."
        )

    return cov_df


def run_coverage_diagnostic(
    baseline_results: list,
    store: "ProjectStore",
    backend_id: str,
    experiment_id: str,
) -> pd.DataFrame | None:
    """Load coverage data and run candidate coverage + entity profile analysis.

    Uses baseline_results if available, otherwise falls back to stored
    eval dataset filtered for token_matched_candidates.

    Returns:
        Coverage DataFrame, or None if no data available.
    """
    if baseline_results:
        cov_data = baseline_results
    else:
        cov_data = load_eval_dataset(store, backend_id, experiment_id)
        cov_data = [
            r for r in cov_data
            if r.get("pipeline_data", {}).get("token_matched_candidates")
        ]

    if not cov_data:
        print("No coverage data -- run baseline eval or sync traces first.")
        return None

    cov_df = analyze_candidate_coverage(cov_data)
    show_entity_profiles(cov_data)
    return cov_df


# ---------------------------------------------------------------------------
# Baseline & Eval  (thin wrappers adding tqdm/print output)
# ---------------------------------------------------------------------------


async def run_baseline_eval(
    baseline: PromptState,
    eval_data: list,
    campaign_config: dict,
    svc: dict,
) -> tuple:
    """Evaluate baseline prompt and initialize campaign_rounds.

    Returns:
        (campaign_rounds, baseline_results).
    """
    eval_llm = campaign_config["eval_llm"]
    model = eval_llm.get("model", "")
    temperature = eval_llm.get("temperature", 0.1)

    pbar = tqdm(total=len(eval_data) or 1, desc="Baseline eval", unit="query")

    def _on_result(result, index, total):
        pbar.total = total
        is_cached = result.get("cached", False)
        tqdm.write(_fmt_query_result(result, cached=is_cached))
        pbar.update(1)

    from api.services.obs.observability_logger import ObsLogger
    _obs = ObsLogger(svc["store"].base_dir, svc.get("backend_id", ""), langfuse=None)

    try:
        campaign_rounds, baseline_results = await _run_baseline_eval(
            baseline, eval_data, svc.get("backend_client"),
            pipeline_params=campaign_config.get("pipeline_params"),
            store=svc.get("store"), backend_id=svc.get("backend_id", ""),
            experiment_id=svc.get("experiment_id", ""),
            model=model, temperature=temperature,
            on_result=_on_result,
            session_terms=svc.get("session_terms"),
            obs=_obs,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        _print_interrupt_banner(
            "Baseline eval",
            completed=f"{pbar.n}/{pbar.total} queries",
            saved="partial results written to disk (auto-resumes on next run)",
            resume_hint="re-run this cell to continue from checkpoint",
        )
        return [], []
    finally:
        pbar.close()

    display_progress(campaign_rounds)

    failures = [r for r in baseline_results if not r["hit"] and not r.get("error")]
    for r in failures[:5]:
        print(
            f"  MISS: {r['query'][:55]}  |  "
            f"Pred: {r['predicted'][:35]}  |  GT: {r['ground_truth'][:35]}"
        )

    return campaign_rounds, baseline_results


def _print_eval_summary(store: ProjectStore, backend_id: str) -> None:
    """Print a summary table of completed and in-progress eval runs."""
    completed = store.dataset_runs.list_all(backend_id)
    partials = store.dataset_runs.list_partial_evals(backend_id)

    completed_ids = {r["run_id"] for r in completed}
    partials = [p for p in partials if p["run_id"] not in completed_ids]

    if not completed and not partials:
        print("Eval runs: none")
        return

    n_completed = len(completed)
    n_partial = len(partials)
    parts = []
    if n_completed:
        parts.append(f"{n_completed} completed run{'s' if n_completed != 1 else ''}")
    if n_partial:
        parts.append(f"{n_partial} in-progress")
    print(f"Eval runs: {', '.join(parts)}")

    rows = []
    for r in completed:
        scores = r.get("scores", {})
        accuracy = scores.get("accuracy")
        acc_str = f"{accuracy:.1%}" if accuracy is not None else "--"
        model_str = r.get("model", "")
        if len(model_str) > 25:
            model_str = model_str[:22] + "..."
        rows.append({
            "run_id": r["run_id"],
            "name": r.get("name", ""),
            "model": model_str,
            "temp": r.get("temperature", ""),
            "accuracy": acc_str,
            "queries": str(r.get("item_count", "?")),
        })
    for p in partials:
        rows.append({
            "run_id": p["run_id"],
            "name": "(in-progress)",
            "model": "",
            "temp": "--",
            "accuracy": "--",
            "queries": f"{p['items']}/?",
        })

    if not rows:
        return

    vary_cols = []
    for col in ["model", "temp"]:
        vals = {r[col] for r in rows}
        if len(vals) > 1:
            vary_cols.append(col)

    header_cols = ["run_id", "name"] + vary_cols + ["accuracy", "queries"]
    col_labels = {
        "run_id": "run_id", "name": "name", "model": "model",
        "temp": "temp", "accuracy": "accuracy", "queries": "queries",
    }
    widths = {}
    for col in header_cols:
        widths[col] = max(
            len(col_labels[col]),
            max((len(str(r[col])) for r in rows), default=0),
        )

    header = "  " + "  ".join(col_labels[c].ljust(widths[c]) for c in header_cols)
    print(header)
    for r in rows:
        line = "  " + "  ".join(str(r[c]).ljust(widths[c]) for c in header_cols)
        print(line)


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    sample_size: int = 0,
) -> list:
    """Load per-query evaluation data from synced experiments or stored replays."""
    eval_data = _load_eval_dataset(store, backend_id, experiment_id, sample_size)

    if eval_data:
        print(f"Loaded {len(eval_data)} eval queries")
    else:
        print(
            "No eval data found. Re-sync with include_traces=true or run a "
            "replay first."
        )

    _print_eval_summary(store, backend_id)

    return eval_data
