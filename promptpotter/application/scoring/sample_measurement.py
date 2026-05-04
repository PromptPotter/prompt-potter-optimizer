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
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.application.scoring.metrics import find_rank
from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryResult
from promptpotter.shared.errors import ErrorCategory, is_degraded

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.intelligence.indexes import AxisIndex

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


__all__ = [
    "compare_rerun",
    "execute_stale_data_protocol",
    "find_gt_rank",
    "measure_sample",
]

# Wire-response keys always kept on pipeline_data, regardless of pipeline schema.
_INFRA_KEYS: frozenset[str] = frozenset(
    {
        "step_timings",
        "step_tokens",
        "llm_provider",
        "total_time",
        "pipeline_params",
        "diagnostics",
    }
)


def _compute_step_tokens(
    resp_data: dict[str, Any],
    pipeline_schema: Any,
    wire_params: dict[str, Any],
) -> dict[str, dict[str, int | bool]]:
    """Collect per-LLM-node token counts from the backend response.

    Seeds from ``resp_data["step_tokens"]`` when the backend provides it
    (marks each entry ``estimated=False``). For any LLM node still missing
    a count, falls back to a chars/4 heuristic over the interpolated prompt
    and the node's observed output text (marks ``estimated=True``).

    Returns an empty dict when the schema carries no LLM nodes.
    """
    out: dict[str, dict[str, int | bool]] = {}

    raw = resp_data.get("step_tokens")
    if isinstance(raw, dict):
        for node_name, entry in raw.items():
            if isinstance(entry, dict):
                out[node_name] = {
                    "input": int(entry.get("input", 0)),
                    "output": int(entry.get("output", 0)),
                    "estimated": False,
                }

    for node in pipeline_schema.nodes:
        if not node.is_llm or node.name in out:
            continue

        node_cfg = wire_params.get(node.name) or {}
        in_text = node_cfg.get("prompt", "") if isinstance(node_cfg, dict) else ""

        out_text_parts: list[str] = []
        for mapping in node.observation_mappings:
            if not mapping.is_llm:
                continue
            pipeline_val = resp_data.get(mapping.pipeline_key)
            if pipeline_val is None:
                continue
            field = mapping.output_field
            if field and isinstance(pipeline_val, dict):
                picked = pipeline_val.get(field, "")
                out_text_parts.append(str(picked))
            elif field and isinstance(pipeline_val, list):
                for item in pipeline_val:
                    if isinstance(item, dict) and field in item:
                        out_text_parts.append(str(item[field]))
            else:
                out_text_parts.append(str(pipeline_val))
        out_text = " ".join(out_text_parts)

        out[node.name] = {
            "input": len(in_text) // 4,
            "output": len(out_text) // 4,
            "estimated": True,
        }

    return out


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
    if code == 429:
        retry_after = exc.response.headers.get("Retry-After", "?")
        body = (exc.response.text or "").strip()[:300]
        return (
            f"[{ErrorCategory.CLIENT}] HTTP 429 rate-limited "
            f"(Retry-After={retry_after}s, attempts exhausted): {body!r}"
        )
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

        step_tokens = _compute_step_tokens(data, pipeline_schema, wire_params)
        if step_tokens:
            pd["step_tokens"] = step_tokens

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
        from promptpotter.application.scoring.formula import rescore_results

        assert session.scoring.scorer is not None, "session.scoring.scorer required for measurement"
        rescore_results(
            [result],  # type: ignore[list-item]
            session.scoring.scorer,
            session.scoring.scorer_id,
            session.scoring.scorer_formula,
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


# ---------------------------------------------------------------------------
# Stale data protocol — degradation rerun / samplescan / sampleswitch
# ---------------------------------------------------------------------------


def find_gt_rank(result: Mapping[str, Any]) -> int | None:
    """Find ground truth rank in final_ranking. Returns 1-indexed or None."""
    gt = result.get("ground_truth", "")
    if not gt:
        return None
    pd = result.get("pipeline_data") or {}
    return find_rank(pd.get("final_ranking", []), gt)


def compare_rerun(cached_result: Mapping[str, Any], rerun_result: Mapping[str, Any]) -> dict:
    """Compare rerun to cached result. Returns improvement summary."""
    cached_hit = cached_result.get("hit", False)
    rerun_hit = rerun_result.get("hit", False)
    hit_change = f"{'HIT' if cached_hit else 'MISS'}->{'HIT' if rerun_hit else 'MISS'}"

    cached_rank = find_gt_rank(cached_result)
    rerun_rank = find_gt_rank(rerun_result)
    rank_change = (
        f"{cached_rank}->{rerun_rank}"
        if cached_rank is not None and rerun_rank is not None
        else None
    )

    improved = (not cached_hit and rerun_hit) or (
        cached_rank is not None and rerun_rank is not None and rerun_rank < cached_rank
    )

    return {"hit_change": hit_change, "rank_change": rank_change, "improved": improved}


async def execute_stale_data_protocol(
    protocol_steps: list[str],
    sample: Sample,
    cached_result: dict[str, Any],
    session: Session,
    *,
    pipeline_params: dict | None = None,
    axes: AxisIndex | None = None,
) -> tuple[dict[str, Any], str]:
    """Walk the stale data load protocol ladder for a degraded cached query.

    Hyperparameters are read from the ``l1_score`` node config in
    ``optimizer_pipeline.json``.  The protocol step list and all thresholds
    live on that single node — tunable via ``param_keys`` when PromptPotter
    self-optimizes.

    Observation counts come from ``axes.sample_index`` (degradation
    tables ingested from the measurement archive at round boundaries). Within a round
    the count is constant; no mutable state is passed through the scorer.

    Returns ``(result_dict, step_taken)`` where *step_taken* is the step
    that resolved the query, or ``"exhausted"``.
    """
    from promptpotter.application.optimization.llm_call import get_optimizer_schema

    node = get_optimizer_schema().get_node("l1_score")
    assert node is not None, "l1_score node missing from optimizer schema"
    cfg = node.current_config
    result = cached_result

    for step in protocol_steps:
        if session.stop_check and session.stop_check():
            return {**result, "cached": result.get("cached", False)}, "interrupted"
        if step == "rerun":
            trigger_count = cfg.get("rerun_trigger_count", 3)
            historical = axes.sample_index.degradation_count(sample.id) if axes else 0
            effective_count = historical + 1
            if effective_count < trigger_count:
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": effective_count,
                    "degraded_obs_threshold": trigger_count,
                }, "below_threshold"

            result = dict(await measure_sample(sample, session, pipeline_params=pipeline_params))
            result["retry_of_degraded"] = True
            result["rerun_comparison"] = compare_rerun(cached_result, result)
            if not is_degraded(result):
                return result, "rerun"

        elif step == "samplescan":
            n_candidates = cfg.get("samplescan_candidates", 3)
            resolved_threshold = cfg.get("samplescan_threshold", 0.5)
            probe_params = (
                session.pipeline_schema.to_pipeline_params()
                if session.pipeline_schema is not None
                else {}
            )
            result = dict(await measure_sample(sample, session, pipeline_params=probe_params))
            result["samplescan_resolved"] = True
            result["samplescan_config"] = {
                "n_candidates": n_candidates,
                "resolved_threshold": resolved_threshold,
            }
            if not is_degraded(result):
                return result, "samplescan"

        elif step == "sampleswitch":
            min_deg_rate = cfg.get("sampleswitch_min_degradation_rate", 0.5)
            if axes and axes.sample_index.degradation_rate(sample.id) >= min_deg_rate:
                result = {**cached_result, "cached": True, "switched_out": True}
                return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"
