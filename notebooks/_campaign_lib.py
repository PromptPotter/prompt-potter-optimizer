"""Helper library for optimization_campaign.ipynb and termnorm_backend.ipynb.

Extracts plumbing (imports, cache logic, async loops, LLM calls)
so notebook cells stay short and dashboard-readable.
"""

import itertools
import json
import os
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd
from tqdm.auto import tqdm

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.models.backend import BackendConnection, Execution, ExecutionResultItem
from api.models.prompt_state import PromptState
from api.services.backend_client import BackendClient
from api.services.project_store import ProjectStore


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def init_services(
    termnorm_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
) -> dict:
    """Initialize store, client, and load experiment data.

    Returns dict with keys: store, client, queries, terms, exp_data.
    """
    project_root = Path(__file__).resolve().parent.parent
    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")
    client = BackendClient(termnorm_url)

    # Register backend (idempotent)
    if not store.get_backend(backend_id):
        store.register_backend(BackendConnection(
            id=backend_id, name="TermNorm Local",
            backend_type="termnorm", base_url=termnorm_url,
        ))

    # Load experiment data
    exp_data = store.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if not exp_data:
        raise RuntimeError(
            "No synced experiment data. "
            "Run: await client.sync_experiments(store, backend_id)"
        )

    queries = client.extract_replay_queries(exp_data)
    terms = client.extract_session_terms(exp_data)

    print(f"Experiment: {exp_data.get('experiment', {}).get('name', experiment_id)}")
    print(f"Queries: {len(queries)}  |  Session terms: {len(terms)}")

    return {
        "store": store,
        "client": client,
        "queries": queries,
        "terms": terms,
        "exp_data": exp_data,
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
    rc = replay_config
    pp = pipeline_params

    variant_label = "full-pipeline" if not rc["skip_llm_ranking"] else "no-llm2"
    pipeline_notation = (
        "LLM1-TokenMatch-LLM2" if not rc["skip_llm_ranking"] else "LLM1-TokenMatch"
    )

    replay_queries_list = queries[:rc["query_limit"]] if rc["query_limit"] else queries
    total = len(replay_queries_list)

    # Check cache (only when no param overrides)
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

        _hits_counter = [0]  # mutable for nested access
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

    print(f"\nReplay Summary")
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
# Baseline & Eval
# ---------------------------------------------------------------------------


def load_baseline_prompt(exp_data: dict) -> PromptState:
    """Extract the llm_ranking prompt from experiment data, wrap in PromptState."""
    dependencies = exp_data.get("dependencies", {})
    prompts = dependencies.get("prompts", {})

    reranker_prompt = None
    for key, prompt_info in prompts.items():
        if "llm_ranking" in key:
            reranker_prompt = prompt_info
            break

    if reranker_prompt is None:
        raise RuntimeError(
            "No llm_ranking prompt found in synced experiment data. "
            "Re-sync the experiment after TermNorm prompt registry is initialized."
        )

    baseline = PromptState(
        instruction=reranker_prompt["template"],
        parameters={
            "family": reranker_prompt.get("family", "llm_ranking"),
            "version": reranker_prompt.get("version"),
            "template_variables": reranker_prompt.get("template_variables", []),
        },
        changes_description="Baseline reranker_v1 from TermNorm prompt registry",
    )

    print(f"Baseline prompt loaded: {baseline.id[:12]}")
    print(f"  Family: {baseline.parameters['family']}")
    print(f"  Version: {baseline.parameters['version']}")
    print(f"  Template length: {len(baseline.instruction)} chars")

    return baseline


def filter_eval_data(replay_results: list) -> list:
    """Filter replay results to those with entity_profile in pipeline_data."""
    eval_data = [
        r
        for r in replay_results
        if r.get("status") == "success"
        and r.get("pipeline_data", {}).get("entity_profile")
    ]

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
    """Evaluate a reranker prompt on a single query using cached pipeline data.

    Args:
        prompt_template: Prompt with {{core_concept}}, {{entity_profile_json}},
            {{matches}} placeholders.
        query_data: Result dict with pipeline_data.entity_profile and
            pipeline_data.token_matched_candidates.
        eval_llm: Dict with model, provider_url, temperature, max_tokens.
        api_key: API key for the LLM provider.

    Returns:
        dict with keys: query, predicted, ground_truth, hit, confidence, error
    """
    pipeline = query_data["pipeline_data"]
    entity_profile = pipeline["entity_profile"]
    candidates = pipeline.get("token_matched_candidates", [])
    ground_truth = query_data["ground_truth"]
    query = query_data["query"]

    core_concept = entity_profile.get("core_concept", "")
    entity_profile_json = json.dumps(entity_profile, indent=2)

    available = list(candidates[:20])
    sample_size = min(len(available), 20)
    sampled = random.sample(available, sample_size) if available else []
    matches = "\n".join(
        f"- {term}" if isinstance(term, str) else f"- {term[0]}"
        for term in sampled
    )

    rendered = prompt_template.replace("{{core_concept}}", str(core_concept))
    rendered = rendered.replace("{{entity_profile_json}}", entity_profile_json)
    rendered = rendered.replace("{{matches}}", matches)

    full_prompt = (
        f"{rendered}\n\n"
        "IMPORTANT: Return a valid JSON response matching this exact structure:\n"
        "{\n"
        '  "profile_summary": "Brief 1-2 sentence summary of the profile",\n'
        '  "core_concept_description": '
        '"What the core concept fundamentally is",\n'
        '  "ranked_candidates": [\n'
        "    {\n"
        '      "candidate": "exact candidate string",\n'
        '      "core_concept_score": 0.0,\n'
        '      "spec_score": 0.0,\n'
        '      "evaluation_reasoning": '
        '"Brief explanation without quotes or backslashes",\n'
        '      "key_match_factors": ["factor1", "factor2"],\n'
        '      "spec_gaps": ["gap1", "gap2"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Ensure all strings are properly escaped and "
        "avoid complex punctuation in reasoning."
    )

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                eval_llm["provider_url"],
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": eval_llm["model"],
                    "messages": [{"role": "user", "content": full_prompt}],
                    "temperature": eval_llm["temperature"],
                    "max_tokens": eval_llm["max_tokens"],
                    "response_format": {"type": "json_object"},
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            llm_output = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(llm_output)

        ranked = parsed.get("ranked_candidates", [])
        top = ranked[0] if ranked else {}
        predicted = top.get("candidate", "NO_RESULT")
        confidence = top.get(
            "relevance_score", top.get("core_concept_score", 0)
        )

        return {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": predicted == ground_truth,
            "confidence": confidence,
            "error": None,
        }
    except Exception as e:
        return {
            "query": query,
            "predicted": "ERROR",
            "ground_truth": ground_truth,
            "hit": False,
            "confidence": 0,
            "error": str(e),
        }


async def evaluate_prompt(
    prompt_state: PromptState,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
    label: str = "Eval",
    verbose: bool = True,
) -> list:
    """Evaluate a prompt on all eval_data with progress bar.

    Returns list of result dicts.
    """
    results = []
    _pbar = tqdm(total=len(eval_data), desc=f"{label} eval", unit="query")

    for qd in eval_data:
        result = await local_reranker_eval(
            prompt_state.render(), qd, eval_llm, api_key
        )
        results.append(result)

        if verbose:
            tag = "HIT " if result["hit"] else "MISS"
            hits_so_far = sum(1 for r in results if r["hit"])
            done = len(results)
            tqdm.write(
                f"[{done}/{len(eval_data)}] {tag}  {result['query'][:50]:<50s} "
                f"| pred: {result['predicted'][:35]:<35s} "
                f"| Running: {hits_so_far}/{done} ({hits_so_far / done * 100:.1f}%)"
            )
        _pbar.update(1)

    _pbar.close()

    hits = sum(1 for r in results if r["hit"])
    errors = sum(1 for r in results if r["error"])
    acc = hits / len(results) if results else 0
    print(f"\n{label}: {hits}/{len(results)} ({acc:.1%})  |  Errors: {errors}")

    return results


# ---------------------------------------------------------------------------
# Optimization
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
    """Generate candidate prompt variants via LLM meta-prompt.

    Returns list of PromptState.
    """
    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    failure_examples = "\n".join(
        f"  Query: {r['query'][:60]}  |  "
        f"Predicted: {r['predicted'][:40]}  |  "
        f"GT: {r['ground_truth'][:40]}"
        for r in failures[:15]
    )

    rendered_prompt = current_ps.render()

    meta_prompt = (
        f"You are a prompt engineering expert. Generate {n_variants} improved "
        "variants\nof a candidate-ranking prompt used in a terminology "
        "normalization pipeline.\n\n"
        f"CURRENT PROMPT ({current_accuracy:.1%} accuracy on "
        f"{len(current_results)} queries):\n"
        f"---\n{rendered_prompt}\n---\n\n"
        f"FAILURE EXAMPLES (predicted != ground_truth):\n{failure_examples}\n\n"
        "The prompt uses template variables (double-brace syntax):\n"
        "  {{core_concept}} -- core concept from entity profile\n"
        "  {{entity_profile_json}} -- full JSON entity profile from web "
        "research\n"
        "  {{matches}} -- newline-separated list of \"- candidate_term\" "
        "from token matching\n\n"
        "For each variant:\n"
        "1. Analyze WHY the current prompt fails on the examples above\n"
        "2. Make targeted changes to improve ranking accuracy "
        "(get correct candidate to rank #1)\n"
        "3. Keep the same template variables and JSON output format\n\n"
        "Return a JSON object with key \"variants\" containing an array of "
        "objects:\n"
        "  - \"variant_name\": short identifier\n"
        "  - \"changes_description\": 1-2 sentence description of what "
        "changed and why\n"
        "  - \"prompt_text\": full prompt template text"
    )

    print(f"Generating {n_variants} candidate prompts...")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            eval_llm["provider_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": eval_llm["model"],
                "messages": [{"role": "user", "content": meta_prompt}],
                "temperature": creativity,
                "max_tokens": 16000,
                "response_format": {"type": "json_object"},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        generated = json.loads(raw)

    if isinstance(generated, dict):
        variants_list = generated.get("variants", generated.get("prompts", []))
    else:
        variants_list = generated

    candidates = []
    for v in variants_list[:n_variants]:
        ps = current_ps.derive(
            instruction=v["prompt_text"],
            changes_description=v.get(
                "changes_description", v.get("variant_name", "")
            ),
        )
        candidates.append(ps)
        print(
            f"  {v.get('variant_name', ps.id[:12])}: "
            f"{v.get('changes_description', '')[:80]}"
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

    current_acc = current_best["accuracy"]
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]

    best_acc = current_acc
    best_ps = current_ps
    best_results = current_results
    best_label = current_best["label"]

    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_acc = (
            sum(1 for r in c_results if r["hit"]) / len(c_results)
            if c_results
            else 0
        )
        if c_acc > best_acc:
            best_acc = c_acc
            best_ps = candidate
            best_results = c_results
            best_label = candidate.changes_description or candidate.id[:12]

    # Comparison table
    rows = [
        {
            "prompt": f"current_best ({current_best['label'][:30]})",
            "hit@1": f"{current_acc:.1%}",
            "delta": "-",
        }
    ]
    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_acc = (
            sum(1 for r in c_results if r["hit"]) / len(c_results)
            if c_results
            else 0
        )
        delta = c_acc - current_acc
        rows.append({
            "prompt": (candidate.changes_description or candidate.id[:12])[:40],
            "hit@1": f"{c_acc:.1%}",
            "delta": f"{delta:+.1%}",
        })

    print(f"\n{'=' * 70}")
    print("ROUND SUMMARY")
    print(f"{'=' * 70}")
    ipy_display(pd.DataFrame(rows))

    improved = best_acc > current_acc + improvement_threshold
    if improved:
        print(
            f"\nWINNER: {best_label} ({best_acc:.1%}, "
            f"+{best_acc - current_acc:.1%} over previous)"
        )
        print(
            f"  PromptState: {best_ps.id[:12]}  "
            f"(parent: {best_ps.parent_id[:12] if best_ps.parent_id else 'none'})"
        )
    else:
        print(
            f"\nNo improvement beyond threshold ({improvement_threshold:.1%}). "
            "Keeping current best."
        )

    return {
        "label": best_label,
        "prompt_state": best_ps,
        "accuracy": best_acc,
        "hits": sum(1 for r in best_results if r["hit"]),
        "total": len(best_results),
        "results": best_results,
        "candidates_evaluated": len(candidates),
    }


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


async def generate_suggestions(
    campaign_rounds: list,
    replay_results: list,
    campaign_config: dict,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """Build suggestion prompt, call LLM, return parsed JSON."""
    current_best = campaign_rounds[-1]
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]
    current_acc = current_best["accuracy"]

    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    failure_detail = []
    for r in failures[:20]:
        pd_data = next(
            (
                rd.get("pipeline_data", {})
                for rd in replay_results
                if rd["query"] == r["query"]
            ),
            {},
        )
        candidates = pd_data.get("token_matched_candidates", [])
        candidate_names = [
            c[0] if isinstance(c, (list, tuple)) else str(c)
            for c in candidates[:10]
        ]
        gt_in_candidates = r["ground_truth"] in candidate_names
        profile = pd_data.get("entity_profile", {})

        failure_detail.append(
            f"  Query: {r['query'][:60]}\n"
            f"    Predicted: {r['predicted'][:50]}\n"
            f"    Ground truth: {r['ground_truth'][:50]}\n"
            f"    GT in candidates: {gt_in_candidates}\n"
            f"    Top candidates: {candidate_names[:5]}\n"
            f"    Core concept: {profile.get('core_concept', '?')}\n"
        )

    history_lines = []
    for rd in campaign_rounds:
        history_lines.append(
            f"  Round {rd['round']}: {rd['label'][:40]} -> {rd['accuracy']:.1%}"
        )

    suggestion_prompt = (
        "You are an expert optimization advisor for a terminology "
        "normalization pipeline.\n"
        "Analyze the current campaign state and provide actionable "
        "suggestions for the next round.\n\n"
        f"CAMPAIGN HISTORY:\n{chr(10).join(history_lines)}\n\n"
        f"CURRENT BEST PROMPT ({current_acc:.1%} accuracy):\n"
        f"---\n{current_ps.render()}\n---\n\n"
        f"CURRENT CONFIG:\n{json.dumps(campaign_config, indent=2)}\n\n"
        f"FAILURE DETAILS ({len(failures)} failures out of "
        f"{len(current_results)} queries):\n"
        f"{chr(10).join(failure_detail)}\n\n"
        "Provide your analysis as a JSON object with these keys:\n\n"
        '1. "failure_patterns": array of objects, each with:\n'
        '   - "category": failure type (e.g., "bad_profile", '
        '"candidate_absent", "reranker_misjudged", "ambiguous_query")\n'
        '   - "count": estimated count\n'
        '   - "description": explanation\n'
        '   - "examples": array of query strings\n\n'
        '2. "parameter_suggestions": array of objects, each with:\n'
        '   - "parameter": the pipeline_params key to change\n'
        '   - "current_value": current value\n'
        '   - "suggested_value": new value\n'
        '   - "rationale": why this change helps\n\n'
        '3. "prompt_phrase_fragments": array of objects, each with:\n'
        '   - "action": one of "add_to_instruction", '
        '"modify_thinking_style", "add_few_shot", '
        '"modify_answer_format", "modify_persona"\n'
        '   - "text": the exact text snippet to add or use\n'
        '   - "rationale": why this helps with the observed failures\n\n'
        '4. "suggested_config": a complete campaign_config JSON object '
        "with your recommended changes applied\n\n"
        '5. "summary": 2-3 sentence overview of what to try next'
    )

    print("Generating suggestions...")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            eval_llm["provider_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": eval_llm["model"],
                "messages": [{"role": "user", "content": suggestion_prompt}],
                "temperature": 0,
                "max_tokens": 8000,
                "response_format": {"type": "json_object"},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        suggestions = json.loads(
            resp.json()["choices"][0]["message"]["content"]
        )

    return suggestions


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
# Grid Search — Landscape Exploration
# ---------------------------------------------------------------------------

DEFAULT_GRID_AXES = {
    "persona": [
        "",
        "You are a domain expert with deep knowledge of this field.",
        "You are a precise, analytical system that evaluates candidates methodically.",
        "You are a careful assistant that considers all options before deciding.",
    ],
    "task_intent": [
        "",
        "Your task is to identify the single best match from the candidates.",
        "Rank candidates by how well they match the concept described.",
    ],
    "thinking_style": [
        "",
        "Think step by step.",
        "Focus on semantic meaning, not surface-level word overlap.",
        "First understand the core concept, then evaluate each candidate against it.",
    ],
    "answer_format": [
        "",
        "Rank all candidates from most to least relevant.",
    ],
}

GRID_SEARCHABLE_FIELDS = {
    "persona", "task_intent", "problem_description",
    "instruction", "thinking_style", "answer_format",
}

REQUIRED_TEMPLATE_VARS = {"{{core_concept}}", "{{entity_profile_json}}", "{{matches}}"}


async def restructure_context(
    context_input: Any,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        eval_llm: Dict with model, provider_url, temperature, max_tokens.
        api_key: API key for the LLM provider.

    Returns:
        Dict of structured Layer 1 field values.
    """
    if isinstance(context_input, dict):
        mode = "validate"
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        mode = "parse"
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    system_prompt = (
        "You are a prompt engineering assistant. Your job is to structure "
        "user-provided context into Layer 1 prompt fields for an optimization "
        "campaign.\n\n"
        "Layer 1 fields:\n"
        "- persona: Who the LLM should act as (e.g., 'You are a domain expert...')\n"
        "- task_intent: What the prompt needs to accomplish\n"
        "- problem_description: Description of the problem domain\n"
        "- instruction: Core instruction text (may contain template variables)\n"
        "- thinking_style: How to reason (e.g., 'Think step by step')\n"
        "- answer_format: Expected output format\n\n"
        "Return a JSON object with exactly these keys. Use empty string for "
        "fields that don't apply. Be concise and actionable."
    )

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            eval_llm["provider_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": eval_llm["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        result = json.loads(resp.json()["choices"][0]["message"]["content"])

    # Ensure all expected keys present
    for key in ("persona", "task_intent", "problem_description",
                "instruction", "thinking_style", "answer_format"):
        result.setdefault(key, "")

    print(f"Context restructured ({mode} mode):")
    for k, v in result.items():
        if k in GRID_SEARCHABLE_FIELDS and v:
            print(f"  {k}: {v[:80]}{'...' if len(v) > 80 else ''}")

    return result


def validate_grid_config(
    grid_config: dict,
    baseline: PromptState,
) -> dict:
    """Validate grid axes and compute cartesian product size.

    Args:
        grid_config: Dict mapping field names to lists of variant values.
        baseline: The baseline PromptState (for reference).

    Returns:
        Metadata dict: {axes, axis_names, total, actual_count, is_subsampled}

    Raises:
        ValueError: If axis keys are invalid or instruction variants lack
            required template variables.
    """
    axis_names = list(grid_config.keys())
    invalid = set(axis_names) - GRID_SEARCHABLE_FIELDS
    if invalid:
        raise ValueError(
            f"Invalid grid axis fields: {invalid}. "
            f"Must be in {GRID_SEARCHABLE_FIELDS}"
        )

    # Validate instruction variants contain required template vars
    if "instruction" in grid_config:
        for i, variant in enumerate(grid_config["instruction"]):
            if not variant:  # empty string is allowed (means use baseline)
                continue
            missing = REQUIRED_TEMPLATE_VARS - set(
                var for var in REQUIRED_TEMPLATE_VARS if var in variant
            )
            if missing:
                raise ValueError(
                    f"instruction variant {i} is missing template variables: "
                    f"{missing}"
                )

    axes = {name: grid_config[name] for name in axis_names}
    total = 1
    for values in axes.values():
        total *= len(values)

    print(f"Grid config validated:")
    for name, values in axes.items():
        print(f"  {name}: {len(values)} variants")
    print(f"  Total combinations: {total}")

    return {
        "axes": axes,
        "axis_names": axis_names,
        "total": total,
        "actual_count": total,
        "is_subsampled": False,
    }


def build_grid_combinations(
    grid_config: dict,
    baseline: PromptState,
    max_combinations: int = 0,
    seed: int = 42,
) -> Tuple[list, dict]:
    """Build cartesian product of grid axes as PromptState variants.

    Args:
        grid_config: Dict mapping field names to lists of variant values.
        baseline: The baseline PromptState to derive from.
        max_combinations: If >0, subsample to this many combos.
        seed: Random seed for reproducible subsampling.

    Returns:
        Tuple of (combinations list, ps_lookup dict).
        Each combination is (coord_dict, ps_id).
        ps_lookup maps ps_id -> PromptState.
    """
    axis_names = list(grid_config.keys())
    axis_values = [grid_config[name] for name in axis_names]
    all_combos = list(itertools.product(*axis_values))

    if max_combinations > 0 and len(all_combos) > max_combinations:
        rng = random.Random(seed)
        all_combos = rng.sample(all_combos, max_combinations)
        print(f"Subsampled: {max_combinations}/{len(list(itertools.product(*axis_values)))} combinations")

    combinations = []
    ps_lookup = {}

    for combo in all_combos:
        coord_dict = {}
        changes = {}
        labels = []

        for i, (name, value) in enumerate(zip(axis_names, combo)):
            idx = grid_config[name].index(value)
            coord_dict[name] = idx
            labels.append(f"{name[:2]}={idx}")
            if value:  # only override non-empty values
                changes[name] = value

        desc = f"grid[{','.join(labels)}]"
        ps = baseline.derive(**changes, changes_description=desc)
        combinations.append((coord_dict, ps.id))
        ps_lookup[ps.id] = ps

    print(f"Built {len(combinations)} grid combinations")
    return combinations, ps_lookup


async def run_grid_search(
    combinations: list,
    ps_lookup: dict,
    eval_data: list,
    eval_llm: dict,
    api_key: str,
) -> pd.DataFrame:
    """Evaluate each grid combination on eval_data.

    Args:
        combinations: List of (coord_dict, ps_id) tuples.
        ps_lookup: Dict mapping ps_id -> PromptState.
        eval_data: List of query dicts with pipeline_data.
        eval_llm: Dict with model, provider_url, temperature, max_tokens.
        api_key: API key for the LLM provider.

    Returns:
        DataFrame with columns: axis indices, axis labels, prompt_state_id,
        hits, total, accuracy, errors. Sorted by accuracy desc.
    """
    rows = []
    pbar = tqdm(total=len(combinations), desc="Grid search", unit="combo")

    for coord_dict, ps_id in combinations:
        ps = ps_lookup[ps_id]
        results = await evaluate_prompt(
            ps, eval_data, eval_llm, api_key,
            label=ps.changes_description or ps_id[:12],
            verbose=False,
        )

        hits = sum(1 for r in results if r["hit"])
        errors = sum(1 for r in results if r["error"])
        total = len(results)
        accuracy = hits / total if total else 0

        row = dict(coord_dict)
        row["prompt_state_id"] = ps_id
        row["hits"] = hits
        row["total"] = total
        row["accuracy"] = accuracy
        row["errors"] = errors
        rows.append(row)

        pbar.update(1)

    pbar.close()

    df = pd.DataFrame(rows)
    df = df.sort_values("accuracy", ascending=False).reset_index(drop=True)
    return df


def display_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    top_k: int = 5,
) -> None:
    """Display ranked table, marginal stats, and pairwise heatmaps.

    Args:
        grid_df: DataFrame from run_grid_search().
        grid_config: Dict mapping field names to lists of variant values.
        top_k: Number of top combos to highlight.
    """
    from IPython.display import display as ipy_display

    axis_names = list(grid_config.keys())

    # 1. Ranked table
    print(f"\n{'=' * 70}")
    print(f"GRID RESULTS — TOP {top_k}")
    print(f"{'=' * 70}")
    display_cols = axis_names + ["accuracy", "hits", "total", "errors"]
    ipy_display(grid_df[display_cols].head(top_k))

    # 2. Marginal stats
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

    # 3. Pairwise heatmaps
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


def select_grid_winner(
    grid_df: pd.DataFrame,
    ps_lookup: dict,
) -> dict:
    """Select the best-performing grid combination.

    Args:
        grid_df: DataFrame from run_grid_search() (sorted by accuracy desc).
        ps_lookup: Dict mapping ps_id -> PromptState.

    Returns:
        Campaign round entry dict with keys: round, label, prompt_state,
        accuracy, hits, total, results.
    """
    best_row = grid_df.iloc[0]
    ps_id = best_row["prompt_state_id"]
    ps = ps_lookup[ps_id]

    print(f"Grid winner: {ps.changes_description or ps_id[:12]}")
    print(f"  Accuracy: {best_row['accuracy']:.1%} ({best_row['hits']}/{best_row['total']})")
    print(f"  PromptState: {ps_id[:12]}")

    return {
        "round": "grid",
        "label": f"grid_winner ({ps.changes_description or ps_id[:12]})",
        "prompt_state": ps,
        "accuracy": best_row["accuracy"],
        "hits": int(best_row["hits"]),
        "total": int(best_row["total"]),
        "results": [],  # full results not stored per-combo
    }


async def analyze_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    eval_llm: dict,
    api_key: str,
) -> dict:
    """LLM analysis of grid search results.

    Args:
        grid_df: DataFrame from run_grid_search().
        grid_config: Dict mapping field names to lists of variant values.
        eval_llm: Dict with model, provider_url, temperature, max_tokens.
        api_key: API key for the LLM provider.

    Returns:
        Dict with keys: key_findings, strongest_fields, recommended_focus,
        campaign_advice.
    """
    axis_names = list(grid_config.keys())

    # Build summary for LLM
    top_combos = grid_df.head(5).to_dict("records")
    worst_combos = grid_df.tail(3).to_dict("records")

    marginals = {}
    for name in axis_names:
        marginal = grid_df.groupby(name)["accuracy"].mean()
        marginals[name] = {
            int(idx): {
                "accuracy": float(acc),
                "label": grid_config[name][idx][:80] if grid_config[name][idx] else "(empty)",
            }
            for idx, acc in marginal.items()
        }

    analysis_prompt = (
        "You are an optimization advisor. Analyze the results of a grid search "
        "over prompt configuration fields.\n\n"
        f"GRID AXES: {axis_names}\n"
        f"TOTAL COMBINATIONS: {len(grid_df)}\n\n"
        f"TOP 5 COMBINATIONS:\n{json.dumps(top_combos, indent=2, default=str)}\n\n"
        f"WORST 3 COMBINATIONS:\n{json.dumps(worst_combos, indent=2, default=str)}\n\n"
        f"MARGINAL STATS (mean accuracy per axis value):\n"
        f"{json.dumps(marginals, indent=2, default=str)}\n\n"
        "Return a JSON object with:\n"
        '- "key_findings": array of 3-5 concise findings\n'
        '- "strongest_fields": array of field names that matter most for accuracy\n'
        '- "recommended_focus": which fields to prioritize in optimization\n'
        '- "campaign_advice": 2-3 sentence advice for the optimization campaign'
    )

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            eval_llm["provider_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": eval_llm["model"],
                "messages": [{"role": "user", "content": analysis_prompt}],
                "temperature": 0,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        analysis = json.loads(resp.json()["choices"][0]["message"]["content"])

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
    """Load per-query evaluation data from synced experiments or replay cache.

    Priority chain:
    1. Traces — extract from synced experiment traces if available
    2. Replay cache — load from existing execution's pipeline_data
    3. Neither — print message and return empty list

    Args:
        store: ProjectStore instance.
        backend_id: Backend identifier.
        experiment_id: Experiment identifier.
        query_limit: If >0, sample this many queries.

    Returns:
        List of query dicts with keys: query, ground_truth, pipeline_data
        (containing entity_profile and token_matched_candidates).
    """
    # Try traces from synced experiment data
    exp_data = store.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if exp_data:
        traces = exp_data.get("traces", [])
        if traces:
            eval_data = []
            for trace in traces:
                pipeline_data = trace.get("pipeline_data", {})
                if pipeline_data.get("entity_profile"):
                    eval_data.append({
                        "query": trace.get("query", ""),
                        "ground_truth": trace.get("ground_truth", ""),
                        "pipeline_data": pipeline_data,
                        "status": "success",
                    })
            if eval_data:
                if query_limit > 0 and len(eval_data) > query_limit:
                    rng = random.Random(42)
                    eval_data = rng.sample(eval_data, query_limit)
                print(f"Loaded {len(eval_data)} eval queries from experiment traces")
                return eval_data

    # Try replay cache
    executions = store.list_executions(backend_id)
    for ex_summary in executions:
        if ex_summary.get("experiment_id") == experiment_id:
            execution = store.load_execution(backend_id, ex_summary["execution_id"])
            if execution:
                eval_data = [
                    r.model_dump() for r in execution.results
                    if r.status == "success"
                    and r.pipeline_data
                    and r.pipeline_data.get("entity_profile")
                ]
                if eval_data:
                    if query_limit > 0 and len(eval_data) > query_limit:
                        rng = random.Random(42)
                        eval_data = rng.sample(eval_data, query_limit)
                    print(
                        f"Loaded {len(eval_data)} eval queries from replay cache "
                        f"(execution {ex_summary['execution_id']})"
                    )
                    return eval_data

    print(
        "No eval data found. Run replay (Section 3) first or re-sync with "
        "include_traces=true."
    )
    return []


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: ProjectStore,
    backend_id: str,
) -> dict:
    """Find best round, save to store, print confirmation. Returns save_data."""
    winner = campaign_rounds[-1]["prompt_state"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_state"]
            winner_acc = rd["accuracy"]

    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": campaign_rounds[0]["accuracy"],
        "improvement": winner_acc - campaign_rounds[0]["accuracy"],
        "config": campaign_config,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.save_sync(backend_id, filename, save_data)

    print("WINNER SAVED")
    print(f"  PromptState: {winner.id[:12]}")
    print(
        f"  Accuracy: {winner_acc:.1%} "
        f"(baseline: {campaign_rounds[0]['accuracy']:.1%}, "
        f"delta: {save_data['improvement']:+.1%})"
    )
    print(f"  File: .promptpotter/projects/{backend_id}/sync/{filename}")
    print(f"  Rounds completed: {len(campaign_rounds) - 1}")

    return save_data
