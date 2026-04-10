"""Sample measurement — single-query pipeline execution and scoring.

Measures one sample at a time via the backend query endpoint.
Handles per-node intermediate cache walk, cache populate, and error
classification.  Dataset-level scoring lives in ``dataset_scoring``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx

from promptpotter.models.evaluator import EvalResult, ExactMatchEvaluator
from promptpotter.models.query_result import QueryResult
from promptpotter.shared.constants import NO_RESULT
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.prompt_interpolation import interpolate_pipeline_params

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.models.scoring_context import QueryRunner
    from promptpotter.services.stores.intermediate_cache import IntermediateCache

_evaluator = ExactMatchEvaluator({"strip": True})

# Type alias for per-dataset scoring callable (see shared/scoring.py)
Scorer = Callable[[dict], float]

logger = logging.getLogger(__name__)

__all__ = ["measure_sample"]


def _parse_backend_response(
    backend_data: dict,
    final_ranking: list,
    pipeline_schema: PipelineSchema,
) -> dict:
    """Extract pipeline data fields from a backend /matches response.

    Derives the key set from the ``PipelineSchema``'s observation mappings.
    """
    pd: dict = {"final_ranking": final_ranking}
    keys: set[str] = set()
    for mappings in pipeline_schema.obs_extraction_map().values():
        for m in mappings:
            keys.add(m.pipeline_key)
    # Always include infrastructure keys
    keys |= {"step_timings", "llm_provider", "total_time", "pipeline_params", "diagnostics"}
    for key in keys:
        val = backend_data.get(key)
        if val is not None:
            pd[key] = val

    # Determine terminating step: explicit from backend takes priority
    terminated_at = backend_data.get("terminated_at")
    if terminated_at is None:
        st = pd.get("step_timings")
        if st:
            terminated_at = pipeline_schema.infer_terminating_node(st)
    if terminated_at is not None:
        pd["terminated_at"] = terminated_at

    return pd


def _local_result(
    query: str,
    ground_truth: str,
    node_outputs: dict,
    target_steps: list[str],
    pipeline_schema: PipelineSchema,
    scorer: Scorer | None = None,
) -> QueryResult:
    """Construct a measurement result locally from intermediate cache (no backend call).

    Used when the intermediate cache covers ALL target steps — the backend
    would just repackage the same precomputed data, so we skip the HTTP
    round-trip entirely.
    """
    last_node = target_steps[-1] if target_steps else ""
    raw_ranked = node_outputs.get(last_node, [])

    # Normalize: intermediate cache stores [{term, score}],
    # backend returns [{candidate, score}]
    ranked: list[dict] = []
    for item in raw_ranked:
        if isinstance(item, dict):
            ranked.append(
                {
                    "candidate": item.get("candidate") or item.get("term", NO_RESULT),
                    "score": item.get("score", 0),
                }
            )
        else:
            ranked.append({"candidate": str(item), "score": 0})

    predicted = ranked[0]["candidate"] if ranked else NO_RESULT
    eval_output = _evaluator.evaluate(ground_truth, predicted)

    pd: dict = {
        "final_ranking": ranked,
        "total_time": 0.0,
        "terminated_at": last_node,
    }
    # Include node-specific outputs that obs mappings expect
    for step in target_steps:
        node_data = node_outputs.get(step)
        if node_data is not None and step != last_node:
            pd[step] = node_data

    gt_rank = next(
        (i + 1 for i, c in enumerate(ranked) if c.get("candidate") == ground_truth), None
    )
    n_candidates = len(ranked)
    result: dict = {
        "query": query,
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": eval_output.result == EvalResult.PASS,
        "score": eval_output.score,
        "error": None,
        "n_candidates": n_candidates,
        "ground_truth_rank": gt_rank,
        "pipeline_data": pd,
        "precomputed_through": list(target_steps),
        "cached": True,
    }
    if scorer:
        result["score"] = scorer(result)
        result["hit"] = result["score"] >= 1.0
    return result  # type: ignore[return-value]


def _error_result(query: str, ground_truth: str, error_msg: str) -> QueryResult:
    """Build a standard error result dict."""
    return {
        "query": query,
        "predicted": "ERROR",
        "ground_truth": ground_truth,
        "hit": False,
        "score": 0.0,
        "error": error_msg or "unknown error",
        "pipeline_data": None,
    }


def _classify_http_error(exc: httpx.HTTPStatusError) -> str:
    """Classify an HTTP error into a tagged message."""
    code = exc.response.status_code
    if 400 <= code < 500:
        return f"[{ErrorCategory.CLIENT}] HTTP {code}: {exc} — Check pipeline configuration and request parameters."
    return f"[{ErrorCategory.SERVER}] HTTP {code}: {exc} — Backend may be experiencing issues."


async def measure_sample(
    query_data: dict,
    backend_client: QueryRunner,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    intermediate_cache: IntermediateCache | None = None,
    backend_id: str = "",
    scorer: Scorer | None = None,
) -> QueryResult:
    """Measure one sample: run query through pipeline, score against ground truth."""
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    if pipeline_schema is None:
        from promptpotter.models.pipeline_schema import PipelineSchema

        pipeline_schema = PipelineSchema()

    _target_steps = (pipeline_params or {}).get("steps", [])

    try:
        # Per-node cache: reuse upstream pipeline nodes (multi-step only).
        # LLM-only pipelines have no steps → this block is a clean no-op.
        precomputed = None
        cached_steps: list[str] = []
        prefix_keys: list[tuple[str, str]] = []
        if intermediate_cache and _target_steps:
            from promptpotter.services.stores.intermediate_cache import compute_prefix_keys

            prefix_keys = compute_prefix_keys(pipeline_params or {}, pipeline_schema)
            node_outputs_hit, cached_steps = intermediate_cache.walk_prefix(
                backend_id,
                query,
                prefix_keys,
            )
            if node_outputs_hit:
                precomputed = node_outputs_hit

        # Full coverage: all target steps cached — skip backend entirely
        if precomputed and cached_steps == _target_steps:
            return _local_result(
                query,
                ground_truth,
                precomputed,
                _target_steps,
                pipeline_schema,
                scorer=scorer,
            )

        # Interpolate {{variable}} placeholders in prompt templates from query_data
        wire_params = interpolate_pipeline_params(pipeline_params or {}, query_data)

        resp = await backend_client.run_query(
            query,
            pipeline_params=wire_params,
            precomputed=precomputed,
        )
        data = resp.get("data", {})

        # Cache populate: store per-node outputs (multi-step only;
        # LLM-only adapters return empty node_outputs so this is a no-op)
        node_outputs = data.get("node_outputs")
        if intermediate_cache and _target_steps and node_outputs and prefix_keys:
            precomputed_set = set(precomputed or {})
            for node_name, cache_key in prefix_keys:
                if node_name in node_outputs and node_name not in precomputed_set:
                    intermediate_cache.put_node(
                        backend_id,
                        node_name,
                        cache_key,
                        query,
                        node_outputs[node_name],
                    )

        ranked = data.get("final_ranking", [])
        predicted = ranked[0].get("candidate", NO_RESULT) if ranked else NO_RESULT
        if predicted == "ERROR":
            return _error_result(
                query,
                ground_truth,
                f"[{ErrorCategory.PIPELINE}] Backend returned ERROR as candidate"
                " — pipeline internal failure for this query.",
            )
        eval_output = _evaluator.evaluate(ground_truth, predicted)
        gt_rank = next(
            (i + 1 for i, c in enumerate(ranked) if c.get("candidate") == ground_truth),
            None,
        )
        result: dict = {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": eval_output.result == EvalResult.PASS,
            "score": eval_output.score,
            "error": None,
            "n_candidates": len(ranked),
            "ground_truth_rank": gt_rank,
            "pipeline_data": _parse_backend_response(data, ranked, pipeline_schema),
        }
        if precomputed:
            result["precomputed_through"] = list(precomputed.keys())
        if scorer:
            result["score"] = scorer(result)
            result["hit"] = result["score"] >= 1.0
        return result  # type: ignore[return-value]
    except httpx.HTTPStatusError as exc:
        error_msg = _classify_http_error(exc)
        logger.warning("measure_sample for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"[{ErrorCategory.CONNECTION}] {exc} — Backend may be down or unreachable."
        logger.warning("measure_sample CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("measure_sample failed for %s: %s", query[:60], exc)
        return _error_result(query, ground_truth, str(exc))
