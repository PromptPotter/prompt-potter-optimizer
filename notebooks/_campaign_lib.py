"""Helper library for optimization_campaign.ipynb and termnorm_backend.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.

All existing function signatures are preserved for backward compatibility.
"""

import sys
from pathlib import Path
from typing import Tuple

import pandas as pd
from tqdm.auto import tqdm

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.models.backend import BackendConnection, Execution, ExecutionResultItem
from api.models.prompt_state import PromptState
from api.services.backend_client import BackendClient
from api.services.llm_client import LLMClientBase, GroqClient, OpenAIClient
from api.services.project_store import ProjectStore

# --- Service imports (core logic) ---
from api.services.prompt_eval import (
    extract_baseline_prompt,
    filter_eval_data as _filter_eval_data,
    local_reranker_eval as _local_reranker_eval,
    evaluate_prompt_batch,
    compute_accuracy,
)
from api.services.prompt_optimizer import (
    generate_candidates as _generate_candidates,
    select_round_winner as _select_round_winner,
    generate_suggestions as _generate_suggestions,
    save_campaign_winner as _save_campaign_winner,
)
from api.services.grid_search import (
    DEFAULT_GRID_AXES,
    GRID_SEARCHABLE_FIELDS,
    REQUIRED_TEMPLATE_VARS,
    validate_grid_config as _validate_grid_config,
    build_grid_combinations as _build_grid_combinations,
    restructure_context as _restructure_context,
    run_grid_search as _run_grid_search,
    analyze_grid_results as _analyze_grid_results,
    select_grid_winner as _select_grid_winner,
    load_eval_dataset as _load_eval_dataset,
)

# Re-export constants for notebooks
__all__ = [
    "DEFAULT_GRID_AXES",
    "GRID_SEARCHABLE_FIELDS",
    "REQUIRED_TEMPLATE_VARS",
]


# ---------------------------------------------------------------------------
# LLM client adapter
# ---------------------------------------------------------------------------


def _make_llm_client(eval_llm: dict, api_key: str) -> LLMClientBase:
    """Bridge notebook's eval_llm dict config to an LLMClientBase.

    Args:
        eval_llm: Dict with model, provider_url, temperature, max_tokens.
        api_key: API key for the LLM provider.

    Returns:
        LLMClientBase instance.
    """
    provider_url = eval_llm.get("provider_url", "")
    if "groq.com" in provider_url:
        return GroqClient(api_key=api_key)
    elif "openai.com" in provider_url:
        return OpenAIClient(api_key=api_key)
    else:
        # Default: assume OpenAI-compatible endpoint via Groq SDK
        return GroqClient(api_key=api_key)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
) -> dict:
    """Initialize store, client, and load experiment data.

    If experiment data is not in the project store, attempts an automatic
    sync from the backend.  Connection errors are caught so the notebook
    can still start (the user gets a clear message instead of a crash).

    Returns dict with keys: store, client, queries, terms, exp_data,
    backend_id, experiment_id.
    """
    project_root = Path(__file__).resolve().parent.parent
    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")
    client = BackendClient(backend_url)

    if not store.get_backend(backend_id):
        store.register_backend(BackendConnection(
            id=backend_id, name="TermNorm Local",
            backend_type="termnorm", base_url=backend_url,
        ))

    exp_data = store.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale cache: data exists but has no traces
    _has_traces = bool(
        exp_data
        and exp_data.get("runs")
        and exp_data["runs"][0].get("traces")
    )

    # Auto-sync when experiment data is missing or lacks traces
    if not exp_data or not _has_traces:
        reason = "No cached experiment data" if not exp_data else "Cached data has no traces"
        print(f"{reason} — syncing from {backend_url} ...")
        try:
            await client.sync_experiments(store, backend_id, include_traces=True)
            exp_data = store.load_sync(
                backend_id, f"experiments/{experiment_id}.json",
            )
        except Exception as exc:
            print(f"Auto-sync failed ({exc}). Start the backend or sync manually.")

    if not exp_data:
        print(
            "WARNING: No experiment data available. "
            "Downstream cells will fail until data is synced."
        )
        return {
            "store": store,
            "client": client,
            "queries": [],
            "terms": [],
            "exp_data": {},
            "backend_id": backend_id,
            "experiment_id": experiment_id,
        }

    mappings = exp_data.get("mappings", [])
    queries = client.extract_replay_queries(exp_data)
    terms = client.extract_session_terms(exp_data)
    verified = sum(
        1 for m in mappings
        if m.get("dataset_entry", "").strip() not in ("", "--")
    )

    print(f"Experiment : {exp_data.get('experiment', {}).get('name', experiment_id)}")
    print(f"Mappings   : {len(mappings)} total, {verified} with verified ground truth")
    print(f"Queries    : {len(queries)}  |  Session terms: {len(terms)}")

    return {
        "store": store,
        "client": client,
        "queries": queries,
        "terms": terms,
        "exp_data": exp_data,
        "backend_id": backend_id,
        "experiment_id": experiment_id,
    }


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


async def run_or_load_replay(
    client: BackendClient,
    store: ProjectStore,
    queries: list,
    terms: list,
    backend_id: str,
    experiment_id: str,
    replay_config: dict,
    pipeline_params: dict,
) -> Tuple[Execution, list]:
    """Run replay or load from cache. Returns (Execution, replay_results)."""
    import uuid

    rc = replay_config
    pp = pipeline_params

    variant_label = "full-pipeline" if not rc["skip_llm_ranking"] else "no-llm2"
    pipeline_notation = (
        "LLM1-TokenMatch-LLM2" if not rc["skip_llm_ranking"] else "LLM1-TokenMatch"
    )

    replay_queries_list = queries[:rc["query_limit"]] if rc["query_limit"] else queries
    total = len(replay_queries_list)

    # Check cache
    _cached = None
    if not pp:
        for _ex in store.list_executions(backend_id):
            if (
                _ex["experiment_id"] == experiment_id
                and _ex["variant_label"] == variant_label
                and _ex["pipeline_notation"] == pipeline_notation
            ):
                _cached = store.load_execution(backend_id, _ex["execution_id"])
                if _cached:
                    break

    if _cached:
        execution = _cached
        replay_results = [r.model_dump() for r in _cached.results]
        _hits = sum(
            1 for r in replay_results if r.get("predicted") == r["ground_truth"]
        )
        print(f"Using cached execution {execution.execution_id}")
        print(f"  Queries: {len(replay_results)}")
        print(
            f"  hit@1: {_hits}/{len(replay_results)} "
            f"({_hits / len(replay_results) * 100:.1f}%)"
        )
    else:
        execution_id = uuid.uuid4().hex[:12]
        if pp:
            print(f"Pipeline overrides: {pp}")
        print(f"Replaying {total} queries against {client.base_url}...")

        _hits_counter = [0]
        _pbar = tqdm(total=total, desc="Replay", unit="query")

        async def on_result(result, index, total):
            store.append_result(backend_id, execution_id, result)
            hit = result.get("predicted", "") == result["ground_truth"]
            if hit:
                _hits_counter[0] += 1
            done = index + 1
            tag = "HIT " if hit else "MISS"
            tqdm.write(
                f"[{done}/{total}] {tag}  {result['query'][:50]:<50s} "
                f"| pred: {result.get('predicted', '?')[:35]:<35s} "
                f"| Running: {_hits_counter[0]}/{done} "
                f"({_hits_counter[0] / done * 100:.1f}%)"
            )
            _pbar.update(1)

        replay_results = await client.replay_queries(
            queries=replay_queries_list,
            terms=terms,
            skip_llm_ranking=rc["skip_llm_ranking"],
            delay_between=rc["delay_between"],
            on_result=on_result,
            pipeline_params=pp,
        )
        _pbar.close()

        successful = sum(1 for r in replay_results if r["status"] == "success")
        errors = sum(1 for r in replay_results if r["status"] == "error")
        execution = Execution(
            execution_id=execution_id,
            backend_id=backend_id,
            experiment_id=experiment_id,
            variant_label=variant_label,
            pipeline_notation=pipeline_notation,
            session_terms_count=len(terms),
            pipeline_params=pp,
            query_count=len(replay_results),
            successful_count=successful,
            error_count=errors,
            results=[ExecutionResultItem(**r) for r in replay_results],
        )
        store.finalize_execution(execution)
        replay_results = [
            r if isinstance(r, dict) else r.model_dump() for r in replay_results
        ]

    # Summary
    total_r = len(replay_results)
    hits = sum(1 for r in replay_results if r.get("predicted") == r["ground_truth"])
    avg_lat = (
        sum(r.get("latency_ms", 0) for r in replay_results) / total_r
        if total_r
        else 0
    )
    avg_conf = (
        sum(r.get("confidence", 0) for r in replay_results) / total_r
        if total_r
        else 0
    )

    print("\nReplay Summary")
    print(f"  hit@1:          {hits}/{total_r} ({hits / total_r * 100:.1f}%)")
    print(f"  Avg latency:    {avg_lat:,.0f} ms")
    print(f"  Avg confidence: {avg_conf:.3f}")

    return execution, replay_results


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def analyze_candidate_coverage(replay_results: list) -> pd.DataFrame:
    """Analyze candidate coverage and print diagnostic summary."""
    coverage_rows = []
    for r in replay_results:
        if r.get("status") != "success":
            continue
        pd_data = r.get("pipeline_data", {})
        candidates = pd_data.get("token_matched_candidates", [])
        gt = r["ground_truth"]

        candidate_names = []
        for c in candidates:
            if isinstance(c, (list, tuple)):
                candidate_names.append(c[0])
            else:
                candidate_names.append(str(c))

        gt_rank = None
        for i, name in enumerate(candidate_names):
            if name == gt:
                gt_rank = i + 1
                break

        coverage_rows.append({
            "query": r["query"][:50],
            "ground_truth": gt[:40],
            "in_candidates": gt_rank is not None,
            "gt_rank": gt_rank,
            "num_candidates": len(candidate_names),
        })

    cov_df = pd.DataFrame(coverage_rows)
    covered = cov_df["in_candidates"].sum()
    total_cov = len(cov_df)
    coverage_pct = covered / total_cov * 100 if total_cov else 0

    print("CANDIDATE COVERAGE")
    print("=" * 50)
    print(f"  Ground truth in candidates: {covered}/{total_cov} ({coverage_pct:.1f}%)")
    print(f"  Missing from candidates:    {total_cov - covered}/{total_cov}")
    print()

    found = cov_df[cov_df["in_candidates"]]
    if not found.empty:
        print("Rank distribution (ground truth position in candidate list):")
        print(f"  Rank 1 (already top):  {(found['gt_rank'] == 1).sum()}")
        print(
            f"  Rank 2-5:              "
            f"{((found['gt_rank'] >= 2) & (found['gt_rank'] <= 5)).sum()}"
        )
        print(
            f"  Rank 6-10:             "
            f"{((found['gt_rank'] >= 6) & (found['gt_rank'] <= 10)).sum()}"
        )
        print(
            f"  Rank 11-20:            "
            f"{((found['gt_rank'] >= 11) & (found['gt_rank'] <= 20)).sum()}"
        )
        print(f"  Rank >20:              {(found['gt_rank'] > 20).sum()}")
        print(f"  Mean rank:             {found['gt_rank'].mean():.1f}")
        print(f"  Median rank:           {found['gt_rank'].median():.0f}")

    print()
    if coverage_pct > 50:
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


# ---------------------------------------------------------------------------
# Baseline & Eval  (thin wrappers adding print output)
# ---------------------------------------------------------------------------


def load_baseline_prompt(exp_data: dict) -> PromptState:
    """Extract the llm_ranking prompt from experiment data, wrap in PromptState."""
    baseline = extract_baseline_prompt(exp_data)

    print(f"Baseline prompt loaded: {baseline.id[:12]}")
    print(f"  Family: {baseline.parameters['family']}")
    print(f"  Version: {baseline.parameters['version']}")
    print(f"  Template length: {len(baseline.instruction)} chars")

    return baseline


def filter_eval_data(replay_results: list) -> list:
    """Filter replay results to those with entity_profile in pipeline_data."""
    eval_data = _filter_eval_data(replay_results)

    print(
        f"Evaluation data: {len(eval_data)}/{len(replay_results)} "
        "queries with entity_profile"
    )
    if not eval_data:
        print("WARNING: No queries have entity_profile in pipeline_data.")
        print("Re-run replay with skip_llm_ranking=False.")

    return eval_data


async def local_reranker_eval(
    prompt_template: str,
    query_data: dict,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """Evaluate a reranker prompt on a single query using cached pipeline data."""
    client = _make_llm_client(eval_llm, api_key)
    return await _local_reranker_eval(
        prompt_template, query_data, client,
        model=eval_llm.get("model"),
        temperature=eval_llm.get("temperature", 0.1),
        max_tokens=eval_llm.get("max_tokens", 4096),
    )


async def evaluate_prompt(
    prompt_state: PromptState,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
    label: str = "Eval",
    verbose: bool = True,
) -> list:
    """Evaluate a prompt on all eval_data with progress bar."""
    client = _make_llm_client(eval_llm, api_key)
    _pbar = tqdm(total=len(eval_data), desc=f"{label} eval", unit="query")

    def on_result(result, index, total):
        if verbose:
            tag = "HIT " if result["hit"] else "MISS"
            done = index + 1
            tqdm.write(
                f"[{done}/{total}] {tag}  {result['query'][:50]:<50s} "
                f"| pred: {result['predicted'][:35]:<35s}"
            )
        _pbar.update(1)

    results = await evaluate_prompt_batch(
        prompt_state, eval_data, client,
        model=eval_llm.get("model"),
        temperature=eval_llm.get("temperature", 0.1),
        max_tokens=eval_llm.get("max_tokens", 4096),
        on_result=on_result,
    )
    _pbar.close()

    acc = compute_accuracy(results)
    print(
        f"\n{label}: {acc['hits']}/{acc['total']} ({acc['accuracy']:.1%})"
        f"  |  Errors: {acc['errors']}"
    )

    return results


# ---------------------------------------------------------------------------
# Optimization  (thin wrappers adding print/display)
# ---------------------------------------------------------------------------


async def generate_candidates(
    current_ps: PromptState,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    eval_llm: dict,
    api_key: str,
) -> list:
    """Generate candidate prompt variants via LLM meta-prompt."""
    client = _make_llm_client(eval_llm, api_key)
    print(f"Generating {n_variants} candidate prompts...")

    candidates = await _generate_candidates(
        current_ps, current_accuracy, current_results,
        n_variants, creativity, client,
        model=eval_llm.get("model"),
    )

    for c in candidates:
        print(
            f"  {c.changes_description or c.id[:12]}: "
            f"{(c.changes_description or '')[:80]}"
        )

    return candidates


def select_round_winner(
    candidates: list,
    all_candidate_results: dict,
    current_best: dict,
    improvement_threshold: float,
) -> dict:
    """Compare candidates, print comparison table, return round entry dict."""
    from IPython.display import display as ipy_display

    result = _select_round_winner(
        candidates, all_candidate_results, current_best, improvement_threshold,
    )

    # Display comparison table
    print(f"\n{'=' * 70}")
    print("ROUND SUMMARY")
    print(f"{'=' * 70}")
    ipy_display(pd.DataFrame(result["comparison_rows"]))

    if result["improved"]:
        print(
            f"\nWINNER: {result['label']} ({result['accuracy']:.1%}, "
            f"+{result['accuracy'] - current_best['accuracy']:.1%} over previous)"
        )
        ps = result["prompt_state"]
        print(
            f"  PromptState: {ps.id[:12]}  "
            f"(parent: {ps.parent_id[:12] if ps.parent_id else 'none'})"
        )
    else:
        print(
            f"\nNo improvement beyond threshold ({improvement_threshold:.1%}). "
            "Keeping current best."
        )

    return {
        "label": result["label"],
        "prompt_state": result["prompt_state"],
        "accuracy": result["accuracy"],
        "hits": result["hits"],
        "total": result["total"],
        "results": result["results"],
        "candidates_evaluated": result["candidates_evaluated"],
    }


# ---------------------------------------------------------------------------
# Suggestions  (thin wrapper adding print output)
# ---------------------------------------------------------------------------


async def generate_suggestions(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """Build suggestion prompt, call LLM, return parsed JSON."""
    client = _make_llm_client(eval_llm, api_key)
    print("Generating suggestions...")

    return await _generate_suggestions(
        campaign_rounds, eval_data, campaign_config,
        client, model=eval_llm.get("model"),
    )


def display_suggestions(suggestions: dict, round_num: int) -> None:
    """Pretty-print failure patterns, parameter suggestions, and prompt phrases."""
    print(f"\n{'=' * 70}")
    print(f"LLM SUGGESTIONS FOR ROUND {round_num}")
    print(f"{'=' * 70}")

    print(f"\nSUMMARY: {suggestions.get('summary', '')}")

    print("\n--- FAILURE PATTERNS ---")
    for fp in suggestions.get("failure_patterns", []):
        print(
            f"  [{fp.get('category', '?')}] ~{fp.get('count', '?')} queries: "
            f"{fp.get('description', '')}"
        )
        for ex in fp.get("examples", [])[:2]:
            print(f"    e.g. {ex[:60]}")

    print("\n--- PARAMETER CHANGE SUGGESTIONS ---")
    for ps in suggestions.get("parameter_suggestions", []):
        print(
            f"  {ps.get('parameter', '?')}: "
            f"{ps.get('current_value', '?')} -> {ps.get('suggested_value', '?')}"
        )
        print(f"    Rationale: {ps.get('rationale', '')}")

    print("\n--- PROMPT PHRASE FRAGMENTS ---")
    for pf in suggestions.get("prompt_phrase_fragments", []):
        print(f"  [{pf.get('action', '?')}]")
        print(f"    Text: \"{pf.get('text', '')}\"")
        print(f"    Rationale: {pf.get('rationale', '')}")
        print()


# ---------------------------------------------------------------------------
# Grid Search  (thin wrappers adding tqdm/print/display)
# ---------------------------------------------------------------------------


async def restructure_context(
    context_input,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields."""
    client = _make_llm_client(eval_llm, api_key)
    result = await _restructure_context(context_input, client, model=eval_llm.get("model"))

    mode = "validate" if isinstance(context_input, dict) else "parse"
    print(f"Context restructured ({mode} mode):")
    for k, v in result.items():
        if k in GRID_SEARCHABLE_FIELDS and v:
            print(f"  {k}: {v[:80]}{'...' if len(v) > 80 else ''}")

    return result


def validate_grid_config(grid_config: dict, baseline: PromptState) -> dict:
    """Validate grid axes and compute cartesian product size."""
    meta = _validate_grid_config(grid_config, baseline)

    print("Grid config validated:")
    for name, values in meta["axes"].items():
        print(f"  {name}: {len(values)} variants")
    print(f"  Total combinations: {meta['total']}")

    return meta


def build_grid_combinations(
    grid_config: dict,
    baseline: PromptState,
    max_combinations: int = 0,
    seed: int = 42,
):
    """Build cartesian product of grid axes as PromptState variants."""
    combos, lookup = _build_grid_combinations(
        grid_config, baseline, max_combinations, seed,
    )

    if max_combinations > 0 and len(combos) < max_combinations:
        pass  # no subsampling message needed
    print(f"Built {len(combos)} grid combinations")

    return combos, lookup


async def run_grid_search(
    combinations: list,
    ps_lookup: dict,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
) -> pd.DataFrame:
    """Evaluate each grid combination on eval_data."""
    client = _make_llm_client(eval_llm, api_key)
    pbar = tqdm(total=len(combinations), desc="Grid search", unit="combo")

    def on_combo_done(idx, row):
        pbar.update(1)

    df = await _run_grid_search(
        combinations, ps_lookup, eval_data, client,
        model=eval_llm.get("model"),
        temperature=eval_llm.get("temperature", 0.1),
        max_tokens=eval_llm.get("max_tokens", 4096),
        on_combo_done=on_combo_done,
    )
    pbar.close()
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
    print(f"GRID RESULTS — TOP {top_k}")
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


def select_grid_winner(grid_df: pd.DataFrame, ps_lookup: dict) -> dict:
    """Select the best-performing grid combination."""
    result = _select_grid_winner(grid_df, ps_lookup)

    ps = result["prompt_state"]
    print(f"Grid winner: {ps.changes_description or ps.id[:12]}")
    print(f"  Accuracy: {result['accuracy']:.1%} ({result['hits']}/{result['total']})")
    print(f"  PromptState: {ps.id[:12]}")

    return result


async def analyze_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """LLM analysis of grid search results."""
    client = _make_llm_client(eval_llm, api_key)
    analysis = await _analyze_grid_results(
        grid_df, grid_config, client, model=eval_llm.get("model"),
    )

    print(f"\n{'=' * 70}")
    print("GRID ANALYSIS (LLM)")
    print(f"{'=' * 70}")
    for finding in analysis.get("key_findings", []):
        print(f"  - {finding}")
    print(f"\n  Strongest fields: {analysis.get('strongest_fields', [])}")
    print(f"  Recommended focus: {analysis.get('recommended_focus', '')}")
    print(f"  Advice: {analysis.get('campaign_advice', '')}")

    return analysis


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    query_limit: int = 0,
) -> list:
    """Load per-query evaluation data from synced experiments or replay cache."""
    eval_data = _load_eval_dataset(store, backend_id, experiment_id, query_limit)

    if eval_data:
        print(f"Loaded {len(eval_data)} eval queries")
    else:
        print(
            "No eval data found. Re-sync with include_traces=true or run a "
            "replay first."
        )

    return eval_data


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: ProjectStore,
    backend_id: str,
) -> dict:
    """Find best round, save to store, print confirmation."""
    result = _save_campaign_winner(
        campaign_rounds, campaign_config, store, backend_id,
    )

    print("WINNER SAVED")
    print(f"  PromptState: {result['winner_id'][:12]}")
    print(
        f"  Accuracy: {result['accuracy']:.1%} "
        f"(baseline: {result['baseline_accuracy']:.1%}, "
        f"delta: {result['improvement']:+.1%})"
    )
    print(f"  File: .promptpotter/projects/{backend_id}/sync/{result['filename']}")
    print(f"  Rounds completed: {result['campaign_rounds'] - 1}")

    return result
