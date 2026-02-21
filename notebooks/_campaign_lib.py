"""Helper library for optimization_campaign.ipynb and termnorm_backend.ipynb.

Extracts plumbing (imports, cache logic, async loops, LLM calls)
so notebook cells stay short and dashboard-readable.
"""

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
