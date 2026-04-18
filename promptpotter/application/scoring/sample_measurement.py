"""Sample measurement — single-query pipeline execution and scoring.

Measures one sample at a time via the backend query endpoint.
Dataset-level scoring and prior-result matching live in
``search_point_scorer``.

Prompt interpolation
--------------------
Prompt templates can contain ``{{variable}}`` placeholders filled at
eval time from ``query_data`` fields.  Syntax uses double-brace
``{{name}}`` — same convention as ``PromptTemplate.compile_prompt()``.
Available variables come from the dataset item dict; ``ground_truth``
is always excluded to prevent data leakage.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.domain.scoring import ExactMatchComparator, GroundTruthResult, QueryResult
from promptpotter.shared.constants import NO_RESULT
from promptpotter.shared.errors import ErrorCategory

if TYPE_CHECKING:
    from promptpotter.domain.scoring import ScoringEnv

_comparator = ExactMatchComparator({"strip": True})

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template interpolation
# ---------------------------------------------------------------------------

_TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# Fields that must never be interpolated into prompts (answer leakage).
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "ground_truth",
        "hit",
        "score",
        "error",
        "source_sheet",
    }
)


def extract_template_variables(text: str) -> list[str]:
    """Return sorted list of ``{{variable}}`` names found in *text*."""
    return sorted(set(_TEMPLATE_VAR_RE.findall(text)))


def interpolate_prompt(text: str, variables: dict[str, Any]) -> str:
    """Replace ``{{key}}`` placeholders in *text* from *variables*.

    - Keys in ``_EXCLUDED_FIELDS`` are silently skipped (data-leakage guard).
    - Missing keys are left as-is (no crash, logged at debug level).
    - Values are stringified via ``str()``.
    """
    expected = set(_TEMPLATE_VAR_RE.findall(text))
    if not expected:
        return text

    safe_vars = {k: v for k, v in variables.items() if k not in _EXCLUDED_FIELDS}
    for key in expected:
        if key in safe_vars:
            text = text.replace("{{" + key + "}}", str(safe_vars[key]))
        else:
            logger.debug("Template variable {{%s}} not in query_data — left as-is", key)
    return text


def interpolate_pipeline_params(
    pipeline_params: dict[str, Any],
    query_data: dict[str, Any],
) -> dict[str, Any]:
    """Return a shallow copy of *pipeline_params* with prompts interpolated.

    Walks every node config dict.  If a node has a ``"prompt"`` key whose
    value contains ``{{...}}`` tokens, those tokens are replaced from
    *query_data*.  Other keys are untouched.

    The original dict is never mutated.
    """
    has_templates = False
    for v in pipeline_params.values():
        if isinstance(v, dict) and "prompt" in v:
            prompt = v["prompt"]
            if isinstance(prompt, str) and _TEMPLATE_VAR_RE.search(prompt):
                has_templates = True
                break

    if not has_templates:
        return pipeline_params  # fast path — no copy needed

    out = dict(pipeline_params)
    for node_name, node_cfg in out.items():
        if not isinstance(node_cfg, dict) or "prompt" not in node_cfg:
            continue
        prompt = node_cfg["prompt"]
        if not isinstance(prompt, str):
            continue
        interpolated = interpolate_prompt(prompt, query_data)
        if interpolated is not prompt:
            out[node_name] = {**node_cfg, "prompt": interpolated}
    return out


__all__ = ["measure_sample"]

# Wire-response keys always kept on pipeline_data, regardless of pipeline schema.
_INFRA_KEYS: frozenset[str] = frozenset(
    {"step_timings", "llm_provider", "total_time", "pipeline_params", "diagnostics"}
)


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
    env: ScoringEnv,
    pipeline_params: dict | None = None,
) -> QueryResult:
    """Measure one sample: run query through pipeline, score against ground truth."""
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    pipeline_schema = env.pipeline_schema
    if pipeline_schema is None:
        from promptpotter.domain.pipeline_schema import PipelineSchema

        pipeline_schema = PipelineSchema()

    try:
        wire_params = interpolate_pipeline_params(pipeline_params or {}, query_data)
        resp = await env.backend_client.run_query(query, pipeline_params=wire_params)
        data = resp.get("data", {})

        ranked = data.get("final_ranking", [])
        predicted = ranked[0].get("candidate", NO_RESULT) if ranked else NO_RESULT
        if predicted == "ERROR":
            return _error_result(
                query,
                ground_truth,
                f"[{ErrorCategory.PIPELINE}] Backend returned ERROR as candidate"
                " — pipeline internal failure for this query.",
            )
        comparison = _comparator.compare(ground_truth, predicted)
        gt_rank = next(
            (i + 1 for i, c in enumerate(ranked) if c.get("candidate") == ground_truth),
            None,
        )

        # Project wire response → pipeline_data.  Keys: per-schema observation
        # outputs + infrastructure keys.
        pd: dict = {"final_ranking": ranked}
        for key in pipeline_schema.observation_keys | _INFRA_KEYS:
            val = data.get(key)
            if val is not None:
                pd[key] = val
        terminated_at = data.get("terminated_at")
        if terminated_at is None:
            st = pd.get("step_timings") or {}
            for node in pipeline_schema.nodes:
                if st.get(node.name) is not None:
                    terminated_at = node.name
        if terminated_at is not None:
            pd["terminated_at"] = terminated_at

        result: dict = {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": comparison.result == GroundTruthResult.PASS,
            "score": comparison.score,
            "error": None,
            "n_candidates": len(ranked),
            "ground_truth_rank": gt_rank,
            "pipeline_data": pd,
        }
        # Populate per-query evaluator values. Sits inside pipeline_data so
        # the existing per-query scoring formula compiler flattens it into
        # the formula namespace (``evaluators.retrieval_shortfall`` etc.).
        from promptpotter.application.scoring.evaluators import materialize_query_values

        query_evaluators = materialize_query_values(pipeline_schema, result)  # type: ignore[arg-type]
        if query_evaluators:
            pd["evaluators"] = query_evaluators
        if env.scorer:
            result["score"] = env.scorer(result)
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
