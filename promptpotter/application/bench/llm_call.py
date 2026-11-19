from __future__ import annotations

import asyncio
import contextlib
import functools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.pipeline_overlay import pinned_route
from promptpotter.domain.records.run_records import (
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.domain.search_point import NodeConfig
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.base import ChatMessage, ChatRequest, LLMResponse
from promptpotter.infrastructure.llm.heartbeat import CallBeat, heartbeat, silence_detail
from promptpotter.infrastructure.llm.json_parse import (
    OptimizerPromptParseError,
    try_parse_json,
)
from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.llm.send_failure import FailedSendError
from promptpotter.infrastructure.llm.send_pacing import (
    SEND_ATTEMPTS,
    SendBudget,
    throttle_stall_given_to,
)
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.llm.wallet import keyed_client
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.llm_reuse_cache import Answered, LLMReuseCache, hash_call
from promptpotter.shared.errors import OptimizerTimeoutError


def _drop_class_description(schema: dict[str, Any], _model: type[BaseModel]) -> None:
    schema.pop("description", None)


# The response model holds these after the call; a decoder held to them pads or cuts to fit.
_HELD_AFTER = frozenset({"minLength", "minProperties"})
_TYPED = frozenset({"type", "enum", "const", "anyOf", "oneOf", "allOf", "$ref"})


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if any(s.get("type") == "null" for s in (schema, *schema.get("anyOf", ()))):
        return {k: v for k, v in schema.items() if k != "default"}
    named = {k: schema[k] for k in ("description", "title") if k in schema}
    inner = {k: v for k, v in schema.items() if k not in ("description", "title", "default")}
    return {"anyOf": [inner, {"type": "null"}], **named}


def wire_closed(schema: dict[str, Any]) -> dict[str, Any]:
    """The one wire rule a strict host enforces: every object closed, every property required, one
    the model may leave unsaid nullable. An open map or an untyped value cannot be stated, so it raises."""
    out = {k: v for k, v in schema.items() if k not in _HELD_AFTER}
    if not _TYPED & out.keys():
        raise ValueError(f"an untyped value cannot ride a strict wire: {out!r:.200}")
    for key in ("anyOf", "oneOf", "allOf"):
        if key in out:
            out[key] = [wire_closed(s) for s in out[key]]
    if "items" in out:
        out["items"] = wire_closed(out["items"])
    if "$defs" in out:
        out["$defs"] = {name: wire_closed(s) for name, s in out["$defs"].items()}
    if out.get("type") != "object":
        return out
    if out.get("additionalProperties", False) is not False:
        raise ValueError(f"an open map cannot ride a strict wire: {out!r:.200}")
    said = set(out.get("required", ()))
    props = {name: wire_closed(s) for name, s in out.get("properties", {}).items()}
    out["properties"] = {n: s if n in said else _nullable(s) for n, s in props.items()}
    out["required"] = list(props)
    out["additionalProperties"] = False
    return out


def _unsaid(value: object) -> object:
    if not isinstance(value, dict):
        return value
    kept = {k: _unsaid(v) for k, v in value.items() if v is not None}
    return {k: v for k, v in kept.items() if v != {} or value[k] == {}}


class OptimizerResponseModel(StrictModel):
    """Every model whose JSON Schema goes on the wire; class descriptions are dropped from it."""

    model_config = ConfigDict(json_schema_extra=_drop_class_description)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return wire_closed(super().model_json_schema(*args, **kwargs))

    # A null is a key left unsaid, and a map it empties is unsaid too: the field keeps its default.
    @model_validator(mode="before")
    @classmethod
    def _null_is_unsaid(cls, data: object) -> object:
        return _unsaid(data)


@dataclass(frozen=True)
class InjectionBreakdown:
    chars: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)


_NO_INJECTIONS = InjectionBreakdown()


@dataclass(frozen=True)
class FilledPrompt:
    """``rendered`` is what the node was SHOWN, after selection, so the ledger's breakdown and the prompt agree."""

    # Each region's placed panel text, joined behind the region's own text once its slots fill.
    panels: dict[str, str]
    prompt_vars: dict[str, str]
    rendered: dict[str, str] = field(default_factory=dict)
    injections: InjectionBreakdown = _NO_INJECTIONS

    def compose(self, template: OptimizerPromptTemplate) -> OptimizerPromptTemplate:
        """Injected text is data: no slot inside a panel or a slot's value is ever filled."""
        own = template.filled(self.prompt_vars)
        return own.model_copy(
            update={
                region: "\n\n".join(t for t in (getattr(own, region), text) if t)
                for region, text in self.panels.items()
            }
        )


@dataclass(frozen=True, kw_only=True)
class LLMCallContext:
    # The calling node, its config and the template that config runs, as one selection resolved them.
    node: str
    config: NodeConfig
    template: OptimizerPromptTemplate
    cache: LLMReuseCache
    # Drawn per call off the run's clamp (`Cycle.rng`); it keys the reuse cache. None outside a run.
    seed: int | None
    round_num: int | None = None
    candidate_idx: int | None = None


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
    llm_client: OpenAICompatibleClient, request: ChatRequest, *, node_label: str
) -> LLMResponse:
    """Bounds the provider WORKING: held time (throttle, rate limiter, 5xx backoff) is given back."""
    budget = SendBudget(
        OPTIMIZER_CALL_DEADLINE_S * _MAX_ROUND_TRIPS_PER_CALL, attempts=SEND_ATTEMPTS
    )
    try:
        async with budget.clock:
            with scoped(send_budget=budget), throttle_stall_given_to(budget.give_back):
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
    request: ChatRequest
    context: LLMCallContext
    injections: InjectionBreakdown
    started: float
    call_id: str

    def elapsed_s(self) -> float:
        return round(time.monotonic() - self.started, 2)

    @functools.cached_property
    def client(self) -> OpenAICompatibleClient:
        """Resolved only for a send: a replay goes out on no key, so no coupon refuses it."""
        return keyed_client(self.merged.provider)


def _merged_config(
    config: NodeConfig, *, temperature: float | None, seed: int | None
) -> NodeConfig:
    laid: dict[str, object] = {}
    if temperature is not None:
        laid["temperature"] = temperature
    # The determinism clamp goes LAST: it must beat every per-call setting, steered temperature included.
    if (clamp := run_scope().determinism) is not None:
        laid |= clamp.model_dump(exclude_none=True, exclude={"route_order"})
        # A route names one model's hosts: it pins only a call to the model it keys.
        if hosts := (clamp.route_order or {}).get(config.model):
            laid["route_order"] = hosts
    # Except the call's own draw, drawn FROM the clamp's seed: it keys the call on every provider.
    if seed is not None:
        laid["seed"] = seed
    merged = config.model_copy(update=laid)
    # Keyed and sent as the host it gets, as a target node is (`pin_default_routes`).
    if route := pinned_route(merged.model, merged.route_order, merged.provider, default_routes()):
        merged = merged.model_copy(update={"route_order": route})
    return merged


def _sampled_at(merged: NodeConfig) -> float:
    return _DEFAULT_TEMPERATURE if merged.temperature is None else merged.temperature


def _announce(call: _Call) -> None:
    context, label, injections = call.context, call.label, call.injections
    prompt_chars = sum(len(m.get("content") or "") for m in call.request.messages)
    start_record = LLMCallStartRecord(
        call_id=call.call_id,
        node=label,
        round=context.round_num,
        candidate_idx=context.candidate_idx,
        model=call.merged.model,
        prompt_chars=prompt_chars,
        injection_chars=dict(injections.chars),
        injection_dropped=dict(injections.dropped),
    )
    append_record(start_record)


async def _provider_reply(call: _Call) -> LLMResponse:
    context, label, merged = call.context, call.label, call.merged
    # Unconditional: `on_suspend` guards a deadline, not telemetry, so a ledger-less call needs it too.
    heartbeat_task: asyncio.Task[None] = asyncio.create_task(
        heartbeat(
            CallBeat(call.call_id, label),
            round_num=context.round_num,
            start_monotonic=call.started,
            detail_fn=lambda: silence_detail(call.client, merged.model, role="provider"),
        )
    )
    try:
        return await _chat_under_deadline(call.client, call.request, node_label=label)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


def _call_record(
    call: _Call, trace_meta: dict[str, Any] | None, said: dict[str, Any], usage: TokenAccount | None
) -> LLMCallRecord:
    """Enough to rebuild the request beside what came back: the config, prompt and schema sent."""
    merged = call.merged
    config = {"temperature": _sampled_at(merged), "route_order": call.request.route_order}
    keys = ("model", "max_tokens", "reasoning_effort", "top_p", "seed")
    asked = {"type": call.label, "config": merged.model_dump(include=set(keys)) | config}
    asked |= trace_meta or {"messages": call.request.messages}
    return LLMCallRecord(
        node=call.label,
        round=call.context.round_num,
        candidate_idx=call.context.candidate_idx,
        call_id=call.call_id,
        usage=usage,
        payload=asked | {"response_schema": call.request.wire_schema} | said,
    )


def _answered_record(
    call: _Call, answered: Answered, trace_meta: dict[str, Any] | None
) -> LLMCallRecord:
    response = answered.response
    said = {
        "response": _ledger_response_payload(response),
        # EVIDENCE FOR A HUMAN, as `reasoning` is: the reply as the provider sent it.
        "content": response.content,
        "model": response.model,
        "duration_s": answered.duration_s,
        "finish_reason": response.finish_reason,
        "schema_repair_errors": [a.model_dump(mode="json") for a in response.schema_repair_errors],
    }
    # EVIDENCE FOR A HUMAN: nothing downstream reads this key and nothing may start.
    if response.reasoning:
        said["reasoning"] = _ledger_reasoning(response.reasoning)
    if answered.replayed:
        said["cached"] = True
    return _call_record(call, trace_meta, said, response.usage)


def _failed_record(
    call: _Call, failure: BaseException, trace_meta: dict[str, Any] | None
) -> LLMCallRecord:
    """The reply text a failure carries: the last rung's where the repair ladder ran out, a refusal's body."""
    parse = failure if isinstance(failure, OptimizerPromptParseError) else None
    sent = failure.outcome if isinstance(failure, FailedSendError) else None
    refused = None if sent is None or sent.status is None else sent.detail
    said = {
        "error": f"{type(failure).__name__}: {failure}",
        "content": refused if parse is None else parse.raw,
        "duration_s": call.elapsed_s(),
        "finish_reason": None if parse is None else parse.finish_reason,
    }
    return _call_record(call, trace_meta, said, None if parse is None else parse.usage)


async def llm_call(
    messages: list[ChatMessage],
    *,
    context: LLMCallContext,
    injections: InjectionBreakdown = _NO_INJECTIONS,
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
        request=ChatRequest(
            messages=messages,
            model=merged.model,
            temperature=_sampled_at(merged),
            max_tokens=merged.max_tokens,
            response_model=response_model,
            response_schema=response_schema,
            reasoning_effort=merged.reasoning_effort,
            top_p=merged.top_p,
            seed=merged.seed,
            route_order=merged.route_order,
        ),
        context=context,
        injections=injections,
        started=time.monotonic(),
        call_id=uuid.uuid4().hex,
    )

    async def send() -> LLMResponse:
        _announce(call)
        return await _provider_reply(call)

    try:
        answered = await context.cache.answer(
            hash_call(call.request, provider=merged.provider),
            send,
            node=node,
            kind="optimizer",
            provider=merged.provider,
            model=merged.model,
        )
    # A pause's cancel included: an `llm_call_start` with no close reads as a call still waiting.
    except BaseException as failure:
        append_record(_failed_record(call, failure, trace_meta))
        raise
    append_record(_answered_record(call, answered, trace_meta))
    return answered.response


async def answer[M: BaseModel](
    context: LLMCallContext,
    reply: type[M],
    filled: FilledPrompt,
    *,
    temperature: float | None = None,
    response_schema: dict[str, Any] | None = None,
    user_content: str | None = None,
) -> tuple[M, int]:
    """With its schema repairs; every fault is one `OptimizerPromptParseError`, an unbound reply included."""
    template = filled.compose(context.template)
    prompt = template.render()
    if user_content is not None:
        messages: list[ChatMessage] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]
    response = await llm_call(
        messages=messages,
        response_model=reply,
        response_schema=response_schema,
        context=context,
        injections=filled.injections,
        trace_meta={
            "template_name": context.node,
            "template_fields": template.prompt_fields(),
            "variables": filled.prompt_vars,
        },
        temperature=temperature,
    )
    repairs = len(response.schema_repair_errors)
    landed = response.parsed
    if isinstance(landed, reply):
        return landed, repairs
    try:
        bound = reply.model_validate(
            try_parse_json(response.content, context.node) if landed is None else landed
        )
    except ValidationError as unbound:
        raise OptimizerPromptParseError(
            response.content,
            unbound,
            attempts=repairs + 1,
            model=response.model,
            finish_reason=response.finish_reason,
        ) from unbound
    return bound, repairs
