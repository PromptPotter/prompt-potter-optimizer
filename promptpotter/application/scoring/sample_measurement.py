from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.classification import terminal_ranking
from promptpotter.application.scoring.evaluators import materialize_sample_values
from promptpotter.application.scoring.row_diagnostics import rank_ground_truth
from promptpotter.domain.l4.proxies import (
    INNER_FACT_KEYS,
    PARENT_LEVEL_SE_KEY,
)
from promptpotter.domain.pipeline_schema import LLMSpendBound, WebSpendBound
from promptpotter.domain.results_health import classify_result, is_deprecated, terminal_node
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import (
    NO_RESULT,
    PIPELINE_KEYS,
    MeasuredCell,
    PipelineData,
    RerunComparison,
    Scorer,
    extract_item_label,
    turn_scalars,
)
from promptpotter.domain.spend import StepUsage, TokenAccount
from promptpotter.infrastructure.llm.base import hold_ceiling
from promptpotter.infrastructure.llm.heartbeat import heartbeat
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.spend_book import (
    FRAMING_TOKENS,
    Billed,
    Reported,
    SendBound,
)
from promptpotter.infrastructure.llm.telemetry import (
    _CURRENT_ROUND,
    emit_token_usage,
    rate_priced_usd,
)
from promptpotter.shared.errors import CellUnscoreableError, ErrorCategory, SendRefusedError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.domain.pipeline_schema import NodeSpendBound, PipelineNode, PipelineSchema
    from promptpotter.infrastructure.backend import CellBilling

logger = logging.getLogger(__name__)

STALE_DATA_LOAD_PROTOCOL: tuple[str, ...] = ("rerun", "sampleswitch")

RERUN_TRIGGER_COUNT: int = 3

SAMPLESWITCH_MIN_DEGRADATION_RATE: float = 0.5

# TARGET-prompt `{{var}}` slots a dataset row fills; the OPTIMIZER's `{{slot}}`s are `dispatch/facade.py`'s.
_TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# Never interpolated into a prompt: answer leakage.
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "ground_truth",
        "fitness",
        "error",
        "source_sheet",
    }
)


def interpolate_prompt(text: str, variables: dict[str, Any]) -> str:
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


__all__ = ["execute_stale_data_protocol", "measure_sample"]

# Kept whatever the schema declares; ``pipeline_params`` is NOT here: the archive lifts it onto the index entry.
_INFRA_KEYS: frozenset[str] = frozenset(
    {
        "total_time",
        "diagnostics",
        "reasoning_trace",
        "turns",
        "outcome_note",
        "step_phases",
        # An infra key, not a declared observation: the panel reads it and the formula must not.
        PARENT_LEVEL_SE_KEY,
        *INNER_FACT_KEYS,
    }
)
assert _INFRA_KEYS <= PIPELINE_KEYS, (
    "an infra key PipelineData does not declare is filed as a dataset observation"
)

_SPEND_KEYS = ("step_tokens", "step_timings")


PricedPair = tuple[str | None, str | None]
"""The ``(model, provider)`` one node's sends are priced as (``BackendClient.priced_as``)."""

_UNROUTED: PricedPair = (None, None)


def priced_pairs(session: Session, wire_params: Mapping[str, Any]) -> dict[str, PricedPair]:
    priced_as = session.backend_client.priced_as
    return {name: priced_as(cfg) for name, cfg in wire_params.items() if isinstance(cfg, Mapping)}


def _reply_spend(data: Mapping[str, Any], pairs: Mapping[str, PricedPair]) -> PipelineData:
    spent = PipelineData.from_wire({key: data.get(key) for key in _SPEND_KEYS})
    return replace(spent, step_tokens=_reported_usage(spent.step_tokens, pairs))


def _step_parts(
    step_tokens: Mapping[str, StepUsage], step_timings: Mapping[str, float]
) -> list[Billed]:
    return [
        Billed(
            usage.account,
            usage.cost_usd,
            served_by=usage.served_by,
            model=usage.model,
            node=node_name,
            provider=usage.provider,
            duration_s=step_timings.get(node_name, 0.0),
            rate_priced_usd=usage.rate_priced_usd,
        )
        for node_name, usage in step_tokens.items()
        if usage.input or usage.output or usage.cost_usd
    ]


def emit_replayed_step_tokens(
    step_tokens: Mapping[str, StepUsage], step_timings: Mapping[str, float]
) -> None:
    """A replay states what the row RECORDED: its reported cost, else the rate stamped when measured."""
    for part in _step_parts(step_tokens, step_timings):
        emit_token_usage(
            node=part.node or "backend",
            kind="backend",
            usage=part.usage,
            duration_s=part.duration_s or 0.0,
            model=part.model,
            provider=part.provider,
            served_by=part.served_by,
            cost_usd=part.cost_usd,
            recorded_rate_usd=part.rate_priced_usd,
            cached=True,
        )


def _ran_last(pipeline_schema: PipelineSchema, ran: Mapping[str, Any]) -> str | None:
    reached = [node.name for node in pipeline_schema.nodes if ran.get(node.name) is not None]
    return reached[-1] if reached else None


def _uncounted_attempts(
    reported: Mapping[str, StepUsage], each: Mapping[str, SendBound], *, refused_on: str | None
) -> SendBound | None:
    """A node's usage accounts for ONE attempt; nothing says the retried others billed nothing."""
    input_tokens = output_tokens = 0
    usd: float | None = 0.0
    for name, entry in reported.items():
        bound = each.get(name)
        if bound is None or entry.attempts is None:
            continue
        priced = bool(entry.input or entry.output or entry.cost_usd)
        left = entry.attempts - priced - (name == refused_on)
        if left <= 0:
            continue
        input_tokens += left * bound.input_tokens
        output_tokens += left * (bound.output_tokens or 0)
        usd = None if usd is None or bound.usd is None else usd + left * bound.usd
    if not (input_tokens or output_tokens):
        return None
    return SendBound(input_tokens=input_tokens, output_tokens=output_tokens, usd=usd)


def cell_billing(
    pipeline_schema: PipelineSchema,
    pairs: Mapping[str, PricedPair],
    each: Mapping[str, SendBound],
) -> CellBilling[PipelineData]:
    """``None`` where the reply reports no usage at all, which is charged the cell's whole bound."""

    def billed(data: dict[str, Any], refused: bool) -> tuple[Reported | None, PipelineData]:
        spent = _reply_spend(data, pairs)
        if not isinstance(data.get("step_tokens"), dict):
            return None, spent
        reported = spent.step_tokens
        parts = _step_parts(reported, spent.step_timings)
        web = data.get("web_cost")
        usd = web.get("usd") if isinstance(web, dict) else None
        if isinstance(usd, int | float) and not isinstance(usd, bool) and usd:
            searched = next(
                (n.name for n in pipeline_schema.nodes if isinstance(n.spend_bound, WebSpendBound)),
                "web_search",
            )
            parts.append(Billed(TokenAccount(), float(usd), node=searched))
        refused_on = _ran_last(pipeline_schema, reported) if refused else None
        unknown = _uncounted_attempts(reported, each, refused_on=refused_on)
        return Reported(tuple(parts), unknown), spent

    return billed


async def _attempt_bound(
    spend: LLMSpendBound, cfg: Mapping[str, Any], priced_as: PricedPair
) -> SendBound:
    reply = int(cfg.get("max_tokens") or spend.max_tokens)
    reads = spend.input_tokens + FRAMING_TOKENS
    model, provider = priced_as
    ceiling = await hold_ceiling(model, provider, hosts=spend.hosts) if model and provider else None
    if ceiling is None:
        return SendBound(
            input_tokens=reads,
            output_tokens=reply,
            usd=None,
            unpriced=(f"{model or 'no model'} ({provider or 'no provider'})",),
        )
    tier = ceiling.at(reads)
    usd = ceiling.per_request + reads * tier.input + reply * tier.output
    return SendBound(input_tokens=reads, output_tokens=reply, usd=usd)


def _node_spends(
    session: Session, wire_params: Mapping[str, Any]
) -> list[tuple[PipelineNode, Mapping[str, Any], NodeSpendBound | None]]:
    client = session.backend_client
    spends = []
    for node in session.pipeline_schema.nodes:
        raw_cfg = wire_params.get(node.name)
        cfg = raw_cfg if isinstance(raw_cfg, Mapping) else {}
        spends.append((node, cfg, client.node_spend_bound(node, cfg)))
    return spends


async def attempt_bounds(session: Session, wire_params: Mapping[str, Any]) -> dict[str, SendBound]:
    priced_as = session.backend_client.priced_as
    return {
        node.name: await _attempt_bound(spend, cfg, priced_as(cfg))
        for node, cfg, spend in _node_spends(session, wire_params)
        if isinstance(spend, LLMSpendBound)
    }


async def cell_bound(session: Session, wire_params: Mapping[str, Any]) -> SendBound | None:
    """``None`` where the backend's own sends are each admitted; a node with no served bound leaves it UNBOUNDED."""
    client = session.backend_client
    if client.holds_own_sends and not client.derives_spend_bounds:
        return None
    usd = 0.0
    input_tokens = output_tokens = 0
    bounded = True
    unpriced: tuple[str, ...] = ()
    served = False
    for node, cfg, spend in _node_spends(session, wire_params):
        if spend is None:
            bounded = bounded and not node.is_llm
            continue
        served = True
        if isinstance(spend, WebSpendBound):
            usd += spend.queries * spend.usd_per_query
            continue
        each = await _attempt_bound(spend, cfg, client.priced_as(cfg))
        input_tokens += spend.attempts * each.input_tokens
        output_tokens += spend.attempts * (each.output_tokens or 0)
        if each.usd is None:
            unpriced += tuple(name for name in each.unpriced if name not in unpriced)
        else:
            usd += spend.attempts * each.usd
    if not (served and bounded):
        return SendBound(input_tokens=input_tokens, output_tokens=None, usd=None, unpriced=unpriced)
    return SendBound(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=None if unpriced else usd,
        unpriced=unpriced,
    )


def _compute_step_tokens(
    reported: Mapping[str, StepUsage],
    resp_data: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
    wire_params: Mapping[str, Any],
    pairs: Mapping[str, PricedPair],
) -> dict[str, StepUsage]:
    out = dict(reported)
    for node in pipeline_schema.nodes:
        if node.is_llm and node.name not in out:
            out[node.name] = _estimated_usage(
                node, resp_data, wire_params, pairs.get(node.name, _UNROUTED)
            )
    return out


def _reported_usage(
    reported: Mapping[str, StepUsage], pairs: Mapping[str, PricedPair]
) -> dict[str, StepUsage]:
    return {
        node_name: _at_our_rate(
            replace(
                usage,
                estimated=False,
                model=usage.model or pairs.get(node_name, _UNROUTED)[0],
                # The provider we configured billed us, never the wire's, which may answer a slug.
                provider=pairs.get(node_name, _UNROUTED)[1],
            )
        )
        for node_name, usage in reported.items()
    }


def _at_our_rate(usage: StepUsage) -> StepUsage:
    return replace(
        usage,
        rate_priced_usd=rate_priced_usd(
            usage.account,
            model=usage.model,
            provider=usage.provider,
            cost_usd=usage.cost_usd,
            recorded=usage.rate_priced_usd,
        ),
    )


def _estimated_usage(
    node: PipelineNode,
    resp_data: Mapping[str, Any],
    wire_params: Mapping[str, Any],
    priced_as: PricedPair,
) -> StepUsage:
    node_cfg = wire_params.get(node.name) or {}
    in_text = (node_cfg.get("prompt") if isinstance(node_cfg, dict) else None) or ""
    out_text = " ".join(_observed_texts(node, resp_data))
    model, provider = priced_as
    return StepUsage(
        input=len(in_text) // 4,
        output=len(out_text) // 4,
        estimated=True,
        model=model,
        provider=provider,
    )


def _observed_texts(node: PipelineNode, resp_data: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for mapping in node.observation_mappings:
        value = resp_data.get(mapping.pipeline_key) if mapping.is_llm else None
        if value is None:
            continue
        field = mapping.output_field
        if field and isinstance(value, dict):
            texts.append(str(value.get(field, "")))
        elif field and isinstance(value, list):
            texts.extend(
                str(item[field]) for item in value if isinstance(item, dict) and field in item
            )
        else:
            texts.append(str(value))
    return texts


def _error_result(
    sample: Sample,
    error_msg: str,
    *,
    category: ErrorCategory,
) -> MeasuredCell:
    return MeasuredCell(
        sample_id=sample.id,
        sample_key=sample.key,
        query=sample.query,
        ground_truth=sample.ground_truth or "",
        predicted="ERROR",
        error=error_msg or "unknown error",
        error_category=category,
    )


def _extract_upstream_detail(exc: httpx.HTTPStatusError) -> str:
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


# The model ANSWERED and left nothing gradeable: a measured MISS of this configuration, never a hole.
_OUTPUT_FAILURE_CODES = frozenset(
    {"json_parse_failed", "schema_validation_failed", "output_truncated"}
)


def _answered_nothing(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    try:
        detail = exc.response.json().get("detail")
    except Exception:
        return None
    if not isinstance(detail, dict) or detail.get("error_code") not in _OUTPUT_FAILURE_CODES:
        return None
    return {"step_tokens": detail.get("step_tokens") or {}}


_HTTP_FAILURE_WORDS: dict[ErrorCategory, str] = {
    ErrorCategory.PROVIDER_CREDIT: "provider credit refused",
    ErrorCategory.PROVIDER_THROTTLED: "provider throttled",
    ErrorCategory.CLIENT: "caller config rejected by backend",
    ErrorCategory.SERVER: "backend transient error",
}


def _without_identity(
    pipeline_params: dict[str, Any], identity_keys: Mapping[str, frozenset[str]]
) -> dict[str, Any]:
    return {
        node: {k: v for k, v in cfg.items() if k not in owned}
        if (owned := identity_keys.get(node)) and isinstance(cfg, dict)
        else cfg
        for node, cfg in pipeline_params.items()
    }


async def measure_sample(
    sample: Sample,
    session: Session,
    pipeline_params: dict[str, Any] | None = None,
) -> MeasuredCell:
    """One cell's FACTS — ungraded; the walk grades every row it takes, fresh or replayed."""
    # A verifier-graded cell carries `""`, which no answer matches.
    ground_truth = sample.ground_truth or ""
    pipeline_schema = session.pipeline_schema
    try:
        wire_params = interpolate_pipeline_params(
            _without_identity(pipeline_params or {}, session.identity_keys), sample.model_dump()
        )
        pairs = priced_pairs(session, wire_params)
        data, spent, envelope = await _send_cell(sample, session, wire_params, pairs)

        ranked = terminal_ranking(data, pipeline_schema)
        predicted = _predicted(data, ranked, session.backend_client.answer_key)
        if predicted == "ERROR":
            return _error_result(
                sample,
                "Backend returned ERROR as candidate — pipeline internal failure for this query.",
                category=ErrorCategory.PIPELINE,
            )
        gt_rank, n_candidates = rank_ground_truth(ranked, predicted, ground_truth)

        pd = _observations(data, ranked, pipeline_schema, spent)
        # The envelope's final reading, taken AFTER its scope closed.
        if envelope.budget_s is not None:
            pd["unworked_s"] = envelope.unworked
        pd.update(_candidate_and_sample_facts(sample, pipeline_schema, pipeline_params or {}))
        result = MeasuredCell(
            sample_id=sample.id,
            sample_key=sample.key,
            query=sample.query,
            ground_truth=ground_truth,
            predicted=predicted,
            pipeline=replace(
                PipelineData.from_wire(pd),
                step_timings=spent.step_timings,
                step_tokens=_compute_step_tokens(
                    spent.step_tokens, data, pipeline_schema, wire_params, pairs
                ),
            ),
            ground_truth_rank=gt_rank,
            n_candidates=n_candidates,
        )

        # Banked HERE and top-level: a replay never re-enters this function, and a formula names no nested value.
        terms = await materialize_sample_values(
            pipeline_schema, result, extra=session.scoring.judges
        )
        if declared := PIPELINE_KEYS & terms.values.keys():
            raise ValueError(
                f"evaluator terms {sorted(declared)} carry the names of `PipelineData` fields: "
                "banked beside them, each would be read back as the field"
            )
        return replace(
            result,
            pipeline=replace(
                result.pipeline,
                observations={**result.pipeline.observations, **terms.values},
                judge_readings=terms.judged,
            ),
        )
    except SendRefusedError:
        # A refused send is refused for every cell after it: a stop, never a row.
        raise
    except Exception as exc:
        return _unmeasured(sample, exc)


async def _send_cell(
    sample: Sample,
    session: Session,
    wire_params: dict[str, Any],
    pairs: Mapping[str, PricedPair],
) -> tuple[dict[str, Any], PipelineData, CellEnvelope]:
    query = sample.query
    client = session.backend_client
    bound = await cell_bound(session, wire_params)
    envelope = CellEnvelope(
        client.cell_envelope_s(sample, wire_params),
        attempts=client.cell_attempts,
        label=f"{sample.id}:{query[:40]}",
    )
    # Unconditional and OUTSIDE the envelope scope: the envelope's only sighting of a machine sleep rides it.
    heartbeat_task = asyncio.create_task(
        heartbeat(
            session.state.ledger,
            call_id=f"scoring:{sample.id}",
            node="backend_scoring",
            round_num=_CURRENT_ROUND.get(),
            start_monotonic=time.monotonic(),
            on_suspend=envelope.on_suspend,
        )
    )
    try:
        async with envelope:
            try:
                data, spent = await client.run_query(
                    sample,
                    pipeline_params=wire_params,
                    bound=bound,
                    billed=cell_billing(
                        session.pipeline_schema,
                        pairs,
                        await attempt_bounds(session, wire_params),
                    ),
                )
            except httpx.HTTPStatusError as exc:
                if (answered := _answered_nothing(exc)) is None:
                    raise
                logger.warning(
                    "measure_sample for %s: answered nothing gradeable (%s) — a miss",
                    query[:60],
                    _extract_upstream_detail(exc),
                )
                data, spent = answered, _reply_spend(answered, pairs)
    finally:
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
    return data, spent, envelope


def _predicted(data: Mapping[str, Any], ranked: list[Any], answer_key: str | None) -> str:
    if answer_key is not None:
        return str(data.get(answer_key) or "").strip() or NO_RESULT
    return extract_item_label(ranked[0]) if ranked else NO_RESULT


def _observations(
    data: Mapping[str, Any], ranked: list[Any], pipeline_schema: PipelineSchema, spent: PipelineData
) -> dict[str, Any]:
    pd: dict[str, Any] = {"result_ranking": ranked}
    for key in pipeline_schema.observation_keys | _INFRA_KEYS:
        val = data.get(key)
        if val is not None:
            pd[key] = val
    pd.update(turn_scalars(pd.get("turns")))
    reached = data.get("terminal_node") or _ran_last(pipeline_schema, spent.step_timings)
    if reached is not None:
        pd["terminal_node"] = reached
    return pd


def _candidate_and_sample_facts(
    sample: Sample, pipeline_schema: PipelineSchema, pipeline_params: Mapping[str, Any]
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    # Off `pipeline_params`, never `wire_params`: no sample's own length may reach a prompt-length term.
    prompt_nodes = pipeline_schema.prompt_node_names()
    node_cfg = pipeline_params.get(prompt_nodes[0]) if prompt_nodes else None
    if isinstance(node_cfg, dict) and isinstance(node_cfg.get("prompt"), str):
        facts["target_prompt_chars"] = len(node_cfg["prompt"])
    # Banked so a JUDGE can read it: a judge is handed this row and never the `Sample`.
    if sample.question:
        facts["question"] = sample.question
    return facts


def _unmeasured(sample: Sample, exc: Exception) -> MeasuredCell:
    query = sample.query
    if isinstance(exc, CellUnscoreableError):
        logger.warning("measure_sample %s for %s: %s", exc.category.value, query[:60], exc)
        return _error_result(sample, str(exc), category=exc.category)
    # Never a backend 429: `BackendClient.run_query` answers each with the run's backpressure.
    category = failed_send(exc).failure or ErrorCategory.UNKNOWN
    if isinstance(exc, httpx.HTTPStatusError):
        upstream = _extract_upstream_detail(exc)
        error_msg = (
            f"HTTP {exc.response.status_code} — {_HTTP_FAILURE_WORDS[category]}"
            f"{f' :: {upstream}' if upstream else ''}"
        )
        logger.warning("measure_sample for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=category)
    if category is ErrorCategory.CONNECTION:
        error_msg = f"{type(exc).__name__}: {exc} — Backend may be down or unreachable."
        logger.warning("measure_sample CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=category)
    # Named by TYPE: a bare `TimeoutError()` has no message.
    failure = f"{type(exc).__name__}: {exc}"
    logger.warning("measure_sample failed for %s: %s", query[:60], failure, exc_info=exc)
    return _error_result(sample, failure, category=category)


def compare_rerun(
    cached_result: MeasuredCell, rerun_result: MeasuredCell, scorer: Scorer
) -> RerunComparison:
    cached_hit = scorer.grade(cached_result).hit
    rerun_hit = scorer.grade(rerun_result).hit
    hit_change = f"{'HIT' if cached_hit else 'MISS'}->{'HIT' if rerun_hit else 'MISS'}"

    cached_rank = cached_result.ground_truth_rank
    rerun_rank = rerun_result.ground_truth_rank
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
    cached_result: MeasuredCell,
    rerun_pipeline_params: dict[str, Any] | None,
) -> bool:
    # The helper `classify_result` stamps its codes with: a second spelling of the node misses silently.
    node = terminal_node(cached_result)
    if node is None:
        return False
    cl = classify_result(cached_result)
    budget_exhausted = (
        f"{node}:reasoning_budget_exhausted" in cl.infra_codes
        or f"{node}:output_truncated" in cl.infra_codes
    )
    if not budget_exhausted:
        return False

    cached_step = cached_result.pipeline.step_tokens.get(node)
    cached_completion = cached_step.output if cached_step is not None else 0
    if cached_completion <= 0:
        return False

    rerun_max_tokens = ((rerun_pipeline_params or {}).get(node) or {}).get("max_tokens")
    if rerun_max_tokens is None:
        return False

    return int(rerun_max_tokens) <= cached_completion


async def execute_stale_data_protocol(
    sample: Sample,
    cached_result: MeasuredCell,
    session: Session,
    *,
    pipeline_params: dict[str, Any] | None = None,
    sample_index: SampleIndex | None = None,
) -> tuple[MeasuredCell, str]:
    result = cached_result

    for step in STALE_DATA_LOAD_PROTOCOL:
        if session.control.pause_requested():
            return result, "paused"
        if step == "rerun":
            historical = sample_index.degradation_count(sample.id) if sample_index else 0
            effective_count = historical + 1
            if effective_count < RERUN_TRIGGER_COUNT:
                return replace(
                    cached_result,
                    degraded_observed=True,
                    degraded_obs_count=effective_count,
                    degraded_obs_threshold=RERUN_TRIGGER_COUNT,
                ), "below_threshold"

            if _rerun_would_repeat_token_budget_failure(cached_result, pipeline_params):
                return replace(
                    cached_result, config_fundamental_skip=True
                ), "skipped_config_fundamental"

            rerun = await measure_sample(sample, session, pipeline_params=pipeline_params)
            result = replace(
                rerun,
                retry_of_degraded=True,
                rerun_comparison=compare_rerun(
                    cached_result, rerun, session.scoring.require_scorer()
                ),
            )
            if not is_deprecated(result):
                return result, "rerun"

        elif step == "sampleswitch":
            if (
                sample_index
                and sample_index.degradation_rate(sample.id) >= SAMPLESWITCH_MIN_DEGRADATION_RATE
            ):
                return replace(cached_result, cached=True, switched_out=True), "sampleswitch"

    return replace(result, persistently_degraded=True), "exhausted"
