#!/usr/bin/env python3
"""
Replay TermNorm experiment queries with skip_llm_ranking=true (Variant A).

Loads production queries from experiment_1_mappings_response.json,
calls the TermNorm backend API for each, and saves results to a new fixture.

Prerequisites:
  - TermNorm backend running at http://127.0.0.1:8000
  - 127.0.0.1 authorized in TermNorm's config/users.json

Usage:
    python scripts/replay_without_llm2.py --dry-run
    python scripts/replay_without_llm2.py --limit 3
    python scripts/replay_without_llm2.py
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
SOURCE_FIXTURE = FIXTURE_DIR / "experiment_1_mappings_response.json"
OUTPUT_FIXTURE = FIXTURE_DIR / "experiment_1_variant_a_replay.json"


def load_source_fixture() -> dict:
    """Load the experiment fixture."""
    with open(SOURCE_FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_session_terms(data: dict) -> List[str]:
    """Extract unique non-empty dataset_entry values from mappings."""
    entries = set()
    for m in data["mappings"]:
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            entries.add(entry)
    return sorted(entries)


def extract_replay_queries(data: dict) -> List[Dict[str, Any]]:
    """Extract production queries that have valid ground truth.

    Matches evaluation_result queries back to mappings via bom_material.
    Production queries have format 'bom_material/process'.
    """
    # Build bom_material -> dataset_entry lookup
    bom_to_gt = {}
    for m in data["mappings"]:
        bom = m["bom_material"]
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            bom_to_gt[bom] = entry

    queries = []
    eval_results = data["runs"][0]["evaluation_results"]

    for er in eval_results:
        query = er["query"]

        # Try splitting on last '/' to get bom_material and process
        if "/" in query:
            # Split on last '/' — handles cases like "PC/ABS CYCOLOY/molding"
            last_slash = query.rfind("/")
            bom_material = query[:last_slash].strip()
            process = query[last_slash + 1:].strip()
        else:
            bom_material = query.strip()
            process = ""

        # Match to ground truth
        if bom_material not in bom_to_gt:
            continue

        queries.append({
            "query": query,
            "bom_material": bom_material,
            "process": process,
            "ground_truth": bom_to_gt[bom_material],
            "original_predicted": er.get("predicted", ""),
            "original_latency_ms": er.get("latency_ms", 0),
            "original_confidence": er.get("confidence", 0),
            "original_trace_id": er.get("trace_id", ""),
        })

    return queries


async def init_session(
    client: httpx.AsyncClient,
    base_url: str,
    terms: List[str],
) -> None:
    """POST /sessions with terms array."""
    resp = await client.post(
        f"{base_url}/sessions",
        json={"terms": terms},
        timeout=30.0,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  Session initialized: {result.get('message', 'OK')}")


async def run_single_query(
    client: httpx.AsyncClient,
    base_url: str,
    query: str,
) -> Dict[str, Any]:
    """POST /matches with skip_llm_ranking=true."""
    resp = await client.post(
        f"{base_url}/matches",
        json={"query": query, "skip_llm_ranking": True},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()


async def replay_all(
    base_url: str,
    queries: List[Dict[str, Any]],
    terms: List[str],
    delay_between: float = 2.0,
) -> List[Dict[str, Any]]:
    """Replay all queries sequentially against the TermNorm backend."""
    results = []
    async with httpx.AsyncClient() as client:
        await init_session(client, base_url, terms)

        for i, q in enumerate(queries):
            label = q["query"][:60]
            print(f"  [{i + 1}/{len(queries)}] {label}...", end="", flush=True)
            start = time.time()
            try:
                response = await run_single_query(client, base_url, q["query"])
                elapsed = time.time() - start
                data = response.get("data", {})
                ranked = data.get("ranked_candidates", [])
                top = ranked[0] if ranked else {}

                results.append({
                    "query": q["query"],
                    "bom_material": q["bom_material"],
                    "process": q["process"],
                    "ground_truth": q["ground_truth"],
                    "predicted": top.get("candidate", "NO_RESULT"),
                    "confidence": top.get("relevance_score", 0),
                    "ranked_candidates": ranked[:20],
                    "latency_ms": round(elapsed * 1000, 1),
                    "total_time_api": data.get("total_time"),
                    "web_search_status": data.get("web_search_status"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "success",
                    "variant_b_predicted": q["original_predicted"],
                    "variant_b_latency_ms": q["original_latency_ms"],
                    "variant_b_confidence": q["original_confidence"],
                })
                pred = top.get("candidate", "NO_RESULT")[:50]
                print(f" -> {pred} ({elapsed:.1f}s)")
            except Exception as e:
                elapsed = time.time() - start
                results.append({
                    "query": q["query"],
                    "bom_material": q["bom_material"],
                    "process": q["process"],
                    "ground_truth": q["ground_truth"],
                    "status": "error",
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "variant_b_predicted": q["original_predicted"],
                    "variant_b_latency_ms": q["original_latency_ms"],
                    "variant_b_confidence": q["original_confidence"],
                })
                print(f" ERROR ({elapsed:.1f}s): {e}")

            if i < len(queries) - 1:
                await asyncio.sleep(delay_between)

    return results


LIMITATIONS = [
    "Session terms pool is ~93 unique dataset_entry values from mappings"
    " (smaller than production ecoinvent DB)",
    "Web search results may differ from original run"
    " (different time, pages changed)",
    "LLM1 entity profiling may produce different profiles"
    " (non-deterministic)",
]


def save_results(
    results: List[Dict], terms: List[str], source_data: dict
) -> None:
    """Save replay results to fixture file."""
    output = {
        "variant": "A",
        "variant_label": "LLM1-TokenMatching (no LLM2)",
        "pipeline_notation": "LLM1-TokenMatching",
        "source_experiment": source_data["experiment"]["experiment_id"],
        "source_run_id": source_data["runs"][0]["run_id"],
        "replay_timestamp": datetime.now(timezone.utc).isoformat(),
        "session_terms_count": len(terms),
        "query_count": len(results),
        "successful_count": sum(1 for r in results if r["status"] == "success"),
        "error_count": sum(1 for r in results if r["status"] == "error"),
        "limitations": LIMITATIONS,
        "results": results,
    }
    OUTPUT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved to {OUTPUT_FIXTURE}")


async def ingest_to_promptpotter(
    promptpotter_url: str,
    results: List[Dict],
    terms: List[str],
    source_data: dict,
) -> None:
    """POST replay results to PromptPotter's ablation API."""
    payload = {
        "variant_label": "LLM1-TokenMatching (no LLM2)",
        "pipeline_notation": "LLM1-TokenMatching",
        "source_experiment": source_data["experiment"]["experiment_id"],
        "source_run_id": source_data["runs"][0]["run_id"],
        "session_terms_count": len(terms),
        "limitations": LIMITATIONS,
        "results": [r for r in results if r.get("status") == "success"],
    }

    url = f"{promptpotter_url}/api/v1/ablation/results"
    print(f"\n  Ingesting {len(payload['results'])} results to {url}...")

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=30.0)
        resp.raise_for_status()
        body = resp.json()
        print(f"  Ingested: ablation_id={body['ablation_id']}, "
              f"queries={body['query_count']}, stored_at={body['stored_at']}")
        print(f"  Compare at: {promptpotter_url}/api/v1/ablation/compare/{body['ablation_id']}")


def main():
    parser = argparse.ArgumentParser(
        description="Replay TermNorm experiment without LLM2"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="TermNorm API base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--promptpotter-url",
        default="http://127.0.0.1:8001",
        help="PromptPotter API URL to ingest results (default: http://127.0.0.1:8001)",
    )
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip ingesting results into PromptPotter API",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Ingest existing fixture results without replaying",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show queries without calling API",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of queries (0=all)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between queries (default: 2.0)",
    )
    args = parser.parse_args()

    # Load data
    print(f"Loading {SOURCE_FIXTURE.name}...")
    data = load_source_fixture()
    queries = extract_replay_queries(data)
    terms = extract_session_terms(data)

    print(f"  Queries with valid ground truth: {len(queries)}")
    print(f"  Session terms: {len(terms)}")

    # Ingest-only mode: load existing replay results and POST to PromptPotter
    if args.ingest_only:
        if not OUTPUT_FIXTURE.exists():
            print(f"  Error: {OUTPUT_FIXTURE} not found. Run replay first.")
            sys.exit(1)
        with open(OUTPUT_FIXTURE, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  Loading existing replay: {len(existing['results'])} results")
        asyncio.run(
            ingest_to_promptpotter(
                args.promptpotter_url, existing["results"], terms, data
            )
        )
        return

    est_minutes = len(queries) * 20 / 60
    print(f"  Estimated time: ~{est_minutes:.0f} minutes")

    if args.limit > 0:
        queries = queries[: args.limit]
        print(f"  Limited to first {args.limit} queries")

    if args.dry_run:
        print("\nDry run — queries that would be replayed:\n")
        for i, q in enumerate(queries):
            gt_short = q["ground_truth"][:50]
            print(f"  {i + 1:3d}. {q['query'][:65]}")
            print(f"       GT: {gt_short}")
            print(f"       B:  {q['original_predicted'][:50]}")
        return

    # Run replay
    print(f"\nReplaying {len(queries)} queries against {args.base_url}...\n")
    results = asyncio.run(replay_all(args.base_url, queries, terms, args.delay))

    # Summary
    successes = sum(1 for r in results if r["status"] == "success")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"\n  Done: {successes} success, {errors} errors")

    save_results(results, terms, data)

    # Ingest into PromptPotter API
    if not args.no_ingest:
        try:
            asyncio.run(
                ingest_to_promptpotter(args.promptpotter_url, results, terms, data)
            )
        except Exception as e:
            print(f"\n  Warning: Failed to ingest into PromptPotter: {e}")
            print(f"  Results are still saved locally at {OUTPUT_FIXTURE}")
    else:
        print("  Skipping PromptPotter ingest (--no-ingest)")


if __name__ == "__main__":
    main()
