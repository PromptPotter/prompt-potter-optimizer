"""Sample measurement — single-sample pipeline execution and scoring.

Measures one Sample at a time via the backend query endpoint.
Dataset-level scoring and prior-result matching live in
``search_point_scorer``.

Prompt interpolation
--------------------
Prompt templates can contain ``{{variable}}`` placeholders filled at
measurement time from the Sample's fields.  Syntax uses double-brace
``{{name}}`` — same convention as ``PromptTemplate.compile_prompt()``.
``ground_truth`` is always excluded to prevent data leakage.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryResult
from promptpotter.shared.constants import NO_RESULT
from promptpotter.shared.errors import ErrorCategory

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session

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
            logger.debug("Template variable {{%s}} not in sample fields — left as-is", key)
    return text


def interpolate_pipeline_params(
    pipeline_params: dict[str, Any],
    query_data: dict[str, Any],
) -> dict[str, Any]:
    """Return a shallow copy of *pipeline_params* with prompts interpolated."""
    has_templates = False
    for v in pipeline_params.values():
        if isinstance(v, dict) and "prompt" in v:
            prompt = v["prompt"]
            if isinstance(prompt, str) and _TEMPLATE_VAR_RE.search(prompt):
                has_templates = True
                break

    if not has_templates:
        return pipeline_params

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


def _error_result(
    sample: Sample,
    error_msg: str,
) -> QueryResult:
    """Build a standard error result dict.

    Error rows intentionally carry no ``hit``/``score`` — those fields are
    owned exclusively by ``rescore_results``, which skips error rows.
    """
    return QueryResult(
        sample_id=sample.id,
        query=sample.query,
        ground_truth=sample.ground_truth,
        predicted="ERROR",
        error=error_msg or "unknown error",
        pipeline_data=None,
    )


def _classify_http_error(exc: httpx.HTTPStatusError) -> str:
    """Classify an HTTP error into a tagged message."""
    code = exc.response.status_code
    if 400 <= code < 500:
        return f"[{ErrorCategory.CLIENT}] HTTP {code}: {exc} — Check pipeline configuration and request parameters."
    return f"[{ErrorCategory.SERVER}] HTTP {code}: {exc} — Backend may be experiencing issues."


async def measure_sample(
    sample: Sample,
    session: Session,
    pipeline_params: dict | None = None,
) -> QueryResult:
    """Measure one Sample: run query through pipeline, score against ground truth."""
    query = sample.query
    ground_truth = sample.ground_truth

    pipeline_schema = session.pipeline_schema
    if pipeline_schema is None:
        from promptpotter.domain.pipeline_schema import PipelineSchema

        pipeline_schema = PipelineSchema()

    try:
        wire_params = interpolate_pipeline_params(pipeline_params or {}, sample.model_dump())
        resp = await session.backend_client.run_query(query, pipeline_params=wire_params)
        data = resp.get("data", {})

        ranked = data.get("final_ranking", [])
        predicted = ranked[0].get("candidate", NO_RESULT) if ranked else NO_RESULT
        if predicted == "ERROR":
            return _error_result(
                sample,
                f"[{ErrorCategory.PIPELINE}] Backend returned ERROR as candidate"
                " — pipeline internal failure for this query.",
            )
        gt_rank = next(
            (i + 1 for i, c in enumerate(ranked) if c.get("candidate") == ground_truth),
            None,
        )

        # Project wire response → pipeline_data.
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
            "sample_id": sample.id,
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "error": None,
            "n_candidates": len(ranked),
            "ground_truth_rank": gt_rank,
            "pipeline_data": pd,
        }
        # Populate per-query evaluator values.
        from promptpotter.application.scoring.evaluators import materialize_query_values

        query_evaluators = materialize_query_values(pipeline_schema, result)  # type: ignore[arg-type]
        if query_evaluators:
            pd["evaluators"] = query_evaluators
        from promptpotter.shared.scoring import rescore_results

        assert session.scorer is not None, "session.scorer required for measurement"
        rescore_results(
            [result],  # type: ignore[list-item]
            session.scorer,
            session.scorer_id,
            session.scorer_formula,
        )
        return result  # type: ignore[return-value]
    except httpx.HTTPStatusError as exc:
        error_msg = _classify_http_error(exc)
        logger.warning("measure_sample for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"[{ErrorCategory.CONNECTION}] {exc} — Backend may be down or unreachable."
        logger.warning("measure_sample CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("measure_sample failed for %s: %s", query[:60], exc)
        return _error_result(sample, str(exc))
