"""Single-sample pipeline execution + scoring; {{var}} interpolation excludes ground_truth."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

import httpx

from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.scoring.metrics import find_rank
from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryMeasurement, is_hit
from promptpotter.infrastructure.llm.models import emit_token_usage
from promptpotter.shared.errors import ErrorCategory, has_pipeline_warnings

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stale-data recovery ladder
# ---------------------------------------------------------------------------

STALE_DATA_LOAD_PROTOCOL: tuple[str, ...] = ("rerun", "samplescan", "sampleswitch")
"""Step order for handling a degraded cached query. ``execute_stale_data_protocol``
walks this in order; first step that returns a non-degraded result wins."""

RERUN_TRIGGER_COUNT: int = 3
"""Number of degradation sightings (cached + historical) before rerunning."""

SAMPLESWITCH_MIN_DEGRADATION_RATE: float = 0.5
"""Historical degradation rate at which sampleswitch short-circuits to the
cached deprecated answer instead of re-evaluating."""

# ---------------------------------------------------------------------------
# Prompt template interpolation
# ---------------------------------------------------------------------------

# TARGET-prompt interpolation — the `{{var}}` slots a dataset row fills on its way
# to the backend. NOT the dispatch-hub `INJECTIONS` registry, which fills `{{slot}}`s
# in the OPTIMIZER's meta-prompts. Same syntax, two populations, two regexes (the
# other is `dispatch/facade.py`); adding a signal for an L1/L2/L3 prompt goes
# there, never here.
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
    """Replace {{key}} from variables; skip _EXCLUDED_FIELDS; missing keys left as-is."""
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
# ``reasoning_trace`` = the task model's chain-of-thought (head-capped at the
# backend); the critique tier reads it to diagnose WHERE a deduction broke.
_INFRA_KEYS: frozenset[str] = frozenset(
    {
        "step_timings",
        "step_tokens",
        "llm_provider",
        "total_time",
        "pipeline_params",
        "diagnostics",
        "reasoning_trace",
    }
)


class StepTokenUsage(TypedDict):
    """Per-LLM-node ``step_tokens`` entry. The ``NotRequired`` keys are forwarded
    from the backend wire only when the provider surfaced them (TermNorm
    ``record_step_tokens``); absent on estimated-fallback entries. ``finish_reason``
    + ``reasoning`` are the raw response shape ``classify_result`` reads to tell a
    truncation (route to infra, don't fast-eliminate at n=1) from a genuinely empty
    response (fatal)."""

    input: int
    output: int
    estimated: bool
    cost_usd: NotRequired[float]
    model: NotRequired[str]
    finish_reason: NotRequired[str]
    reasoning: NotRequired[int]


def emit_step_token_usage(
    step_tokens: Mapping[str, StepTokenUsage],
    step_timings: Mapping[str, Any],
    *,
    cached: bool,
) -> None:
    """Fan one sample's per-node backend token usage onto the canonical ledger.

    Called on BOTH measurement paths: a fresh backend call (``cached=False``) and a
    measurement-cache hit (``cached=True``, metered from the archived row's own
    ``step_tokens``, which survive ``_materialize_cached`` intact). A hit spent no
    money but the search still made the call, and only metering the misses would make
    an outer arm's cost depend on our cache history rather than on the arm.
    """
    for node_name, entry in step_tokens.items():
        in_tok = entry["input"]
        out_tok = entry["output"]
        cost_usd = entry.get("cost_usd")
        # Skip a wholly-empty step, but never one that still carries a fixed/per-call
        # cost (spend is the headline) — that would silently drop cost from the ledger.
        if in_tok == 0 and out_tok == 0 and not cost_usd:
            continue
        raw_dur = step_timings.get(node_name)
        emit_token_usage(
            node=str(node_name),
            kind="backend",
            input_tokens=in_tok,
            output_tokens=out_tok,
            duration_s=float(raw_dur) if isinstance(raw_dur, (int, float)) else 0.0,
            model=entry.get("model"),
            cost_usd=cost_usd,
            cached=cached,
        )


def _compute_step_tokens(
    resp_data: dict[str, Any],
    pipeline_schema: PipelineSchema,
    wire_params: dict[str, Any],
) -> dict[str, StepTokenUsage]:
    """Collect per-LLM-node token counts from the backend response.

    Seeds from ``resp_data["step_tokens"]`` when the backend provides it
    (marks each entry ``estimated=False``). For any LLM node still missing
    a count, falls back to a chars/4 heuristic over the interpolated prompt
    and the node's observed output text (marks ``estimated=True``).

    Every entry carries the node's ``model``: the backend's upstream id when it
    reports one, else the model the overlay pinned for that node
    (``wire_params[node]["model"]`` — the dataset OWNS its task model, so this is
    never absent for an LLM node). Downstream reads ``entry["model"]`` directly.

    Returns an empty dict when the schema carries no LLM nodes.
    """
    out: dict[str, StepTokenUsage] = {}

    def _configured_model(node_name: str) -> str | None:
        cfg = wire_params.get(node_name)
        model = cfg.get("model") if isinstance(cfg, dict) else None
        return model if isinstance(model, str) else None

    raw = resp_data.get("step_tokens")
    if isinstance(raw, dict):
        for node_name, entry in raw.items():
            if isinstance(entry, dict):
                seeded: StepTokenUsage = {
                    "input": int(entry.get("input", 0)),
                    "output": int(entry.get("output", 0)),
                    "estimated": False,
                }
                # Forward per-node cost/model when the backend surfaced them
                # (provider returned USD; upstream model id) — the dashboard
                # prefers these over its bundled rate table.
                cost = entry.get("cost_usd")
                if isinstance(cost, (int, float)):
                    seeded["cost_usd"] = float(cost)
                wire_model = entry.get("model")
                model = wire_model if isinstance(wire_model, str) else _configured_model(node_name)
                if model is not None:
                    seeded["model"] = model
                # Forward the raw response shape so ``classify_result`` can
                # distinguish a truncation (``finish_reason=length``) from a
                # genuinely empty response. Dropping these collapses every
                # empty terminal onto the fatal ``empty_response`` arm.
                fr = entry.get("finish_reason")
                if isinstance(fr, str):
                    seeded["finish_reason"] = fr
                reasoning = entry.get("reasoning")
                if isinstance(reasoning, (int, float)) and not isinstance(reasoning, bool):
                    seeded["reasoning"] = int(reasoning)
                out[node_name] = seeded

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

        estimated: StepTokenUsage = {
            "input": len(in_text) // 4,
            "output": len(out_text) // 4,
            "estimated": True,
        }
        model = _configured_model(node.name)
        if model is not None:
            estimated["model"] = model
        out[node.name] = estimated

    return out


def _error_result(
    sample: Sample,
    error_msg: str,
    *,
    category: ErrorCategory,
) -> QueryMeasurement:
    """Build a standard error result dict.

    ``category`` is the typed error channel — it owns "this sample errored"
    (``is_error_result`` reads it); ``error`` is the plain human message.
    Error rows intentionally carry no ``hit``/``score`` — those fields are
    owned exclusively by ``rescore_results``, which skips error rows.
    """
    return QueryMeasurement(
        sample_id=sample.id,
        query=sample.query,
        ground_truth=sample.ground_truth,
        predicted="ERROR",
        cached=False,
        error=error_msg or "unknown error",
        error_category=category,
        pipeline_data=None,
    )


def _extract_upstream_detail(exc: httpx.HTTPStatusError) -> str:
    """Pull the structured upstream summary out of a backend error response.

    Backends that propagate provider 4xx errors send a JSON body of shape::

        {"detail": {"upstream_status": 400, "upstream_provider": "openrouter",
                    "upstream_model": "...", "upstream_message": "...",
                    "error_code": "..."}}

    (FastAPI's ``HTTPException(detail=dict)`` serializes as ``detail`` key.)
    When that shape is present, return a compact one-liner — the operator
    needs to see what the upstream provider actually complained about (e.g.
    ``"temperature: Invalid input: expected number, received string"``)
    instead of a bare ``'502 Bad Gateway'``. Falls back to a truncated body
    when the response isn't structured.
    """
    body_text = (exc.response.text or "").strip()
    if not body_text:
        return ""
    try:
        body = exc.response.json()
    except Exception:
        return body_text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        provider = detail.get("upstream_provider") or "?"
        model = detail.get("upstream_model") or "?"
        msg = detail.get("upstream_message") or detail.get("error_code") or "?"
        ustatus = detail.get("upstream_status")
        prefix = f"upstream={provider}:{model}"
        if ustatus is not None:
            prefix = f"{prefix} {ustatus}"
        return f"{prefix} :: {msg}"
    if isinstance(detail, str):
        return detail[:300]
    return body_text[:300]


def _classify_http_error(exc: httpx.HTTPStatusError) -> tuple[ErrorCategory, str]:
    """Classify an HTTP error into ``(category, plain message)``."""
    code = exc.response.status_code
    upstream = _extract_upstream_detail(exc)
    if code == 429:
        retry_after = exc.response.headers.get("Retry-After", "?")
        return (
            ErrorCategory.CLIENT,
            f"HTTP 429 rate-limited (Retry-After={retry_after}s, attempts exhausted): {upstream!r}",
        )
    if 400 <= code < 500:
        tail = f" :: {upstream}" if upstream else ""
        return ErrorCategory.CLIENT, f"HTTP {code} — caller config rejected by backend{tail}"
    tail = f" :: {upstream}" if upstream else ""
    return ErrorCategory.SERVER, f"HTTP {code} — backend transient error{tail}"


async def measure_sample(
    sample: Sample,
    session: Session,
    pipeline_params: dict[str, Any] | None = None,
) -> QueryMeasurement:
    query = sample.query
    ground_truth = sample.ground_truth

    pipeline_schema = session.pipeline_schema
    if pipeline_schema is None:
        from promptpotter.domain.pipeline_schema import PipelineSchema

        pipeline_schema = PipelineSchema()

    try:
        wire_params = interpolate_pipeline_params(pipeline_params or {}, sample.model_dump())

        def _emit_backend_warning(payload: dict[str, Any]) -> None:
            # Emit a typed ledger record so the dashboard projection bumps its
            # backend-retry counter and recent-warnings list, and the audit
            # archive records what the operator was waiting on. The retry
            # itself is unchanged — this is pure visibility.
            ledger = session.state.ledger
            if ledger is None:
                return
            from promptpotter.domain.run_records import PhaseRecord

            try:
                ledger.append(
                    PhaseRecord(
                        phase="backend",
                        event="warning",
                        payload={**payload, "query": query[:80]},
                    )
                )
            except Exception:
                logger.exception("backend warning ledger emit failed; continuing")

        from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
        from promptpotter.infrastructure.llm.models import _CURRENT_ROUND

        ledger = session.state.ledger
        heartbeat_task: asyncio.Task[None] | None = None
        if ledger is not None:
            heartbeat_task = asyncio.create_task(
                heartbeat(
                    ledger,
                    call_id=f"scoring:{sample.id}",
                    node="backend_scoring",
                    round_num=_CURRENT_ROUND.get(),
                    start_monotonic=time.monotonic(),
                )
            )
        try:
            resp = await session.backend_client.run_query(
                query, pipeline_params=wire_params, on_warning=_emit_backend_warning
            )
        finally:
            # Cancel the heartbeat whether the query succeeded or raised —
            # an in-flight task would otherwise survive and keep appending
            # progress records against a closed call.
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning(
                        "heartbeat task for backend scoring raised on teardown",
                        exc_info=True,
                    )
        data = resp.get("data", {})

        # The prediction is the head of the TERMINAL ranker's output — candidate_ranking
        # when token_matching is terminal, final_ranking when an llm_ranking/llm_only node
        # is — read through the schema, not a hardcoded key. extract_item_label is
        # shape-agnostic (dict {candidate}, (name, score) tuple, or bare string).
        from promptpotter.application.optimization.pobb.classification import (
            terminal_ranking,
        )
        from promptpotter.application.scoring.formula import extract_item_label

        ranked = terminal_ranking({"pipeline_data": data}, pipeline_schema)
        predicted = extract_item_label(ranked[0]) if ranked else NO_RESULT
        if predicted == "ERROR":
            return _error_result(
                sample,
                "Backend returned ERROR as candidate — pipeline internal failure for this query.",
                category=ErrorCategory.PIPELINE,
            )
        gt_rank = find_rank(ranked, ground_truth)

        # Project wire response → pipeline_data. `result_ranking` is the canonical derived
        # terminal ranking the scorer + find_gt_rank read; the raw per-node observation keys
        # (candidate_ranking / final_ranking) are copied below for their per-node diagnostics.
        pd: dict[str, Any] = {"result_ranking": ranked}
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
            # measure_sample only reaches here for fresh backend calls (cache hits
            # return early and meter themselves from the archived row), so this
            # never double-counts.
            emit_step_token_usage(step_tokens, data.get("step_timings") or {}, cached=False)

        result: dict[str, Any] = {
            "sample_id": sample.id,
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "cached": False,
            "error": None,
            "error_category": None,
            "n_candidates": len(ranked),
            "ground_truth_rank": gt_rank,
            "pipeline_data": pd,
        }
        # Populate per-sample evaluator values.
        from promptpotter.application.scoring.evaluators import materialize_sample_values

        sample_evaluators = materialize_sample_values(pipeline_schema, result)  # type: ignore[arg-type]
        if sample_evaluators:
            pd["evaluators"] = sample_evaluators
        from promptpotter.application.scoring.formula import rescore_results

        assert session.scoring.scorer is not None, "session.scoring.scorer required for measurement"
        rescore_results(
            [result],
            session.scoring.scorer,
            session.scoring.scorer_id,
            session.scoring.scorer_formula,
        )
        return result  # type: ignore[return-value]
    except httpx.HTTPStatusError as exc:
        category, error_msg = _classify_http_error(exc)
        logger.warning("measure_sample for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=category)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"{exc} — Backend may be down or unreachable."
        logger.warning("measure_sample CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=ErrorCategory.CONNECTION)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("measure_sample failed for %s: %s", query[:60], exc)
        return _error_result(sample, str(exc), category=ErrorCategory.UNKNOWN)


# ---------------------------------------------------------------------------
# Stale data protocol — degradation rerun / samplescan / sampleswitch
# ---------------------------------------------------------------------------


def find_gt_rank(result: Mapping[str, Any]) -> int | None:
    """Find ground truth rank in the terminal ranking. Returns 1-indexed or None."""
    gt = result.get("ground_truth", "")
    if not gt:
        return None
    pd = result.get("pipeline_data") or {}
    return find_rank(pd.get("result_ranking", []), gt)


def compare_rerun(
    cached_result: Mapping[str, Any], rerun_result: Mapping[str, Any]
) -> dict[str, Any]:
    cached_hit = is_hit(cached_result.get("fitness"))
    rerun_hit = is_hit(rerun_result.get("fitness"))
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


def _rerun_would_repeat_token_budget_failure(
    cached_result: Mapping[str, Any],
    rerun_pipeline_params: dict[str, Any] | None,
) -> bool:
    """Short-circuit gate: skip the rerun branch when the cached failure was
    a token-budget exhaustion (``finish_reason=length``) AND the rerun's
    ``llm_only.max_tokens`` is no larger than the cached run's actual
    output token count.

    Rationale: when the LLM emits exactly its ``max_tokens`` budget and
    still finishes ``length`` (with reasoning tokens > 0, or just truncated
    output), the cap was binding. A rerun at the same-or-tighter budget
    will exhaust at least as fast — burning another LLM call for the
    same DEPR result. The stale-data ladder is built for *transient*
    failures (rate-limit flake, model glitch); config-fundamental
    failures don't recover and don't deserve the retry budget. The
    ``samplescan`` branch is not gated because it runs with the schema
    defaults, which can carry a larger ``max_tokens`` than the
    candidate's pinch.
    """
    from promptpotter.domain.rendering import classify_result

    # The terminal LLM node (llm_only single-node, llm_ranking multi-node) is read
    # from the cached result itself, so the infra-code / token lookups key on the SAME
    # node classify_result stamped. The trailing `or "llm_only"` IS a literal coupling
    # to the single-node sentinel — it fires when the row carries no `terminated_at`.
    # "llm_only" names ONE thing now — the single-node pipeline sentinel. The connector
    # of that name was deleted, so the collision this comment used to warn about is gone;
    # folding the sentinel into a declared constant is an open design call, not an
    # absence of coupling.
    node = ((cached_result.get("pipeline_data") or {}).get("terminated_at")) or "llm_only"
    cl = classify_result(cached_result)
    budget_exhausted = (
        f"{node}:reasoning_budget_exhausted" in cl.infra_codes
        or f"{node}:output_truncated" in cl.infra_codes
    )
    if not budget_exhausted:
        return False

    cached_step = ((cached_result.get("pipeline_data") or {}).get("step_tokens") or {}).get(
        node
    ) or {}
    cached_completion = int(cached_step.get("output", 0))
    if cached_completion <= 0:
        # No reliable cap signal — be conservative and let the rerun happen.
        return False

    rerun_max_tokens = ((rerun_pipeline_params or {}).get(node) or {}).get("max_tokens")
    if rerun_max_tokens is None:
        # No explicit override in the rerun — runs at backend default which
        # could be larger or smaller than the cached cap. Conservative: don't
        # skip; let the rerun run and see.
        return False

    return int(rerun_max_tokens) <= cached_completion


async def execute_stale_data_protocol(
    protocol_steps: list[str],
    sample: Sample,
    cached_result: dict[str, Any],
    session: Session,
    *,
    pipeline_params: dict[str, Any] | None = None,
    axes: AxisIndex | None = None,
) -> tuple[dict[str, Any], str]:
    """Walk the stale data load protocol ladder for a degraded cached query.

    Hyperparameters are module-level constants
    (``RERUN_TRIGGER_COUNT``, ``SAMPLESWITCH_MIN_DEGRADATION_RATE``).

    Observation counts come from ``axes.sample_index`` (degradation
    tables ingested from the measurement archive at round boundaries). Within a round
    the count is constant; no mutable state is passed through the scorer.

    Returns ``(result_dict, step_taken)`` where *step_taken* is the step
    that resolved the query, or ``"exhausted"``.
    """
    result = cached_result

    for step in protocol_steps:
        if pause_requested(session):
            declare_run_phase(session, RunPhase.PAUSED)
            return {**result, "cached": result.get("cached", False)}, "paused"
        if step == "rerun":
            historical = axes.sample_index.degradation_count(sample.id) if axes else 0
            effective_count = historical + 1
            if effective_count < RERUN_TRIGGER_COUNT:
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": effective_count,
                    "degraded_obs_threshold": RERUN_TRIGGER_COUNT,
                }, "below_threshold"

            if _rerun_would_repeat_token_budget_failure(cached_result, pipeline_params):
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "config_fundamental_skip": True,
                    "skip_reason": "rerun_max_tokens_le_cached_completion",
                }, "skipped_config_fundamental"

            result = dict(await measure_sample(sample, session, pipeline_params=pipeline_params))
            result["retry_of_degraded"] = True
            result["rerun_comparison"] = compare_rerun(cached_result, result)
            if not has_pipeline_warnings(result):
                return result, "rerun"

        elif step == "samplescan":
            probe_params = (
                session.pipeline_schema.to_pipeline_params()
                if session.pipeline_schema is not None
                else {}
            )
            result = dict(await measure_sample(sample, session, pipeline_params=probe_params))
            result["samplescan_resolved"] = True
            if not has_pipeline_warnings(result):
                return result, "samplescan"

        elif step == "sampleswitch":
            if (
                axes
                and axes.sample_index.degradation_rate(sample.id)
                >= SAMPLESWITCH_MIN_DEGRADATION_RATE
            ):
                result = {**cached_result, "cached": True, "switched_out": True}
                return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"
