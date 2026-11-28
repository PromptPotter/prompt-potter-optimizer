from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from statistics import fmean
from typing import Any

from promptpotter.application.initialization.session import Session
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.classification import terminal_ranking
from promptpotter.application.scoring.evaluators import materialize_sample_values
from promptpotter.connectors.protocol import Connector, PricedPair
from promptpotter.domain.fitness_fold import cell_channels_of
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_schema import (
    LLMSpendBound,
    NodeSpendBound,
    PipelineNode,
    PipelineSchema,
    WebSpendBound,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import (
    NO_RESULT,
    PIPELINE_KEYS,
    MeasuredCell,
    PipelineData,
    extract_item_label,
    turn_scalars,
)
from promptpotter.domain.spend import CellPrice, StepUsage, TokenAccount
from promptpotter.infrastructure.backend import CellBilling
from promptpotter.infrastructure.llm.base import hold_ceiling
from promptpotter.infrastructure.llm.heartbeat import CellBeat, heartbeat
from promptpotter.infrastructure.llm.send_failure import FailedSendError
from promptpotter.infrastructure.llm.spend_book import (
    FRAMING_TOKENS,
    Billed,
    ReportedSpend,
    SendBound,
    require_spend_book,
)
from promptpotter.infrastructure.llm.telemetry import (
    call_price,
    current_round,
    emit_replay_usage,
    rate_priced_usd,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import CellUnscoreableError, ErrorCategory, SendRefusedError

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
        # A variable no sample field fills is left as written.
        if key in safe_vars:
            text = text.replace("{{" + key + "}}", str(safe_vars[key]))
    return text


def interpolate_pipeline_params(
    pipeline_params: PipelineParams, query_data: dict[str, Any]
) -> PipelineParams:
    return pipeline_params.with_nodes(
        {
            node: {**cfg, "prompt": interpolate_prompt(prompt, query_data)}
            if isinstance(prompt := cfg.get("prompt"), str)
            else cfg
            for node, cfg in pipeline_params.laid_nodes.items()
        }
    )


# Kept whatever the schema declares; ``pipeline_params`` is NOT here: the archive lifts it onto the index entry.
_INFRA_KEYS: frozenset[str] = frozenset(
    {
        "total_time",
        "diagnostics",
        "reasoning_trace",
        "turns",
        "outcome_note",
        "step_phases",
    }
)
assert _INFRA_KEYS <= PIPELINE_KEYS, (
    "an infra key PipelineData does not declare is filed as a dataset observation"
)

_SPEND_KEYS = ("step_tokens", "step_timings")

_UNROUTED: PricedPair = (None, None)


def _reply_spend(data: Mapping[str, Any], pairs: Mapping[str, PricedPair]) -> PipelineData:
    spent = PipelineData.from_wire({key: data.get(key) for key in _SPEND_KEYS})
    return replace(spent, step_tokens=_reported_usage(spent.step_tokens, pairs))


def _step_parts(
    step_tokens: Mapping[str, StepUsage], step_timings: Mapping[str, float]
) -> list[Billed]:
    return [
        Billed(
            usage.tokens,
            usage.cost_usd,
            served_by=usage.served_by,
            model=usage.model,
            node=node_name,
            provider=usage.provider,
            duration_s=step_timings.get(node_name, 0.0),
            rate_priced_usd=usage.rate_priced_usd,
        )
        for node_name, usage in step_tokens.items()
        if usage.tokens.total or usage.cost_usd
    ]


def emit_replayed_step_tokens(
    step_tokens: Mapping[str, StepUsage], step_timings: Mapping[str, float]
) -> None:
    """A replay states what the row RECORDED: its reported cost, else the rate stamped when measured."""
    for part in _step_parts(step_tokens, step_timings):
        emit_replay_usage(
            node=part.node or "backend",
            usage=part.usage,
            duration_s=part.duration_s or 0.0,
            price=call_price(
                "backend",
                part.usage,
                model=part.model,
                provider=part.provider,
                cost_usd=part.cost_usd,
                recorded=part.rate_priced_usd,
            ),
            served_by=part.served_by,
        )


def _ran_last(pipeline_schema: PipelineSchema, ran: Mapping[str, Any]) -> str | None:
    reached = [node.name for node in pipeline_schema.nodes if ran.get(node.name) is not None]
    return reached[-1] if reached else None


def _unreported_attempts(
    reported: Mapping[str, StepUsage], each: Mapping[str, SendBound]
) -> SendBound | None:
    input_tokens = output_tokens = 0
    usd: float | None = 0.0
    for name, entry in reported.items():
        bound = each.get(name)
        left = entry.unreported_attempts
        if bound is None or not left:
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

    def billed(data: dict[str, Any]) -> tuple[ReportedSpend | None, PipelineData]:
        spent = _reply_spend(data, pairs)
        if not isinstance(data.get("step_tokens"), dict):
            return None, spent
        reported = spent.step_tokens
        parts = _step_parts(reported, spent.step_timings)
        web = data.get("web_cost")
        usd = web.get("usd") if isinstance(web, dict) else None
        if isinstance(usd, int | float) and not isinstance(usd, bool) and usd:
            searched = next(
                (
                    n.name
                    for n in pipeline_schema.running_nodes
                    if isinstance(n.spend_bound, WebSpendBound)
                ),
                "web_search",
            )
            parts.append(Billed(TokenAccount(), float(usd), node=searched))
        return ReportedSpend(tuple(parts), _unreported_attempts(reported, each)), spent

    return billed


async def _attempt_bound(spend: LLMSpendBound, cfg: NodeCall, priced_as: PricedPair) -> SendBound:
    reply = cfg.max_tokens or spend.max_tokens
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
    session: Session, calls: Mapping[str, NodeCall]
) -> list[tuple[PipelineNode, NodeCall, NodeSpendBound | None]]:
    client = session.backend_client
    spends = []
    for node in session.bound_resolution.chain.running_nodes:
        cfg = calls.get(node.name) or NodeCall()
        spends.append((node, cfg, client.node_spend_bound(node, cfg)))
    return spends


async def attempt_bounds(session: Session, calls: Mapping[str, NodeCall]) -> dict[str, SendBound]:
    priced_as = session.backend_client.connector.priced_as
    return {
        node.name: await _attempt_bound(spend, cfg, priced_as(cfg))
        for node, cfg, spend in _node_spends(session, calls)
        if isinstance(spend, LLMSpendBound)
    }


_PRICED_RUNS = 32


def measured_cell_price(
    stores: Stores, dataset_hash: str, wire_params: PipelineParams
) -> CellPrice | None:
    """What a cell BILLED on these models over the archive's newest rows; ``None`` before any."""
    on_models = {name: {"model": cfg.model} for name, cfg in wire_params.nodes.items() if cfg.model}
    costs: list[float] = []
    seconds: list[float] = []
    for m in stores.archive.measurements_for_config(
        on_models, dataset_hash=dataset_hash, newest=_PRICED_RUNS
    ):
        # An archive row carries no grade, and the price is no formula's reading.
        channels = cell_channels_of(m.cell, None)
        if (cost := channels.get("cost")) is None:
            continue
        costs.append(cost)
        if (spent := channels.get("latency")) is not None:
            seconds.append(spent)
    if not costs:
        return None
    return CellPrice(
        usd=fmean(costs), seconds=fmean(seconds) if seconds else None, cells=len(costs)
    )


async def cell_bound(session: Session, params: PipelineParams) -> SendBound | None:
    """``None`` where the backend's own sends are each admitted; a node with no served bound leaves it UNBOUNDED."""
    return await _calls_bound(session, params.nodes)


async def _calls_bound(session: Session, calls: Mapping[str, NodeCall]) -> SendBound | None:
    client = session.backend_client
    if client.connector.holds_own_sends and client.connector.sent_spend_bound is None:
        return None
    usd = 0.0
    input_tokens = output_tokens = 0
    bounded = True
    unpriced: tuple[str, ...] = ()
    served = False
    for node, cfg, spend in _node_spends(session, calls):
        if spend is None:
            bounded = bounded and not node.is_llm
            continue
        served = True
        if isinstance(spend, WebSpendBound):
            usd += spend.queries * spend.usd_per_query
            continue
        each = await _attempt_bound(spend, cfg, client.connector.priced_as(cfg))
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
    wire_params: PipelineParams,
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
            usage.tokens,
            model=usage.model,
            provider=usage.provider,
            cost_usd=usage.cost_usd,
            recorded=usage.rate_priced_usd,
        ),
    )


def _estimated_usage(
    node: PipelineNode,
    resp_data: Mapping[str, Any],
    wire_params: PipelineParams,
    priced_as: PricedPair,
) -> StepUsage:
    prompt = wire_params.node(node.name).laid_keys().get("prompt")
    in_text = prompt if isinstance(prompt, str) else ""
    out_text = " ".join(_observed_texts(node, resp_data))
    model, provider = priced_as
    return StepUsage(
        TokenAccount(input=len(in_text) // 4, output=len(out_text) // 4),
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
    refused_parameter: str | None = None,
) -> MeasuredCell:
    return MeasuredCell(
        sample_key=sample.key,
        query=sample.query,
        ground_truth=sample.ground_truth or "",
        predicted="ERROR",
        error=error_msg or "unknown error",
        error_category=category,
        refused_parameter=refused_parameter,
    )


def _said_detail(said: str) -> object:
    """The ``detail`` a backend's refusal body names, else the body's own text."""
    try:
        body = json.loads(said)
    except ValueError:
        return said.strip()[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, dict | str) else said.strip()[:300]


def _upstream(said: str) -> str:
    if not isinstance(detail := _said_detail(said), dict):
        return str(detail)[:300]
    upstream = (
        f"upstream={detail.get('upstream_provider') or '?'}:{detail.get('upstream_model') or '?'}"
    )
    if (ustatus := detail.get("upstream_status")) is not None:
        upstream = f"{upstream} {ustatus}"
    return f"{upstream} :: {detail.get('upstream_message') or detail.get('error_code') or '?'}"


# The model ANSWERED and left nothing gradeable: a measured MISS of this configuration, never a hole.
_OUTPUT_FAILURE_CODES = frozenset(
    {"json_parse_failed", "schema_validation_failed", "output_truncated"}
)


def _answered_nothing(said: str) -> dict[str, Any] | None:
    detail = _said_detail(said)
    if not isinstance(detail, dict) or detail.get("error_code") not in _OUTPUT_FAILURE_CODES:
        return None
    return {"step_tokens": detail.get("step_tokens") or {}}


async def measure_sample(
    sample: Sample,
    session: Session,
    pipeline_params: PipelineParams,
    /,
) -> MeasuredCell:
    """One cell's FACTS — ungraded; the walk grades every row it takes, fresh or replayed."""
    # A verifier-graded cell carries `""`, which no answer matches.
    ground_truth = sample.ground_truth or ""
    pipeline_schema = session.bound_resolution.chain
    try:
        wire_params = interpolate_pipeline_params(pipeline_params, sample.model_dump())
        calls = wire_params.nodes
        pairs = {
            name: session.backend_client.connector.priced_as(cfg) for name, cfg in calls.items()
        }
        data, spent, envelope = await _send_cell(sample, session, wire_params, calls, pairs)

        ranked = terminal_ranking(data, pipeline_schema)
        predicted = _predicted(data, ranked, session.backend_client.connector.answer_key)
        if predicted == "ERROR":
            return _error_result(
                sample,
                "Backend returned ERROR as candidate — pipeline internal failure for this query.",
                category=ErrorCategory.PIPELINE,
            )

        pd = _observations(data, ranked, pipeline_schema, spent)
        pd["cell_report"] = _cell_report(data, session.backend_client.connector)
        # The envelope's final reading, taken AFTER its scope closed.
        pd["unworked_s"] = envelope.unworked
        pd.update(_candidate_and_sample_facts(sample, pipeline_schema, pipeline_params))
        result = MeasuredCell(
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
        )

        # Banked HERE and top-level: a replay never re-enters this function, and a formula names no nested value.
        terms = await materialize_sample_values(
            pipeline_schema, result, extra=session.state.scoring.judges
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
    except Exception as exc:  # noqa: BLE001 -- any fault of the cell is its row
        return _failed_row(sample, exc)


async def _send_cell(
    sample: Sample,
    session: Session,
    wire_params: PipelineParams,
    calls: Mapping[str, NodeCall],
    pairs: Mapping[str, PricedPair],
) -> tuple[dict[str, Any], PipelineData, CellEnvelope]:
    query = sample.query
    client = session.backend_client
    bound = await _calls_bound(session, calls)
    # The run's wall clock bounds every cell, so one still open at its edge is cut.
    budget_s = client.connector.cell_envelope_s(sample, wire_params)
    if (left := require_spend_book(f"cell {sample.id}").clock_left()) is not None:
        if left <= 0.0:
            raise SendRefusedError(
                f"cell {sample.id}: the launch's wall clock is spent, so nothing was sent",
                category=ErrorCategory.WALL_CLOCK,
            )
        budget_s = min(budget_s, left)
    envelope = CellEnvelope(
        budget_s,
        attempts=client.connector.cell_attempts,
        label=f"{sample.id}:{query[:40]}",
    )
    cell_hop = client.connector.cell_hop
    # Unconditional and OUTSIDE the envelope scope: the envelope's only sighting of a machine sleep rides it.
    heartbeat_task = asyncio.create_task(
        heartbeat(
            CellBeat(sample.id),
            round_num=current_round(),
            start_monotonic=time.monotonic(),
            inner_fn=None if cell_hop is None else functools.partial(cell_hop, sample, wire_params),
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
                    pairs=pairs,
                    billed=cell_billing(
                        session.bound_resolution.chain,
                        pairs,
                        await attempt_bounds(session, calls),
                    ),
                )
            except FailedSendError as exc:
                if (answered := _answered_nothing(exc.outcome.detail)) is None:
                    raise
                data, spent = answered, _reply_spend(answered, pairs)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
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


def _cell_report(data: Mapping[str, Any], connector: Connector) -> dict[str, Any]:
    """The connector's declaration is the gate: a dataset observation of a term's name is no term."""
    return {
        key: value for key in connector.cell_report_keys if (value := data.get(key)) is not None
    }


def _candidate_and_sample_facts(
    sample: Sample, pipeline_schema: PipelineSchema, pipeline_params: PipelineParams
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    # Off `pipeline_params`, never `wire_params`: no sample's own length may reach a prompt-length term.
    # Summed over every prompt node: the term prices the whole prompt the configuration ships.
    rendered = [
        prompt
        for node in pipeline_schema.prompt_nodes()
        if isinstance(prompt := pipeline_params.node(node.name).laid_keys().get("prompt"), str)
    ]
    if rendered:
        facts["target_prompt_chars"] = sum(map(len, rendered))
    # Banked so a JUDGE can read it: a judge is handed this row and never the `Sample`.
    if sample.question:
        facts["question"] = sample.question
    return facts


def _failed_row(sample: Sample, exc: Exception) -> MeasuredCell:
    if isinstance(exc, CellUnscoreableError):
        return _error_result(sample, str(exc), category=exc.category)
    if not isinstance(exc, FailedSendError):
        # A send fails typed at its adapter's door; this is the cell's own, named by TYPE.
        return _error_result(sample, f"{type(exc).__name__}: {exc}", category=ErrorCategory.UNKNOWN)
    # Never a backend 429: `BackendClient.run_query` answers each with the run's backpressure.
    outcome = exc.outcome
    category = outcome.failure or ErrorCategory.UNKNOWN
    error_msg = outcome.detail
    if outcome.status is not None:
        upstream = _upstream(outcome.detail)
        error_msg = f"HTTP {outcome.status} {category}{f' :: {upstream}' if upstream else ''}"
    elif category is ErrorCategory.CONNECTION:
        error_msg = f"{error_msg} — Backend may be down or unreachable."
    return _error_result(
        sample, error_msg, category=category, refused_parameter=outcome.refused_parameter
    )
