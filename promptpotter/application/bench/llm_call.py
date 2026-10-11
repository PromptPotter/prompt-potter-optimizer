from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from promptpotter.application.optimizer_manifest import (
    get_optimizer_config_overrides,
    llm_node_config,
    resolve_node_override,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.run_records import (
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.heartbeat import heartbeat, waiting_on
from promptpotter.infrastructure.llm.json_parse import (
    OptimizerPromptParseError,
    extract_parsed_json,
)
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import (
    SEND_ATTEMPTS,
    SendBudget,
    throttle_stall_given_to,
    under_budget,
)
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.llm.telemetry import call_priced, emit_token_usage
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache, hash_call
from promptpotter.shared.errors import OptimizerTimeoutError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = [
    "InjectionBreakdown",
    "LLMCallContext",
    "OptimizerResponseModel",
    "llm_call",
    "run_optimizer_node",
]


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
    silent: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMCallContext:
    ledger: CycleEventLog | None = None
    round_num: int | None = None
    candidate_idx: int | None = None
    # Consulted only when non-``None``, so omitting it silently re-spends.
    cache: LLMReuseCache | None = None
    injections: InjectionBreakdown = field(default_factory=InjectionBreakdown)


_LLM_DEFAULTS: dict[str, Any] = {"temperature": 0.0}

# Per round trip: the provider SDK's own timeout is per-read-gap, so a slow stream never trips it.
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
    assert budget.clock is not None
    try:
        async with budget.clock:
            with under_budget(budget), throttle_stall_given_to(budget.give_back):
                return await llm_client.chat(request, label=CallLabel(node_label, "optimizer"))
    except TimeoutError as exc:
        raise OptimizerTimeoutError(
            f"optimizer call {node_label} exceeded the {budget.deadline_s:.0f}s deadline"
        ) from exc


def _replay(cache: LLMReuseCache, key: str, *, label: str) -> LLMResponse | None:
    try:
        payload = cache.load(key)
        if payload is None:
            return None
        return LLMResponse.model_validate(payload)
    except Exception as exc:
        # Deliberately broad: nothing in a cache may ever cost a run.
        logger.warning("optimizer_reuse entry for %s unusable, re-sampling — %s", label, exc)
        return None


def _ledger_response_payload(response: LLMResponse) -> Any:
    if response.parsed is None:
        return response.content
    if isinstance(response.parsed, BaseModel):
        return response.parsed.model_dump()
    return response.parsed


@dataclass(frozen=True)
class _Call:
    label: str
    merged: dict[str, Any]
    client: LLMClientBase
    messages: list[dict[str, str]]
    response_model: type[BaseModel] | None
    response_schema: dict[str, Any] | None
    context: LLMCallContext
    started: float
    call_id: str

    def elapsed_s(self) -> float:
        return round(time.monotonic() - self.started, 2)


def _merged_config(
    node: str | None, config: dict[str, Any] | None, overrides: dict[str, Any]
) -> dict[str, Any]:
    if config is None:
        config = llm_node_config(node) if node else {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}
    if node and (specimen := resolve_node_override(node)).model:
        merged["model"] = specimen.model
        if specimen.provider:
            merged["provider"] = specimen.provider
    # The determinism clamp goes LAST: it must beat every per-call override, steered temperature included.
    if config_overrides := get_optimizer_config_overrides():
        merged = {**merged, **config_overrides}
    return merged


def _reuse_key(
    merged: dict[str, Any],
    messages: list[dict[str, str]],
    response_model: type[BaseModel] | None,
    response_schema: dict[str, Any] | None,
) -> str:
    return hash_call(
        messages=messages,
        model=merged.get("model"),
        provider=merged["provider"],
        temperature=merged["temperature"],
        json_schema=response_schema,
        response_model=response_model.__name__ if response_model else None,
        seed=merged.get("seed"),
        max_tokens=merged.get("max_tokens"),
        reasoning_effort=merged.get("reasoning_effort"),
        top_p=merged.get("top_p"),
        route_order=merged.get("route_order"),
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
        model=call.merged.get("model"),
        started_at_ms=int(time.time() * 1000),
        prompt_chars=prompt_chars,
        injection_chars=dict(injections.chars),
        injection_dropped=dict(injections.dropped),
        injection_silent=list(injections.silent),
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
        call.merged["model"],
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
            detail_fn=lambda: waiting_on(call.client, merged.get("model"), role="provider"),
        )
    )
    try:
        return await _chat_under_deadline(
            call.client,
            ChatRequest(
                messages=call.messages,
                model=merged["model"],
                temperature=merged["temperature"],
                max_tokens=merged.get("max_tokens"),
                response_model=call.response_model,
                response_schema=call.response_schema,
                reasoning_effort=merged.get("reasoning_effort"),
                top_p=merged.get("top_p"),
                seed=merged.get("seed"),
                # An empty route pins nothing, so it is no request for one.
                route_order=merged.get("route_order") or None,
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
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "heartbeat task for %s raised on teardown",
                label,
                exc_info=True,
            )


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
            "model": call.merged.get("model"),
            "temperature": call.merged["temperature"],
            "max_tokens": call.merged.get("max_tokens"),
        },
        "response": _ledger_response_payload(response),
        "usage": response.usage.model_dump(),
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
        payload=payload,
    )


async def llm_call(
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict[str, Any] | None = None,
    trace_meta: dict[str, Any] | None = None,
    response_model: type[BaseModel] | None = None,
    response_schema: dict[str, Any] | None = None,
    context: LLMCallContext | None = None,
    **overrides: Any,
) -> LLMResponse:
    if context is None:
        context = LLMCallContext()
    label = node or "llm_call"
    merged = _merged_config(node, config, overrides)
    llm_client = get_llm_client(merged["provider"])

    cache_key: str | None = None
    replayed: LLMResponse | None = None
    if context.cache is not None:
        cache_key = _reuse_key(merged, messages, response_model, response_schema)
        replayed = _replay(context.cache, cache_key, label=label)

    call = _Call(
        label=label,
        merged=merged,
        client=llm_client,
        messages=messages,
        response_model=response_model,
        response_schema=response_schema,
        context=context,
        started=time.monotonic(),
        call_id=uuid.uuid4().hex if context.ledger is not None else "",
    )
    if replayed is not None:
        response = replayed
        # ``parsed`` is typed ``Any``, so `model_validate` leaves the saved dict a dict.
        if response_model is not None and isinstance(response.parsed, dict):
            response.parsed = response_model.model_validate(response.parsed)
        duration_s = call.elapsed_s()
        logger.debug("optimizer_reuse hit for %s (%s)", label, cache_key)
    else:
        _announce(call)
        response = await _provider_reply(call)
        duration_s = call.elapsed_s()

    # A cache hit is metered too, once per campaign: incurred cost stays invariant to cache history.
    counted = cache_key is not None and call_priced(cache_key)
    if replayed is not None and not counted:
        emit_token_usage(
            node=label,
            kind="optimizer",
            usage=response.usage,
            provider=merged["provider"],
            served_by=response.served_by,
            duration_s=duration_s,
            model=response.model,
            cost_usd=response.cost_usd,
            cached=True,
        )

    # Never cache an empty response: a TRANSIENT provider failure would replay forever, tenant-wide.
    usable = bool(response.content.strip()) or response.parsed is not None
    if replayed is None and context.cache is not None and cache_key is not None and usable:
        context.cache.save(cache_key, response.model_dump())

    if context.ledger is not None:
        context.ledger.append(
            _call_record(
                call, response, duration_s, cached=replayed is not None, trace_meta=trace_meta
            )
        )

    return response


async def run_optimizer_node(
    *,
    template_name: str,
    template: PromptTemplate,
    prompt_vars: dict[str, Any],
    response_model: type[BaseModel] | None,
    temperature: float | None = None,
    response_schema: dict[str, Any] | None = None,
    user_content: str | None = None,
    context: LLMCallContext | None = None,
) -> tuple[Any, str, int]:
    prompt = template.compile_prompt(**prompt_vars)
    if user_content is not None:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]
    overrides: dict[str, Any] = {}
    if temperature is not None:
        overrides["temperature"] = temperature
    response = await llm_call(
        messages=messages,
        node=template_name,
        response_model=response_model,
        response_schema=response_schema,
        context=context,
        trace_meta={
            "template_name": template_name,
            "template_fields": template.prompt_fields(),
            "variables": prompt_vars,
        },
        **overrides,
    )
    return extract_parsed_json(response), prompt, len(response.schema_repair_errors)
