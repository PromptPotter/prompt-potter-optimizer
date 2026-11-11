from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from promptpotter.application.jobs.coupon import keyed_client
from promptpotter.application.optimizer_manifest import bound_determinism
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.pipeline_overlay import pinned_route
from promptpotter.domain.run_records import (
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.domain.search_point import NodeConfig
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.heartbeat import heartbeat, silence_detail
from promptpotter.infrastructure.llm.json_parse import (
    OptimizerPromptParseError,
    extract_parsed_json,
)
from promptpotter.infrastructure.llm.registry import default_routes, provider_sends
from promptpotter.infrastructure.llm.request import ChatMessage, ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import (
    SEND_ATTEMPTS,
    SendBudget,
    throttle_stall_given_to,
    under_budget,
)
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache, hash_call
from promptpotter.shared.errors import OptimizerTimeoutError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


@shapes_optimizer_prompt
def _drop_class_description(schema: dict[str, Any], _model: type[BaseModel]) -> None:
    schema.pop("description", None)


@shapes_optimizer_prompt
class OptimizerResponseModel(StrictModel):
    """Every model whose JSON Schema goes on the wire; class descriptions are dropped from it."""

    model_config = ConfigDict(json_schema_extra=_drop_class_description)


@dataclass(frozen=True)
class InjectionBreakdown:
    chars: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class LLMCallContext:
    # The calling node and its config as its selection resolved it: the one source a call lays over.
    node: str
    config: NodeConfig
    cache: LLMReuseCache
    # Drawn per call off the run's clamp (`Cycle.rng`); it keys the reuse cache. None outside a run.
    seed: int | None
    ledger: CycleEventLog | None = None
    round_num: int | None = None
    candidate_idx: int | None = None
    injections: InjectionBreakdown = field(default_factory=InjectionBreakdown)


# What a node declaring no temperature samples at.
_DEFAULT_TEMPERATURE = 0.0

# Per round trip: the client's own httpx timeout is per-read-gap, so a slow stream never trips it.
OPTIMIZER_CALL_DEADLINE_S: float = 180.0

# The parse ladder inside `chat()` is a repair then a clean re-ask, each a whole round trip.
_MAX_ROUND_TRIPS_PER_CALL = 3

_REASONING_LEDGER_CAP = 4000


def _ledger_reasoning(text: str) -> str:
    if len(text) <= _REASONING_LEDGER_CAP:
        return text
    half = _REASONING_LEDGER_CAP // 2
    return f"{text[:half]}\n[… {len(text) - 2 * half} chars …]\n{text[-half:]}"


async def _chat_under_deadline(
    llm_client: LLMClientBase, request: ChatRequest, *, node_label: str
) -> LLMResponse:
    """Bounds the provider WORKING: held time (throttle, rate limiter, 5xx backoff) is given back."""
    budget = SendBudget(
        OPTIMIZER_CALL_DEADLINE_S * _MAX_ROUND_TRIPS_PER_CALL, attempts=SEND_ATTEMPTS
    )
    try:
        async with budget.clock:
            with under_budget(budget), throttle_stall_given_to(budget.give_back):
                return await llm_client.chat(request, label=CallLabel(node_label, "optimizer"))
    except TimeoutError as exc:
        raise OptimizerTimeoutError(
            f"optimizer call {node_label} exceeded the {budget.deadline_s:.0f}s deadline"
        ) from exc


def _ledger_response_payload(response: LLMResponse) -> Any:
    if response.parsed is None:
        return response.content
    if isinstance(response.parsed, BaseModel):
        return response.parsed.model_dump()
    return response.parsed


@dataclass(frozen=True)
class _Call:
    label: str
    merged: NodeConfig
    messages: list[ChatMessage]
    response_model: type[BaseModel] | None
    response_schema: dict[str, Any] | None
    context: LLMCallContext
    started: float
    call_id: str

    def elapsed_s(self) -> float:
        return round(time.monotonic() - self.started, 2)

    @functools.cached_property
    def client(self) -> LLMClientBase:
        """Resolved only for a send: a replay goes out on no key, so no coupon refuses it."""
        return keyed_client(self.merged.provider)


def _merged_config(
    config: NodeConfig, *, temperature: float | None, seed: int | None
) -> NodeConfig:
    laid: dict[str, object] = {}
    if temperature is not None:
        laid["temperature"] = temperature
    # The determinism clamp goes LAST: it must beat every per-call setting, steered temperature included.
    if (clamp := bound_determinism()) is not None:
        laid |= clamp.model_dump(exclude_none=True)
    # Except the call's own draw, drawn FROM the clamp's seed: it keys the call on every provider.
    if seed is not None:
        laid["seed"] = seed
    merged = config.laid(laid)
    # Keyed and sent as the host it gets, as a target node is (`pin_default_routes`).
    if route := pinned_route(merged.model, merged.route_order, merged.provider, default_routes()):
        merged = merged.laid({"route_order": route})
    return merged


def _sampled_at(merged: NodeConfig) -> float:
    return _DEFAULT_TEMPERATURE if merged.temperature is None else merged.temperature


def _reuse_key(
    merged: NodeConfig,
    messages: list[ChatMessage],
    response_model: type[BaseModel] | None,
    response_schema: dict[str, Any] | None,
) -> str:
    return hash_call(
        messages=messages,
        model=merged.model,
        provider=merged.provider,
        temperature=_sampled_at(merged),
        json_schema=response_schema,
        response_model=response_model.__name__ if response_model else None,
        seed=merged.seed,
        max_tokens=merged.max_tokens,
        reasoning_effort=merged.reasoning_effort,
        top_p=merged.top_p,
        route_order=merged.route_order,
    )


def _announce(call: _Call) -> None:
    context, label = call.context, call.label
    injections = context.injections
    prompt_chars = sum(len(m.get("content") or "") for m in call.messages)
    start_record = LLMCallStartRecord(
        call_id=call.call_id,
        node=label,
        round=context.round_num,
        candidate_idx=context.candidate_idx,
        model=call.merged.model,
        started_at_ms=int(time.time() * 1000),
        prompt_chars=prompt_chars,
        injection_chars=dict(injections.chars),
        injection_dropped=dict(injections.dropped),
    )
    if context.ledger is not None:
        context.ledger.append(start_record)
    # The alarm is a REFUSED panel, never the prompt's size: the mandatory floor is admitted at any cost.
    no_room = start_record.refused_panels
    log = logger.warning if no_room else logger.info
    heaviest = (
        " · heaviest: "
        + ", ".join(
            f"{name} {chars:,}c"
            for name, chars in sorted(injections.chars.items(), key=lambda kv: -kv[1])[:3]
        )
        if no_room and injections.chars
        else ""
    )
    by_size = sorted(injections.dropped.items(), key=lambda kv: -kv[1])
    thinned = [f"{n} -{c}" for n, c in by_size if n not in no_room][:3]
    # `refused_panels` sorts by NAME, so truncating it unranked reports the alphabet, not the loss.
    worst = sorted(no_room, key=lambda n: -injections.dropped.get(n, 0))
    more = f" (+{len(worst) - 4} more)" if len(worst) > 4 else ""
    dropped = (" · NO ROOM: " + ", ".join(worst[:4]) + more if worst else "") + (
        " · thinned: " + ", ".join(thinned) if thinned else ""
    )
    log(
        "→ optimizer call: %s · %s · %d-char prompt%s%s",
        label,
        call.merged.model,
        prompt_chars,
        heaviest,
        dropped,
    )


async def _provider_reply(call: _Call) -> LLMResponse:
    context, label, merged = call.context, call.label, call.merged
    # Unconditional: `on_suspend` guards a deadline, not telemetry, so a ledger-less call needs it too.
    heartbeat_task: asyncio.Task[None] = asyncio.create_task(
        heartbeat(
            context.ledger,
            call_id=call.call_id,
            node=label,
            round_num=context.round_num,
            start_monotonic=call.started,
            detail_fn=lambda: silence_detail(call.client, merged.model, role="provider"),
        )
    )
    try:
        return await _chat_under_deadline(
            call.client,
            ChatRequest(
                messages=call.messages,
                model=merged.model,
                temperature=_sampled_at(merged),
                max_tokens=merged.max_tokens,
                response_model=call.response_model,
                response_schema=call.response_schema,
                reasoning_effort=merged.reasoning_effort,
                top_p=merged.top_p,
                # The draw goes on the wire only where the provider's client sends one.
                seed=merged.seed if "seed" in provider_sends(merged.provider) else None,
                # An empty route pins nothing, so it is no request for one.
                route_order=merged.route_order or None,
            ),
            node_label=label,
        )
    except OptimizerPromptParseError as parse_err:
        logger.error(
            "%s: optimizer call failed to parse — %s",
            label,
            parse_err.diagnosis(),
        )
        raise
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


def _call_record(
    call: _Call,
    response: LLMResponse,
    duration_s: float,
    *,
    cached: bool,
    trace_meta: dict[str, Any] | None,
) -> LLMCallRecord:
    payload: dict[str, Any] = {
        "type": call.label,
        "config": {
            "model": call.merged.model,
            "temperature": _sampled_at(call.merged),
            "max_tokens": call.merged.max_tokens,
        },
        "response": _ledger_response_payload(response),
        "model": response.model,
        "duration_s": duration_s,
        "finish_reason": response.finish_reason,
        "schema_repair_errors": response.schema_repair_errors,
    }
    # EVIDENCE FOR A HUMAN: nothing downstream reads this key and nothing may start.
    if response.reasoning:
        payload["reasoning"] = _ledger_reasoning(response.reasoning)
    if cached:
        payload["cached"] = True
    if trace_meta:
        payload.update(trace_meta)
    else:
        payload["messages"] = call.messages
    return LLMCallRecord(
        node=call.label,
        round=call.context.round_num,
        candidate_idx=call.context.candidate_idx,
        call_id=call.call_id,
        usage=response.usage,
        payload=payload,
    )


async def llm_call(
    messages: list[ChatMessage],
    *,
    context: LLMCallContext,
    trace_meta: dict[str, Any] | None = None,
    response_model: type[BaseModel] | None = None,
    response_schema: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> LLMResponse:
    node = context.node
    merged = _merged_config(context.config, temperature=temperature, seed=context.seed)
    call = _Call(
        label=node,
        merged=merged,
        messages=messages,
        response_model=response_model,
        response_schema=response_schema,
        context=context,
        started=time.monotonic(),
        call_id=uuid.uuid4().hex if context.ledger is not None else "",
    )

    async def send() -> LLMResponse:
        _announce(call)
        return await _provider_reply(call)

    answered = await context.cache.answer(
        _reuse_key(merged, messages, response_model, response_schema),
        send,
        node=node,
        kind="optimizer",
        provider=merged.provider,
        model=merged.model,
    )
    response = answered.response
    # ``parsed`` is typed ``Any``, so a banked reply's `model_validate` leaves the saved dict a dict.
    if response_model is not None and isinstance(response.parsed, dict):
        response.parsed = response_model.model_validate(response.parsed)

    if context.ledger is not None:
        context.ledger.append(
            _call_record(
                call,
                response,
                answered.duration_s,
                cached=answered.replayed,
                trace_meta=trace_meta,
            )
        )

    return response


async def run_optimizer_node(
    *,
    template: OptimizerPromptTemplate,
    prompt_vars: dict[str, Any],
    response_model: type[BaseModel] | None,
    temperature: float | None = None,
    response_schema: dict[str, Any] | None = None,
    user_content: str | None = None,
    context: LLMCallContext,
) -> tuple[Any, str, int]:
    prompt = template.compile_prompt(**prompt_vars)
    if user_content is not None:
        messages: list[ChatMessage] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]
    response = await llm_call(
        messages=messages,
        response_model=response_model,
        response_schema=response_schema,
        context=context,
        trace_meta={
            "template_name": context.node,
            "template_fields": template.prompt_fields(),
            "variables": prompt_vars,
        },
        temperature=temperature,
    )
    return extract_parsed_json(response), prompt, len(response.schema_repair_errors)
