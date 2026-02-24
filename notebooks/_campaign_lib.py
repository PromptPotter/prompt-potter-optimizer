"""Helper library for optimization_campaign.ipynb and termnorm_backend.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

import json
import logging
import sys
from pathlib import Path

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
    backend_reranker_eval as _backend_reranker_eval,
    evaluate_prompt_batch,
    compute_accuracy,
    eval_content_hash,
    build_dataset_run_data,
    make_incremental_writer,
)
from api.services.prompt_optimizer import (
    generate_candidates as _generate_candidates,
    select_round_winner as _select_round_winner,
    generate_suggestions as _generate_suggestions,
    save_campaign_winner as _save_campaign_winner,
)
from api.config.settings import load_variant_library
from api.services.grid_search import (
    DEFAULT_GRID_AXES,
    SAMPLING_ALPHA,
    GRID_SEARCHABLE_FIELDS,
    REQUIRED_TEMPLATE_VARS,
    MIN_DIAGNOSTIC_QUERIES,
    DEFAULT_DIAGNOSTIC_QUERIES,
    validate_grid_config as _validate_grid_config,
    build_grid_points as _build_grid_points,
    restructure_context as _restructure_context,
    run_grid_search as _run_grid_search,
    analyze_grid_results as _analyze_grid_results,
    build_grid_analysis_prompt as _build_grid_analysis_prompt,
    select_grid_winner as _select_grid_winner,
    load_eval_dataset as _load_eval_dataset,
    grid_plan_identity as _grid_plan_identity,
    serialize_grid_plan as _serialize_grid_plan,
    deserialize_grid_plan as _deserialize_grid_plan,
    resolve_point_evals as _resolve_point_evals,
    build_diagnostic_set as _build_diagnostic_set,
    sensitivity_scan as _sensitivity_scan,
    adaptive_search as _adaptive_search,
    smart_search_plan_identity as _smart_search_plan_identity,
    serialize_smart_search_plan as _serialize_smart_search_plan,
    deserialize_smart_search_plan as _deserialize_smart_search_plan,
    build_prompt_result_index as _build_prompt_result_index,
    synthesize_sensitivity_from_grid as _synthesize_sensitivity_from_grid,
    assess_scan_coverage as _assess_scan_coverage,
    build_data_inventory as _build_data_inventory,
)

# Re-export constants for notebooks
__all__ = [
    "DEFAULT_GRID_AXES",
    "SAMPLING_ALPHA",
    "GRID_SEARCHABLE_FIELDS",
    "REQUIRED_TEMPLATE_VARS",
    "MIN_DIAGNOSTIC_QUERIES",
    "DEFAULT_DIAGNOSTIC_QUERIES",
    "load_variant_library",
]


# ---------------------------------------------------------------------------
# Pipeline config
# ---------------------------------------------------------------------------

# Maps each pipeline step name to the set of parameter names it uses.
PIPELINE_STEP_PARAMS = {
    "web_search": {"max_sites", "num_results", "content_char_limit"},
    "entity_profiling": {"raw_content_limit", "profiling_temperature", "profiling_max_tokens"},
    "token_matching": {"max_token_candidates", "relevance_weight_core"},
    "llm_ranking": {
        "ranking_temperature", "ranking_max_tokens",
        "ranking_sample_size", "ranking_prompt",
    },
}


def load_pipeline_config(exp_data: dict) -> dict:
    """Extract pipeline config (steps + params) from synced experiment data."""
    runs = exp_data.get("runs", [])
    if not runs:
        return {"steps": [], "notation": "unknown", "name": "", "version": ""}
    pipeline = runs[0].get("pipeline", {})
    config = pipeline.get("config", {})
    return {
        "steps": config.get("steps", []),
        "notation": pipeline.get("notation", ""),
        "name": config.get("name", ""),
        "version": config.get("version", ""),
    }


def build_pipeline_params(
    pipeline_config: dict, overrides: dict | None = None,
) -> dict:
    """Build pipeline_params from a (possibly shortened) pipeline config.

    Returns dict ready for evaluate_prompt(..., pipeline_params=params).
    Includes 'steps' list (sent to TermNorm) and any user overrides.
    """
    step_names = [s["name"] for s in pipeline_config["steps"]]
    params: dict = {"steps": step_names}

    active_param_names: set = set()
    for name in step_names:
        active_param_names |= PIPELINE_STEP_PARAMS.get(name, set())

    if overrides:
        for k, v in overrides.items():
            if k in active_param_names:
                params[k] = v

    return params


def show_pipeline_config(svc: dict, campaign_config: dict) -> dict:
    """Import pipeline config from backend, apply overrides, print summary.

    Replaces the Pipeline Config notebook cell. Sets
    ``campaign_config["pipeline_params"]`` as a side-effect.

    Returns:
        pipeline_params dict ready for evaluate_prompt().
    """
    pipeline_config = load_pipeline_config(svc["exp_data"])

    print(f"Pipeline: {pipeline_config['name']} ({pipeline_config['version']})")
    print(f"Notation: {pipeline_config['notation']}")
    print(f"Steps ({len(pipeline_config['steps'])}):")
    for i, step in enumerate(pipeline_config["steps"]):
        print(f"  {i + 1}. {step['name']} ({step['type']})")

    print(f"\nActive steps: {' -> '.join(s['name'] for s in pipeline_config['steps'])}")

    pipeline_params = build_pipeline_params(
        pipeline_config,
        overrides=campaign_config.get("pipeline_overrides"),
    )
    campaign_config["pipeline_params"] = pipeline_params
    print(f"Pipeline params: {pipeline_params}")

    # Show tunable parameters per active step with variant library search ranges
    variant_lib = load_variant_library()
    pp_search = variant_lib.get("pipeline_params", {})
    step_names = [s["name"] for s in pipeline_config["steps"]]
    print("\nTunable parameters:")
    for step_name in step_names:
        params = sorted(PIPELINE_STEP_PARAMS.get(step_name, set()))
        if not params:
            continue
        print(f"  {step_name}:")
        for param in params:
            search_vals = pp_search.get(param)
            if search_vals:
                print(f"    {param:<34s} search: {search_vals}")
            elif param == "ranking_prompt":
                print(f"    {param:<34s} (overridden by optimizer)")
            else:
                print(f"    {param:<34s} (no search values defined)")

    return pipeline_params


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

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(
        exp_data
        and exp_data.get("runs")
        and exp_data["runs"][0].get("traces")
    )

    # Auto-sync when experiment data is missing or lacks traces
    if not exp_data or not _has_traces:
        reason = "No stored experiment data" if not exp_data else "Stored data has no traces"
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
        "backend_client": client,
        "session_terms": BackendClient.extract_session_terms(exp_data) if exp_data else [],
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
) -> tuple[Execution, list]:
    """Run replay or load from stored results. Returns (Execution, replay_results)."""
    import uuid

    rc = replay_config
    pp = pipeline_params

    variant_label = "full-pipeline" if not rc["skip_llm_ranking"] else "no-llm2"
    pipeline_notation = (
        "LLM1-TokenMatch-LLM2" if not rc["skip_llm_ranking"] else "LLM1-TokenMatch"
    )

    replay_queries_list = queries[:rc["query_limit"]] if rc["query_limit"] else queries
    total = len(replay_queries_list)

    # Check for existing execution
    _existing = None
    if not pp:
        for _ex in store.list_executions(backend_id):
            if (
                _ex["experiment_id"] == experiment_id
                and _ex["variant_label"] == variant_label
                and _ex["pipeline_notation"] == pipeline_notation
            ):
                _existing = store.load_execution(backend_id, _ex["execution_id"])
                if _existing:
                    break

    if _existing:
        execution = _existing
        replay_results = [r.model_dump() for r in _existing.results]
        _hits = sum(
            1 for r in replay_results if r.get("predicted") == r["ground_truth"]
        )
        print(f"Using stored execution {execution.execution_id}")
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


async def evaluate_prompt(
    prompt_state: PromptState,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
    label: str = "Eval",
    verbose: bool = True,
    *,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    force: bool = False,
    backend_client=None,
    pipeline_params: "dict | None" = None,
) -> list:
    """Evaluate a prompt on all eval_data via the backend with progress bar.

    Args:
        store: If provided, enables deduplication via ProjectStore dataset_runs.
            Also enables incremental writes (.partial.jsonl) for crash
            protection and partial-run resume.
        backend_id: Required when store is provided.
        force: Skip dedup lookup and re-evaluate (overwrites existing run).
        backend_client: BackendClient for evaluation via /matches endpoint.
            Required — raises ValueError if not provided.
        pipeline_params: Optional pipeline parameter overrides.
    """
    if backend_client is None:
        raise ValueError(
            "backend_client is required. Start the TermNorm backend and pass "
            "svc.get('backend_client') from init_services()."
        )

    rendered = prompt_state.render()
    model = eval_llm.get("model", "")
    temperature = eval_llm.get("temperature", 0.1)
    content_hash = eval_content_hash(rendered, eval_data, model, temperature)

    # --- dedup lookup ---
    if store and backend_id and not force:
        existing = store.load_dataset_run_by_hash(backend_id, content_hash)
        if existing:
            results = existing["dataset_run_items"]
            acc = existing.get("scores", {})
            hits = acc.get("hits", sum(1 for r in results if r.get("hit")))
            total = acc.get("total", len(results))
            accuracy = acc.get("accuracy", hits / total if total else 0)
            print(
                f"[stored] {label}: {hits}/{total} ({accuracy:.1%})"
                f"  |  Errors: {acc.get('errors', 0)}"
            )
            return results

    # --- compute run_id for incremental writes ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    # --- check for partial results (resume after crash) ---
    partial_results = []
    remaining_data = eval_data
    if store and backend_id:
        partial_results = store.load_partial_eval(backend_id, run_id)
        if partial_results and len(partial_results) < len(eval_data):
            skip = len(partial_results)
            print(f"[resume] Found {skip}/{len(eval_data)} partial results, resuming...")
            remaining_data = eval_data[skip:]
        elif len(partial_results) >= len(eval_data):
            # Partial file is complete — skip to finalize
            remaining_data = []
        else:
            partial_results = []

    # --- evaluate via backend ---
    _pbar = tqdm(total=len(eval_data), desc=f"{label} eval", unit="query",
                 initial=len(partial_results))

    # Build incremental writer callback
    _incremental_writer = None
    if store and backend_id:
        _incremental_writer = make_incremental_writer(store, backend_id, run_id)

    def on_result(result, index, total):
        if _incremental_writer is not None:
            _incremental_writer(result, index, total)
        if verbose:
            tag = "HIT " if result["hit"] else "MISS"
            done = len(partial_results) + index + 1
            tqdm.write(
                f"[{done}/{len(eval_data)}] {tag}  {result['query'][:50]:<50s} "
                f"| pred: {result['predicted'][:35]:<35s}"
            )
        _pbar.update(1)

    new_results = await evaluate_prompt_batch(
        prompt_state, remaining_data, backend_client,
        pipeline_params=pipeline_params,
        on_result=on_result,
    )
    _pbar.close()

    results = partial_results + new_results
    acc = compute_accuracy(results)
    print(
        f"\n{label}: {acc['hits']}/{acc['total']} ({acc['accuracy']:.1%})"
        f"  |  Errors: {acc['errors']}"
    )

    # --- finalize: save complete run, delete .partial.jsonl ---
    if store and backend_id:
        run_data = build_dataset_run_data(
            run_id, label, content_hash, prompt_state.id,
            rendered, model, temperature, acc, results,
        )
        store.finalize_eval_run(backend_id, run_id, run_data)
        print(f"[stored] Saved {run_id}")

    return results


async def run_baseline_eval(
    baseline: PromptState,
    eval_data: list,
    campaign_config: dict,
    api_key: str,
    svc: dict,
) -> tuple:
    """Evaluate baseline prompt and initialize campaign_rounds.

    Replaces the evaluate-baseline notebook cell.

    Returns:
        (campaign_rounds, baseline_results) where campaign_rounds is a
        list with a single baseline entry.
    """
    if not eval_data:
        eval_data = load_eval_dataset(
            svc["store"], svc["backend_id"], svc["experiment_id"],
        )
        if not eval_data:
            raise RuntimeError(
                "No evaluation data in project store. "
                "Generate data first (e.g. run termnorm_backend.ipynb)."
            )

    baseline_results = await evaluate_prompt(
        baseline, eval_data, campaign_config["eval_llm"], api_key,
        label="Baseline",
        store=svc["store"], backend_id=svc["backend_id"],
        backend_client=svc.get("backend_client"),
        pipeline_params=campaign_config.get("pipeline_params"),
    )
    baseline_hits = sum(1 for r in baseline_results if r["hit"])
    baseline_accuracy = baseline_hits / len(baseline_results) if baseline_results else 0

    campaign_rounds = [{
        "round": 0, "label": "baseline", "prompt_state": baseline,
        "accuracy": baseline_accuracy, "hits": baseline_hits,
        "total": len(baseline_results), "results": baseline_results,
    }]

    display_progress(campaign_rounds)

    failures = [r for r in baseline_results if not r["hit"] and not r["error"]]
    for r in failures[:5]:
        print(
            f"  MISS: {r['query'][:55]}  |  "
            f"Pred: {r['predicted'][:35]}  |  GT: {r['ground_truth'][:35]}"
        )

    return campaign_rounds, baseline_results


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
# Grid Plan Discovery
# ---------------------------------------------------------------------------


def list_grid_plans(store: ProjectStore, backend_id: str) -> list:
    """List all grid search plans with their status.

    Shows plan metadata only (no stored result count — that requires knowing the
    eval sampling params which this function doesn't have).
    """
    plans = store.list_grid_plans(backend_id)
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
    eval_queries_per_point: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
) -> "pd.DataFrame | None":
    """Load stored eval results for a grid plan and return a results DataFrame.

    Rebuilds the same DataFrame shape that ``run_grid_search`` returns,
    but purely from stored data (no backend calls).  Returns None if
    the plan doesn't exist or has no stored results.

    Must receive the same eval sampling params as the grid search that
    produced the runs, so that content hashes match.
    """
    plan_data = store.load_grid_plan(backend_id, plan_id)
    if not plan_data:
        return None

    from api.services.grid_search import deserialize_grid_plan as _deser
    grid_points, state_lookup, _, grid_axes, _, _ = _deser(plan_data)

    eval_plan = _resolve_point_evals(
        grid_points, state_lookup, eval_data,
        eval_queries_per_point, shared_queries, seed,
    )
    rows = []
    for info in eval_plan:
        existing = store.load_dataset_run_by_hash(backend_id, info.content_hash)
        if not existing:
            continue
        scores = existing.get("scores", {})
        row = dict(info.coord_dict) if isinstance(info.coord_dict, dict) else {}
        row.update({
            "prompt_state_id": info.ps_id,
            "accuracy": scores.get("accuracy", 0),
            "hits": scores.get("hits", 0),
            "total": scores.get("total", 0),
            "errors": scores.get("errors", 0),
        })
        rows.append(row)

    if not rows:
        return None

    df = pd.DataFrame(rows).sort_values("accuracy", ascending=False).reset_index(drop=True)
    print(f"  {plan_id}: {len(rows)}/{len(grid_points)} stored")
    return df


def merge_grid_results(*dataframes: "pd.DataFrame") -> "pd.DataFrame":
    """Merge multiple grid result DataFrames, keeping the best accuracy per prompt_state_id."""
    combined = pd.concat(dataframes, ignore_index=True)
    combined = (
        combined.sort_values("accuracy", ascending=False)
        .drop_duplicates(subset=["prompt_state_id"], keep="first")
        .sort_values("accuracy", ascending=False)
        .reset_index(drop=True)
    )
    print(f"Merged: {len(combined)} unique grid points from {len(dataframes)} plans")
    return combined


def show_grid_overview(
    svc: dict,
    campaign_config: dict,
    merge_plans: bool = False,
) -> dict:
    """List grid plans and load cached results for each.

    Replaces the Grid Campaign Overview notebook cell.

    Returns:
        Dict with keys: plans, plan_dfs, merged_grid_df (or None).
    """
    store = svc["store"]
    backend_id = svc["backend_id"]

    plans = list_grid_plans(store, backend_id)

    gs = campaign_config["grid_search"]
    eval_queries_per_point = gs.get("eval_queries_per_point", 0)
    shared_queries_flag = gs.get("shared_queries", True)
    seed = gs.get("seed", 42)

    plan_dfs: dict = {}
    if plans:
        eval_data = load_eval_dataset(
            store, backend_id, svc["experiment_id"],
        )
        for p in plans:
            df = load_grid_plan_results(
                store, backend_id, p["plan_id"], eval_data,
                eval_queries_per_point=eval_queries_per_point,
                shared_queries=shared_queries_flag,
                seed=seed,
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


# ---------------------------------------------------------------------------
# Grid Search  (thin wrappers adding tqdm/print/display)
# ---------------------------------------------------------------------------


async def restructure_context(
    context_input,
    eval_llm: dict,
    api_key: str,
    improvement_areas: str = "",
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields."""
    client = _make_llm_client(eval_llm, api_key)
    result = await _restructure_context(
        context_input, client,
        model=eval_llm.get("model"),
        improvement_areas=improvement_areas,
    )

    mode = "validate" if isinstance(context_input, dict) else "parse"
    print(f"Context restructured ({mode} mode):")
    for k, v in result.items():
        if k in GRID_SEARCHABLE_FIELDS and v:
            print(f"  {k}: {v[:80]}{'...' if len(v) > 80 else ''}")

    if result.get("consultation"):
        print(f"\nConsultation:\n  {result['consultation']}")

    return result


def validate_grid_config(
    grid_config: dict, baseline: PromptState, grid_budget: int = 0,
) -> dict:
    """Validate grid axes and compute cartesian product size."""
    meta = _validate_grid_config(grid_config, baseline, grid_budget=grid_budget)

    print("Grid config validated:")
    for name, values in meta["axes"].items():
        print(f"  {name}: {len(values)} variants")
    print(f"  Total grid points: {meta['total']}")

    return meta


def build_grid_points(
    grid_config: dict,
    baseline: PromptState,
    grid_budget: int = 0,
    exploration_rate: float = 0.5,
    seed: int = 42,
):
    """Build cartesian product of grid axes as PromptState variants.

    Returns (grid_points, state_lookup) — the sampling_meta is printed
    and not forwarded to keep the notebook interface simple.
    """
    points, lookup, meta = _build_grid_points(
        grid_config, baseline, grid_budget, exploration_rate, seed,
    )

    # Print sampling summary
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


async def resume_or_build_grid(
    campaign_config: dict,
    baseline: PromptState,
    eval_llm: dict,
    api_key: str,
    store: ProjectStore,
    backend_id: str,
    improvement_areas: str = "",
) -> tuple:
    """Resume an existing grid plan or build a new one.

    Computes a stable plan_id from user-controlled config inputs. If a
    matching plan exists on disk and is not completed, it is deserialized
    and returned (skipping the LLM restructure call). Otherwise, a new
    plan is built, serialized, and saved.

    Returns:
        (plan_id, grid_points, grid_state_lookup, grid_axes,
         layer1_fields, grid_baseline)
    """
    gs = campaign_config["grid_search"]
    grid_budget = gs.get("grid_budget", 0)
    exploration_rate = campaign_config.get("exploration_rate", 0.5)
    seed = gs.get("seed", 42)
    context_input = gs.get("context_fields", gs.get("context", ""))

    # Build grid axes
    if gs.get("use_defaults", True):
        grid_axes = dict(DEFAULT_GRID_AXES)
    else:
        grid_axes = {}
    if gs.get("custom_axes"):
        grid_axes.update(gs["custom_axes"])

    plan_id = _grid_plan_identity(
        grid_axes, baseline.instruction, context_input,
        grid_budget, exploration_rate, seed,
    )

    # Check for existing plan
    existing = store.load_grid_plan(backend_id, plan_id)
    if existing:
        print(f"[RESUME] Found existing grid plan: {plan_id} (status: {existing.get('status', '?')})")
        (
            grid_points, grid_state_lookup, sampling_meta,
            grid_axes, layer1_fields, grid_baseline,
        ) = _deserialize_grid_plan(existing)
        print(
            f"  Grid points: {len(grid_points)}  |  "
            f"Status: {existing.get('status', '?')}"
        )
        return (
            plan_id, grid_points, grid_state_lookup,
            grid_axes, layer1_fields, grid_baseline,
        )

    # Build new plan: LLM restructure + grid points
    print(f"[NEW] Building grid plan: {plan_id}")
    layer1_fields = await restructure_context(
        context_input, eval_llm, api_key,
        improvement_areas=improvement_areas,
    )

    grid_baseline = baseline.derive(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="grid_baseline",
    )

    _validate_grid_config(grid_axes, grid_baseline, grid_budget=grid_budget)
    points, state_lookup, sampling_meta = _build_grid_points(
        grid_axes, grid_baseline,
        grid_budget=grid_budget,
        exploration_rate=exploration_rate,
        seed=seed,
    )

    # Print sampling summary
    if sampling_meta["is_uncapped"]:
        print(
            f"Built {sampling_meta['n_selected']} grid points "
            f"(requested {grid_budget}, capped at full space of "
            f"{sampling_meta['total_space']})"
        )
    elif grid_budget > 0:
        print(
            f"Sampled {sampling_meta['n_selected']}/"
            f"{sampling_meta['total_space']} grid points "
            f"(exploration_rate={sampling_meta['exploration_rate']:.2f})"
        )
    else:
        print(f"Built {sampling_meta['n_selected']} grid points (full grid)")

    if sampling_meta["distance_distribution"]:
        parts = [
            f"d{d}={c}"
            for d, c in sorted(sampling_meta["distance_distribution"].items())
        ]
        print(f"  Distance distribution: {', '.join(parts)}")

    # Persist
    plan_data = _serialize_grid_plan(
        plan_id, grid_axes, grid_baseline, layer1_fields,
        points, state_lookup, sampling_meta,
    )
    store.save_grid_plan(backend_id, plan_id, plan_data)
    print(f"  Saved grid plan to disk: {plan_id}")

    return (
        plan_id, points, state_lookup,
        grid_axes, layer1_fields, grid_baseline,
    )


async def run_grid_search(
    grid_points: list,
    state_lookup: dict,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
    *,
    plan_id: str = "",
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    backend_client=None,
    session_terms: "list | None" = None,
    pipeline_params: "dict | None" = None,
    eval_queries_per_point: int = 0,
    shared_queries: bool = True,
    grid_seed: int = 42,
) -> pd.DataFrame:
    """Evaluate each grid point on eval_data via the backend."""
    if backend_client is None:
        raise ValueError(
            "backend_client is required. Start the TermNorm backend and pass "
            "svc.get('backend_client') from init_services()."
        )

    eval_plan = _resolve_point_evals(
        grid_points, state_lookup, eval_data,
        eval_queries_per_point, shared_queries, grid_seed,
    )
    n_stored = 0
    if store and backend_id:
        for info in eval_plan:
            if store.load_dataset_run_by_hash(backend_id, info.content_hash):
                n_stored += 1

    n_total = len(grid_points)
    n_remaining = n_total - n_stored
    q_label = (
        f"{eval_queries_per_point} quer{'y' if eval_queries_per_point == 1 else 'ies'}"
        if eval_queries_per_point > 0
        else f"{len(eval_data)} queries"
    )
    if n_stored > 0:
        print(
            f"[resume] Skipping {n_stored}/{n_total} stored grid points, "
            f"evaluating {n_remaining} remaining"
        )
    else:
        print(f"Evaluating {n_total} grid points x {q_label} each")

    _point_counter = [0]  # mutable counter for closures

    def on_query_done(point_idx, qi, total_q, result):
        hit = result.get("hit")
        err = result.get("error")
        if err:
            print(
                f"    [{qi + 1}/{total_q}] ERROR  {result['query'][:50]:<50s} "
                f"| {err}"
            )
        else:
            tag = "HIT " if hit else "MISS"
            print(
                f"    [{qi + 1}/{total_q}] {tag}  {result['query'][:50]:<50s} "
                f"| pred: {result['predicted'][:35]:<35s} "
                f"| gt: {result['ground_truth'][:35]}"
            )

    def on_point_done(idx, row):
        _point_counter[0] += 1
        print(
            f"  [{_point_counter[0]}/{n_total}] "
            f"acc={row['accuracy']:.1%} ({row['hits']}/{row['total']})"
        )

    def on_point_reused(idx, row):
        pass  # already counted in n_stored summary

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
        eval_queries_per_point=eval_queries_per_point,
        shared_queries=shared_queries,
        seed=grid_seed,
    )

    # Mark plan as completed
    if plan_id and store and backend_id:
        try:
            store.update_grid_plan_status(backend_id, plan_id, "completed")
            print(f"Grid plan {plan_id} marked as completed.")
        except Exception:
            pass  # Non-critical; plan may not exist if run without resume_or_build_grid

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


def select_grid_winner(grid_df: pd.DataFrame, state_lookup: dict) -> dict:
    """Select the best-performing grid point."""
    result = _select_grid_winner(grid_df, state_lookup)

    ps = result["prompt_state"]
    print(f"Grid winner: {ps.changes_description or ps.id[:12]}")
    print(f"  Accuracy: {result['accuracy']:.1%} ({result['hits']}/{result['total']})")
    print(f"  PromptState: {ps.id[:12]}")

    return result


def select_and_seed_grid_winner(
    grid_df: "pd.DataFrame | None",
    merged_grid_df: "pd.DataFrame | None",
    grid_state_lookup: dict,
    plan_dfs: dict,
    svc: dict,
    campaign_rounds: list,
) -> dict:
    """Select grid winner, build combined lookup if needed, seed campaign.

    Replaces the 4.5f notebook cell. Appends the winner to
    ``campaign_rounds`` as a side-effect.

    Returns:
        The grid_winner dict.
    """
    winner_df = merged_grid_df if merged_grid_df is not None else grid_df

    # Build combined state_lookup when using merged results
    if merged_grid_df is not None and plan_dfs:
        combined_lookup: dict = {}
        for pid in plan_dfs:
            plan_data = svc["store"].load_grid_plan(svc["backend_id"], pid)
            if plan_data:
                _, sl, _, _, _, _ = _deserialize_grid_plan(plan_data)
                combined_lookup.update(sl)
        grid_winner = select_grid_winner(winner_df, combined_lookup)
    else:
        grid_winner = select_grid_winner(winner_df, grid_state_lookup)

    campaign_rounds.append(grid_winner)

    # Print Layer 1 breakdown
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
    eval_llm: dict,
    api_key: str,
) -> dict:
    """LLM analysis of grid search results."""
    if not api_key:
        raise RuntimeError(
            "No API key provided. Set GROQ_API_KEY in your .env file "
            "and restart the kernel."
        )

    # Build and display the prompt before calling the LLM
    prompt = _build_grid_analysis_prompt(grid_df, grid_config)
    print("LLM ANALYSIS PROMPT")
    print("=" * 70)
    print(prompt)
    print("=" * 70)

    model = eval_llm.get("model", "?")
    print(f"\nCalling {model} ...")

    client = _make_llm_client(eval_llm, api_key)
    analysis = await _analyze_grid_results(
        grid_df, grid_config, client, model=eval_llm.get("model"),
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


def print_eval_summary(store: ProjectStore, backend_id: str) -> None:
    """Print a summary table of completed and in-progress eval runs."""
    completed = store.list_dataset_runs(backend_id)
    partials = store.list_partial_evals(backend_id)

    # Exclude partials whose run_id matches a completed run (already finalized)
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

    # Build rows
    rows = []
    for r in completed:
        scores = r.get("scores", {})
        accuracy = scores.get("accuracy")
        acc_str = f"{accuracy:.1%}" if accuracy is not None else "—"
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
            "temp": "—",
            "accuracy": "—",
            "queries": f"{p['items']}/?",
        })

    if not rows:
        return

    # Determine which columns vary (only show columns that differ across rows)
    vary_cols = []
    for col in ["model", "temp"]:
        vals = {r[col] for r in rows}
        if len(vals) > 1:
            vary_cols.append(col)

    # Always-shown columns
    header_cols = ["run_id", "name"] + vary_cols + ["accuracy", "queries"]

    # Column widths
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

    # Print header
    header = "  " + "  ".join(col_labels[c].ljust(widths[c]) for c in header_cols)
    print(header)

    # Print rows
    for r in rows:
        line = "  " + "  ".join(str(r[c]).ljust(widths[c]) for c in header_cols)
        print(line)


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    query_limit: int = 0,
) -> list:
    """Load per-query evaluation data from synced experiments or stored replays."""
    eval_data = _load_eval_dataset(store, backend_id, experiment_id, query_limit)

    if eval_data:
        print(f"Loaded {len(eval_data)} eval queries")
    else:
        print(
            "No eval data found. Re-sync with include_traces=true or run a "
            "replay first."
        )

    print_eval_summary(store, backend_id)

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


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------


def display_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round.

    Shows: round-by-round accuracy, rolling average over last ``window``
    rounds, trend indicator (improving/plateau/declining).
    """
    if not campaign_rounds:
        print("No rounds to display.")
        return

    print(f"\n{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}")
    accuracies = []

    for rd in campaign_rounds:
        acc = rd["accuracy"]
        accuracies.append(acc)
        n = len(accuracies)

        # Rolling average over last `window` entries
        window_slice = accuracies[-window:]
        rolling_avg = sum(window_slice) / len(window_slice)

        # Trend
        if n <= 1:
            trend_str = "-"
        else:
            delta = acc - accuracies[-2]
            if abs(delta) < 0.001:
                trend_str = "+0.0%  <-- plateau"
            elif delta > 0:
                trend_str = f"+{delta:.1%}"
            else:
                trend_str = f"{delta:.1%}"

        round_label = str(rd["round"])
        if rd.get("round") == "grid":
            round_label = "G"

        print(
            f"  {round_label:<5s} {acc:>8.1%} {rolling_avg:>12.1%}  {trend_str}"
        )


# ---------------------------------------------------------------------------
# Smart Prompt Search wrappers
# ---------------------------------------------------------------------------


def build_diagnostic_set(
    eval_data: list,
    baseline_results: list,
    n_queries: int = DEFAULT_DIAGNOSTIC_QUERIES,
    seed: int = 42,
) -> tuple[list, dict]:
    """Build a stratified diagnostic query set and print summary."""
    diagnostic, summary = _build_diagnostic_set(
        eval_data, baseline_results, n_queries=n_queries, seed=seed,
    )

    print("Diagnostic set built:")
    print(
        f"  Queries: {summary['n_queries']} "
        f"({summary['n_hits']} hits + {summary['n_misses']} misses)"
    )
    print(
        f"  Pool: {summary['total_pool_hits']} hits, "
        f"{summary['total_pool_misses']} misses"
    )

    return diagnostic, summary


async def resume_or_build_diagnostic(
    campaign_config: dict,
    baseline: PromptState,
    baseline_results: list,
    eval_llm: dict,
    api_key: str,
    store: "ProjectStore",
    backend_id: str,
    eval_data: list,
    improvement_areas: str = "",
) -> tuple[str, PromptState, list, dict, list]:
    """Resume or build smart search diagnostic set.

    Returns:
        (plan_id, search_baseline, diagnostic, diag_summary, axis_profiles_or_empty)

    If a plan already exists on disk, skips LLM restructure and diagnostic
    building. If the plan status is ``scan_complete`` or later, also returns
    cached axis profiles.
    """
    import hashlib as _hashlib

    ss = campaign_config.get("smart_search", {})
    variant_library = load_variant_library()

    plan_id = _smart_search_plan_identity(
        baseline.instruction,
        variant_library,
        ss,
        improvement_areas,
        seed=ss.get("seed", 42),
    )

    existing = store.load_smart_search_plan(backend_id, plan_id)
    if existing:
        status = existing.get("status", "?")
        plan = _deserialize_smart_search_plan(existing)

        if plan["scan_results"] and status in ("scan_complete", "search_complete"):
            cached_profiles = plan["scan_results"].get("axis_profiles", [])
            print(f"[RESUME] Scan complete in plan {plan_id}, reusing profiles")
            return (
                plan_id,
                plan["search_baseline_ps"],
                plan["diagnostic"],
                plan["diag_summary"],
                cached_profiles,
            )
        # diagnostic_built → reuse saved baseline; prefer sibling with scan data
        siblings = [
            s for s in store.list_smart_search_plans(backend_id)
            if s["plan_id"] != plan_id
            and s["status"] in ("scan_complete", "search_complete")
            and s.get("variant_library_hash") == existing.get("variant_library_hash", "")
            and s.get("n_axis_profiles", 0) > 0
        ]
        if siblings:
            # Prefer matching n_diagnostic, then scan_complete status
            current_n_diag = plan.get("config", {}).get("n_diagnostic", 6)
            siblings.sort(key=lambda s: (
                s.get("n_diagnostic") != current_n_diag,
                s["status"] != "scan_complete",
            ))
            sib_data = store.load_smart_search_plan(backend_id, siblings[0]["plan_id"])
            sib_plan = _deserialize_smart_search_plan(sib_data)
            sib_profiles = (sib_plan.get("scan_results") or {}).get("axis_profiles", [])
            print(f"[RESUME] Adopting scan data from plan {siblings[0]['plan_id']} "
                  f"({len(sib_profiles)} axis profiles)")
            return (
                plan_id,
                sib_plan["search_baseline_ps"],
                sib_plan["diagnostic"],
                sib_plan["diag_summary"],
                sib_profiles,
            )

        # No sibling with data — return saved baseline (no LLM rebuild)
        print(f"[RESUME] Plan {plan_id} (status: {status}), reusing saved diagnostic")
        return (
            plan_id,
            plan["search_baseline_ps"],
            plan["diagnostic"],
            plan["diag_summary"],
            [],
        )

    # Build new plan: LLM restructure + diagnostic set
    print(f"[NEW] Building smart search plan: {plan_id}")
    layer1_fields = await restructure_context(
        baseline.instruction, eval_llm, api_key,
        improvement_areas=improvement_areas,
    )
    search_baseline = baseline.derive(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="search_baseline (decomposed)",
    )
    print(f"\n-> search_baseline: {search_baseline.id[:12]} "
          f"(render: {len(search_baseline.render())} chars)")

    diagnostic, diag_summary = build_diagnostic_set(
        eval_data, baseline_results,
        n_queries=ss.get("n_diagnostic", 6),
    )

    # Compute a short hash of the full variant library for traceability
    vl_json = json.dumps(variant_library, sort_keys=True)
    vl_hash = _hashlib.sha256(vl_json.encode()).hexdigest()[:12]

    config = {
        "n_diagnostic": ss.get("n_diagnostic", 6),
        "max_rounds": ss.get("max_rounds", 3),
        "stop_threshold": ss.get("stop_threshold", 0.0),
    }
    plan_data = _serialize_smart_search_plan(
        plan_id, config, baseline, search_baseline,
        layer1_fields, diagnostic, diag_summary, vl_hash,
    )
    store.save_smart_search_plan(backend_id, plan_id, plan_data)
    print(f"  Saved smart search plan to disk: {plan_id}")

    return plan_id, search_baseline, diagnostic, diag_summary, []


def list_smart_search_plans(store: "ProjectStore", backend_id: str) -> list[dict]:
    """List all smart search plans on disk (for notebook inspection)."""
    return store.list_smart_search_plans(backend_id)


def build_historical_index(store: "ProjectStore", backend_id: str) -> dict:
    """Build prompt result index from all historical dataset runs.

    Returns the index dict: ``rendered_prompt_hash -> {query -> result}``.
    """
    index = _build_prompt_result_index(store, backend_id)
    n_prompts = len(index)
    n_results = sum(len(v) for v in index.values())
    print(f"Historical index: {n_prompts} unique prompts, "
          f"{n_results} total query results cached")
    return index


def show_scan_coverage(
    baseline_ps,
    variant_library: dict,
    diagnostic: list,
    prompt_index: dict,
    pipeline_params: dict | None = None,
    min_queries: int = 6,
    axis_requirements: dict[str, int] | None = None,
) -> dict:
    """Print a formatted coverage report and return the raw dict.

    Calls ``assess_scan_coverage()`` from ``grid_search`` and displays
    a human-readable table showing which axes are covered by cached data.
    """
    result = _assess_scan_coverage(
        baseline_ps, variant_library, diagnostic, prompt_index,
        pipeline_params=pipeline_params,
        min_queries=min_queries,
        axis_requirements=axis_requirements,
    )

    bl = result["baseline_coverage"]
    axes = result["axes"]
    summary = result["summary"]

    print("=" * 70)
    print(f"  COVERAGE ADVISOR  (min_queries={result['min_queries']})")
    print("=" * 70)
    print(f"  Baseline: {bl['n_cached']}/{bl['n_needed']} queries cached"
          f" {'✓' if bl['sufficient'] else '✗'}")
    print()

    pf_axes = [a for a in axes if a["axis_type"] == "prompt_field"]
    pp_axes = [a for a in axes if a["axis_type"] == "pipeline_param"]

    if pf_axes:
        print("  Prompt field axes:")
        for a in pf_axes:
            label = "value" if a["n_values"] == 1 else "values"
            usable_parts = []
            if a.get("variants"):
                n_partial = sum(
                    1 for v in a["variants"]
                    if 0 < v["n_cached"] < min_queries
                )
                usable_parts.append(f"{a['n_usable']} usable")
                if n_partial:
                    usable_parts.append(f"{n_partial} partial")
                n_uncovered = sum(
                    1 for v in a["variants"] if v["n_cached"] == 0
                )
                if n_uncovered:
                    usable_parts.append(f"{n_uncovered} uncovered")
            detail = f"  ({', '.join(usable_parts)})" if usable_parts else ""
            mark = "✓" if a["sufficient"] else "✗"
            print(f"    {a['axis']:<22s} {a['n_values']} {label:<6s} "
                  f"| {a['n_usable']}/{a['n_required']} required  "
                  f"{mark}{detail}")
        print()

    if pp_axes:
        print("  Pipeline params (always need backend):")
        n_diag = result["n_diagnostic"]
        for a in pp_axes:
            n_calls = a["n_values"] * n_diag
            print(f"    {a['axis']:<22s} {a['n_values']} variants "
                  f"× {n_diag} queries = {n_calls} calls")
        print()

    saved = summary["backend_calls_saved"]
    needed = summary["backend_calls_needed"]
    pf_needed = summary.get("prompt_field_calls_needed", 0)
    pp_needed = summary.get("pipeline_param_calls_needed", 0)
    parts = []
    if pf_needed:
        parts.append(f"{pf_needed} prompt-field")
    if pp_needed:
        parts.append(f"{pp_needed} pipeline-param")
    breakdown = f" ({' + '.join(parts)})" if parts else ""
    print(f"  Summary: {saved} cached, {needed} still needed{breakdown}")
    print(f"  >> {result['recommendation']}")

    if (summary["backend_calls_saved"] == 0
            and sum(len(v) for v in prompt_index.values()) > 0):
        n_total = sum(len(v) for v in prompt_index.values())
        print(f"\n  Note: {n_total} results exist in the index under other baselines.")
        print("  The current search_baseline was rebuilt and has no cached data yet.")

    print("=" * 70)

    return result


def show_data_inventory(
    prompt_index: dict,
    store: "ProjectStore",
    backend_id: str,
) -> dict:
    """Print a formatted data inventory table and return the raw dict.

    Calls ``build_data_inventory()`` from ``grid_search`` and displays
    a per-axis breakdown of what the historical index contains.
    """
    inv = _build_data_inventory(prompt_index, store, backend_id)

    tp = inv["total_prompts"]
    tr = inv["total_results"]
    print("=" * 70)
    print(f"  DATA INVENTORY  ({tp} prompts, {tr} query results)")
    print("=" * 70)

    bp = inv["baseline_prompts"]
    bq = inv["baseline_queries"]
    print(f"  Baselines: {bp} plan baseline(s) — {bq} queries cached")
    print()

    axes = inv["axes"]
    if axes:
        print(f"  {'Axis':<22s} {'Prompts':>8s} {'Queries':>8s} {'Distinct values':>16s}")
        for axis_name, info in axes.items():
            print(f"  {axis_name:<22s} {info['n_prompts']:>8d} "
                  f"{info['n_queries']:>8d} {info['distinct_values']:>16d}")
        print()
    else:
        print("  No axis variations found in stored plans.")
        print()

    pp = inv.get("pipeline_params", {})
    if pp:
        print("  Pipeline parameters (from sensitivity scans):")
        for pname, info in pp.items():
            card = info["cardinality"]
            sens = info["sensitivity_range"]
            budget = info["exploration_budget"]
            print(f"    {pname:<24s} {card} values scanned  "
                  f"sensitivity: {sens:.3f}  [{budget}]")
        print()
    else:
        print("  Pipeline parameters: not yet scanned")
        print()

    mp = inv["matched_prompts"]
    mr = inv["matched_results"]
    up = inv["unmatched_prompts"]
    ur = inv["unmatched_results"]
    print(f"  Identified: {mp}/{tp} prompts ({mr}/{tr} queries) via stored plans")
    print(f"  Unmatched:  {up} prompts ({ur} queries)")
    print("=" * 70)

    return inv


def synthesize_sensitivity(
    store: "ProjectStore",
    backend_id: str,
    prompt_index: dict,
    diagnostic: list,
) -> tuple | None:
    """Try to derive sensitivity profiles from grid search data.

    Returns ``(df, profiles)`` or ``None`` if insufficient coverage.
    """
    result = _synthesize_sensitivity_from_grid(
        store, backend_id, prompt_index, diagnostic,
    )
    if result is None:
        print("Grid synthesis: insufficient coverage for sensitivity derivation")
        return None
    df, profiles = result
    n_active = sum(1 for p in profiles if p["exploration_budget"] != "skip")
    print(f"Grid synthesis: {len(profiles)} axes profiled, "
          f"{n_active} active (non-skip)")
    return result


async def sensitivity_scan(
    baseline_ps,
    variant_library: dict,
    eval_data: list,
    backend_client,
    user_focus: str = "",
    store=None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    request_delay: float = 1.0,
    session_terms: list | None = None,
    plan_id: str = "",
    prompt_result_index: dict | None = None,
) -> tuple:
    """Run a sensitivity scan over all axes with progress output.

    Returns (per_variant_df, axis_profiles).
    """
    print("Running sensitivity scan...")
    if user_focus:
        print(f"  User focus: {user_focus}")

    # Show baseline field values (initial conditions for each axis)
    print("\n  Baseline field values:")
    for field in ("persona", "task_intent", "problem_description",
                  "instruction", "thinking_style", "answer_format"):
        val = getattr(baseline_ps, field, "")
        if val:
            print(f"    {field}: {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"    {field}: (empty)")
    print()

    n_configs = sum(
        len(v)
        for v in variant_library.get("prompt_fields", {}).values()
        if len(v) > 1
    ) + sum(
        len(v)
        for v in variant_library.get("pipeline_params", {}).values()
        if len(v) > 1
    )
    print(f"  Estimated configs: ~{n_configs} x {len(eval_data)} queries")

    cb = _make_scan_progress_cb()

    # Suppress httpx noise during scan (our own logger still shows per-query progress)
    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)
    try:
        print("  Evaluating baseline...")
        df, profiles = await _sensitivity_scan(
            baseline_ps, variant_library, eval_data, backend_client,
            user_focus=user_focus,
            store=store, backend_id=backend_id,
            pipeline_params=pipeline_params,
            request_delay=request_delay,
            session_terms=session_terms,
            progress_cb=cb,
            prompt_result_index=prompt_result_index,
        )
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    print(f"\nSensitivity scan complete: {len(df)} variants evaluated")
    display_axis_profiles(profiles)

    # Persist scan results
    if store and backend_id and plan_id:
        store.update_smart_search_plan(backend_id, plan_id, {
            "status": "scan_complete",
            "scan_results": {
                "rows": df.to_dict(orient="records"),
                "axis_profiles": profiles,
            },
        })
        print(f"  Saved scan results to plan: {plan_id}")

    return df, profiles


def _make_scan_progress_cb():
    """Build a progress callback for sensitivity_scan with flip tracking."""
    baseline_results: list = []

    def _cb(event: dict) -> None:
        t = event["type"]

        if t == "baseline_done":
            baseline_results.clear()
            baseline_results.extend(event.get("results", []))
            cached = " [cached]" if event.get("cached") else ""
            print(f"  Baseline: {event['hits']}/{event['total']} "
                  f"({event['accuracy']:.1%}){cached}")

        elif t == "axis_start":
            ai = event["axis_index"] + 1
            total = event["total_axes"]
            card = event["cardinality"]
            print(f"\n{'=' * 70}")
            print(f"  Axis {ai}/{total}: {event['axis']} "
                  f"({event['axis_type']}, {card} values)")
            print(f"{'=' * 70}")

        elif t == "variant_done":
            vi = event["value_idx"]
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            is_bl = event["is_baseline_value"]
            cached = event.get("cached", False)

            if is_bl:
                delta_str = "(baseline)"
                marker = ""
            elif delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(f"  [{vi}] {preview:<42s} {hits}/{total}  "
                  f"{acc:.1%}  {delta_str}{marker}{cache_str}")

            # Show flips vs baseline (GAINED / LOST queries)
            results = event.get("results", [])
            if results and baseline_results and not is_bl:
                for br, vr in zip(baseline_results, results):
                    b_hit = br.get("hit", False)
                    v_hit = vr.get("hit", False)
                    if b_hit != v_hit:
                        q = (vr.get("query") or "")[:40]
                        pred = (vr.get("predicted") or "")[:35]
                        if v_hit:
                            print(f"        GAINED  {q:<40s}  -> {pred}")
                        else:
                            print(f"        LOST    {q:<40s}  -> {pred}")

        elif t == "axis_done":
            budget = event["exploration_budget"]
            sr = event["sensitivity_range"]
            bd = event["best_delta"]
            wd = event["worst_delta"]
            print(f"  >> {event['axis']}: range={sr:.1%}, "
                  f"best={bd:+.1%}, worst={wd:+.1%}, budget={budget}")

    return _cb


async def adaptive_search(
    baseline_ps,
    variant_library: dict,
    eval_data: list,
    backend_client,
    axis_profiles: list[dict],
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    store=None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    request_delay: float = 1.0,
    session_terms: list | None = None,
    plan_id: str = "",
    prompt_result_index: dict | None = None,
) -> tuple:
    """Run adaptive coordinate descent search with progress output.

    Returns (best_ps, best_pipeline_params, search_log_df).
    """
    active = [p for p in axis_profiles if p["exploration_budget"] != "skip"]
    print(f"Adaptive search: {len(active)} active axes, max {max_rounds} rounds")
    for p in active:
        print(
            f"  {p['axis']} ({p['axis_type']}): "
            f"card={p['cardinality']}, budget={p['exploration_budget']}"
        )

    cb = _make_search_progress_cb()

    # Suppress httpx noise during search
    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)
    try:
        best_ps, best_params, log_df = await _adaptive_search(
            baseline_ps, variant_library, eval_data, backend_client,
            axis_profiles,
            max_rounds=max_rounds,
            stop_threshold=stop_threshold,
            store=store, backend_id=backend_id,
            pipeline_params=pipeline_params,
            request_delay=request_delay,
            session_terms=session_terms,
            progress_cb=cb,
            prompt_result_index=prompt_result_index,
        )
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    # Print search path summary
    if not log_df.empty:
        print(f"\nSearch log: {len(log_df)} evaluations across "
              f"{log_df['round'].nunique()} rounds")
        best_row = log_df.loc[log_df["accuracy"].idxmax()]
        print(
            f"  Best found: {best_row['axis']}={best_row['value_preview']} "
            f"({best_row['accuracy']:.1%})"
        )
    else:
        print("\nNo evaluations performed (all axes skipped).")

    # Persist search results
    if store and backend_id and plan_id:
        store.update_smart_search_plan(backend_id, plan_id, {
            "status": "search_complete",
            "search_results": {
                "best_ps": best_ps.model_dump(),
                "best_params": best_params,
                "log_rows": log_df.to_dict(orient="records")
                if not log_df.empty else [],
            },
        })
        print(f"  Saved search results to plan: {plan_id}")

    return best_ps, best_params, log_df


def _make_search_progress_cb():
    """Build a progress callback for adaptive_search."""

    def _cb(event: dict) -> None:
        t = event["type"]

        if t == "round_start":
            r = event["round"]
            max_r = event["max_rounds"]
            acc = event["current_accuracy"]
            axes = event["active_axes"]
            print(f"\n{'=' * 70}")
            print(f"  Round {r}/{max_r} | current accuracy: {acc:.1%}")
            print(f"  Active axes: {', '.join(axes)}")
            print(f"{'=' * 70}")

        elif t == "axis_start":
            axis = event["axis"]
            card = event["cardinality"]
            budget = event["budget"]
            print(f"\n  -- {axis} ({event['axis_type']}, "
                  f"{card} values, budget={budget}) --")

        elif t == "variant_done":
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            cached = event.get("cached", False)

            if delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(f"    {preview:<42s} {hits}/{total}  "
                  f"{acc:.1%}  {delta_str}{marker}{cache_str}")

            # Show per-query results (diagnostic set is small)
            results = event.get("results", [])
            for r in results:
                hit_str = "HIT " if r.get("hit") else "MISS"
                q = (r.get("query") or "")[:35]
                pred = (r.get("predicted") or "")[:30]
                print(f"      {hit_str}  {q:<35s}  -> {pred}")

        elif t == "axis_resolved":
            action = event["action"]
            axis = event["axis"]
            if action == "improved":
                imp = event["improvement"]
                bv = event["best_value"]
                new_acc = event["new_accuracy"]
                print(f"  ** {axis} IMPROVED +{imp:.1%} -> "
                      f"{new_acc:.1%} (best: {bv})")
            else:
                print(f"  -- {axis}: no improvement, resolved")

        elif t == "round_done":
            r = event["round"]
            acc = event["accuracy"]
            if event["improved"]:
                print(f"\n  Round {r} done: accuracy now {acc:.1%}")
            else:
                print(f"\n  Round {r}: no improvement, stopping.")

    return _cb


def display_axis_profiles(profiles: list[dict]) -> None:
    """Display axis profiles as a formatted table."""
    if not profiles:
        print("No axis profiles to display.")
        return

    print(f"\n{'Rank':<5s} {'Axis':<25s} {'Type':<15s} "
          f"{'Card':<5s} {'Range':<8s} {'Budget':<8s}")
    print("-" * 70)
    for rank, p in enumerate(profiles, 1):
        print(
            f"  {rank:<3d} {p['axis']:<25s} {p['axis_type']:<15s} "
            f"{p['cardinality']:<5d} {p['sensitivity_range']:<8.3f} "
            f"{p['exploration_budget']:<8s}"
        )


async def run_manual_round(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    api_key: str,
    svc: dict,
) -> dict:
    """Run a single manual optimization round.

    Replaces the optimization-round notebook cell.

    Returns:
        The round entry dict (also appended to campaign_rounds).
    """
    import random as _random

    opt = campaign_config["optimization"]
    eval_llm = campaign_config["eval_llm"]
    current_best = campaign_rounds[-1]
    queries_per_eval = campaign_config.get("queries_per_eval", 0)
    round_num = len(campaign_rounds)

    print(
        f"=== ROUND {round_num} === Current best: "
        f"{current_best['label']} ({current_best['accuracy']:.1%})\n"
    )

    if queries_per_eval > 0 and len(eval_data) > queries_per_eval:
        rng = _random.Random(42)
        round_eval_data = rng.sample(eval_data, queries_per_eval)
        print(f"Subsampled eval_data to {queries_per_eval}/{len(eval_data)} queries")
    else:
        round_eval_data = eval_data

    candidates = await generate_candidates(
        current_best["prompt_state"], current_best["accuracy"],
        current_best["results"],
        opt["n_variants"], opt["creativity"], eval_llm, api_key,
    )

    all_candidate_results = {}
    for idx, c in enumerate(candidates):
        all_candidate_results[c.id] = await evaluate_prompt(
            c, round_eval_data, eval_llm, api_key,
            label=f"Candidate {idx + 1}",
            store=svc["store"], backend_id=svc["backend_id"],
            backend_client=svc.get("backend_client"),
            pipeline_params=campaign_config.get("pipeline_params"),
        )

    round_entry = select_round_winner(
        candidates, all_candidate_results, current_best,
        opt["improvement_threshold"],
    )
    round_entry["round"] = round_num
    campaign_rounds.append(round_entry)

    display_progress(campaign_rounds)

    return round_entry


# ---------------------------------------------------------------------------
# Semi-automatic optimization loop
# ---------------------------------------------------------------------------


async def run_optimization_loop(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    api_key: str,
    *,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    max_rounds: int = 10,
    patience: int = 3,
    backend_client=None,
    pipeline_params: "dict | None" = None,
) -> list:
    """Run optimization rounds until patience exhausted or max_rounds reached.

    After each round:
    - Display progress (accuracy + rolling avg)
    - If improvement > threshold: auto-continue
    - If no improvement for ``patience`` rounds: stop and print summary

    Args:
        campaign_rounds: Existing rounds list (modified in place and returned).
        eval_data: Full evaluation dataset.
        campaign_config: Campaign configuration dict (must have ``eval_llm``,
            ``optimization``, and optionally ``queries_per_eval``).
        api_key: LLM API key.
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier (required when store is provided).
        max_rounds: Hard cap on optimization rounds.
        patience: Rounds without improvement before auto-stop.
        backend_client: BackendClient for evaluation via /matches endpoint.
        pipeline_params: Optional pipeline parameter overrides.

    Returns:
        Updated campaign_rounds list.
    """
    import random as _random

    opt = campaign_config.get("optimization", {})
    eval_llm = campaign_config["eval_llm"]
    queries_per_eval = campaign_config.get("queries_per_eval", 0)
    n_variants = opt.get("n_variants", 5)
    creativity = opt.get("creativity", 0.7)
    threshold = opt.get("improvement_threshold", 0.01)
    patience = opt.get("patience", patience)

    # Subsample eval_data if queries_per_eval is set
    if queries_per_eval > 0 and len(eval_data) > queries_per_eval:
        rng = _random.Random(42)
        round_eval_data = rng.sample(eval_data, queries_per_eval)
        print(f"Subsampled eval_data to {queries_per_eval}/{len(eval_data)} queries")
    else:
        round_eval_data = eval_data

    rounds_without_improvement = 0

    for _ in range(max_rounds):
        current_best = campaign_rounds[-1]
        round_num = len(campaign_rounds)
        print(
            f"\n{'=' * 70}\n"
            f"=== ROUND {round_num} === Current best: "
            f"{current_best['label']} ({current_best['accuracy']:.1%})\n"
            f"{'=' * 70}"
        )

        candidates = await generate_candidates(
            current_best["prompt_state"],
            current_best["accuracy"],
            current_best["results"],
            n_variants,
            creativity,
            eval_llm,
            api_key,
        )

        all_candidate_results = {}
        for idx, c in enumerate(candidates):
            all_candidate_results[c.id] = await evaluate_prompt(
                c, round_eval_data, eval_llm, api_key,
                label=f"Candidate {idx + 1}",
                store=store,
                backend_id=backend_id,
                backend_client=backend_client,
                pipeline_params=pipeline_params,
            )

        round_entry = select_round_winner(
            candidates, all_candidate_results, current_best, threshold,
        )
        round_entry["round"] = round_num
        campaign_rounds.append(round_entry)

        # Display progress
        display_progress(campaign_rounds)

        # Check improvement
        improved = round_entry["accuracy"] > current_best["accuracy"] + threshold
        if improved:
            rounds_without_improvement = 0
            print(f"\nImprovement detected, auto-continuing...")
        else:
            rounds_without_improvement += 1
            print(
                f"\nNo improvement ({rounds_without_improvement}/{patience} patience)"
            )
            if rounds_without_improvement >= patience:
                print(
                    f"\nStopping: no improvement for {patience} consecutive rounds."
                )
                break

    # Final summary
    best = max(campaign_rounds, key=lambda r: r["accuracy"])
    print(f"\n{'=' * 70}")
    print("OPTIMIZATION COMPLETE")
    print(f"  Rounds run: {len(campaign_rounds) - 1}")
    print(f"  Best accuracy: {best['accuracy']:.1%} (round {best['round']})")
    print(f"{'=' * 70}")

    return campaign_rounds
