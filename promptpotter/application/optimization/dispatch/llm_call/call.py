"""The optimizer LLM call itself. ``llm_call`` is the chokepoint every optimizer prompt call goes
through for 429-retry, the wall-clock deadline, the heartbeat, token emit, the ledger, the cache."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    get_optimizer_config_overrides,
    get_optimizer_schema,
    load_optimizer_prompt,
    resolve_node_override,
)
from promptpotter.application.optimization.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)
from promptpotter.config.settings import (
    OPTIMIZER_CALL_DEADLINE_S,
    OPTIMIZER_PROMPT_BUDGET_CHARS,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.run_records import (
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import (
    OptimizerPromptParseError,
    extract_parsed_json,
)
from promptpotter.infrastructure.llm.rate_limit import (
    MAX_429_ATTEMPTS,
    decide_429_wait,
    wait_with_countdown,
)
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.telemetry import emit_round_warning, emit_token_usage
from promptpotter.infrastructure.store.stores import OptimizerReuseCache, hash_call

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["LLMCallContext", "llm_call", "run_optimizer_node"]


@dataclass(frozen=True)
class LLMCallContext:
    """Audit + cache context for one optimizer LLM call — the kwargs that always travel
    together. ``cache`` is consulted only when non-``None``, so omitting it silently re-spends."""

    ledger: CycleEventLog | None = None
    round_num: int | None = None
    candidate_idx: int | None = None
    cache: OptimizerReuseCache | None = None
    # `DispatchHub.fill`'s third return value, measured — the composition behind `prompt_chars`.
    # It rides the context rather than `trace_meta` because it belongs on the START record: the
    # over-budget warning fires there, and a breakdown that lands only after the call cannot
    # explain the warning the operator is reading.
    injection_chars: dict[str, int] = field(default_factory=dict)


_LLM_DEFAULTS: dict[str, Any] = {"temperature": 0.0}


# One logical call may fire ONE schema-repair retry inside `chat()`. `OPTIMIZER_CALL_DEADLINE_S`
# is the per-round-trip ceiling, so the logical-call wall must budget BOTH or a healthy-but-slow
# reasoning model that needs a repair false-halts with neither round-trip having hung.
_MAX_ROUND_TRIPS_PER_CALL = 2

# Head-cap on the thinking channel before it rides the ledger: a reasoning model can emit tens
# of KB per call into every round file, and the head is where the approach is stated. Matches
# TermNorm's `reasoning_trace_cap` so both channels truncate alike. ANALYTICAL ONLY — see
# ``LLMResponse.reasoning``: nothing may branch on this value.
_REASONING_LEDGER_CAP = 4000


async def _chat_under_deadline(
    llm_client: LLMClientBase,
    *,
    node_label: str,
    **chat_kwargs: Any,
) -> LLMResponse:
    """The provider SDK's ``timeout`` is a per-read-gap bound, so a slowly streaming reasoning model
    never trips it; this is the wall clock. A first timeout retries, and that retry hits the ledger."""
    budget_s = OPTIMIZER_CALL_DEADLINE_S * _MAX_ROUND_TRIPS_PER_CALL
    for attempt in range(2):
        try:
            async with asyncio.timeout(budget_s):
                return await llm_client.chat(**chat_kwargs)
        except TimeoutError:
            if attempt == 0:
                logger.warning(
                    "optimizer call %s exceeded the %.0fs deadline — retrying once",
                    node_label,
                    budget_s,
                )
                emit_round_warning(
                    kind="optimizer_deadline_retry",
                    message=(
                        f"optimizer call {node_label} exceeded its {budget_s:.0f}s wall and "
                        f"was retried once — this round spent up to {budget_s * 2:.0f}s on it"
                    ),
                    detail={"node": node_label, "budget_s": budget_s, "attempt": 1},
                )
                continue
            logger.error(
                "optimizer call %s exceeded the %.0fs deadline twice — halting",
                node_label,
                budget_s,
            )
            raise
    raise AssertionError("unreachable — the loop returns or raises on every path")


def _ledger_response_payload(response: LLMResponse) -> Any:
    """Materialize ``response.parsed`` into a JSON-safe shape for the ledger — typed instances dump,
    raw dicts pass through, and a text-mode call falls back to the raw content string."""
    if response.parsed is None:
        return response.content
    if isinstance(response.parsed, BaseModel):
        return response.parsed.model_dump()
    return response.parsed


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
    """LLM call with config-driven defaults; precedence ``_LLM_DEFAULTS < config < overrides``, and
    ``provider``/``model`` resolve from the node's config, so the client is built here, not passed."""
    if context is None:
        context = LLMCallContext()
    if config is None:
        if node:
            schema_node = get_optimizer_schema().get_node(node)
            if schema_node is None:
                raise KeyError(f"Unknown optimizer node: {node}")
            config = schema_node.current_config
            if response_model is None:
                response_model = OPTIMIZER_RESPONSE_MODELS.get(node)
        else:
            config = {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}
    # The outer L4 cycle evolving the inner OPTIMIZER's model as a searchpoint: ONE model the
    # outer carrier node set, fanned onto every inner node. Beats the node's file config, stays
    # UNDER the instrument clamp below, which pins only temperature+seed. `hash_call` already
    # keys on `merged["model"]`, so the swap gets its own cache key for free.
    if node and (specimen := resolve_node_override(node)).model:
        merged["model"] = specimen.model
        if specimen.provider:
            merged["provider"] = specimen.provider
    # The inner-cycle determinism clamp, applied LAST so it beats both the node's file config
    # and any per-call override — notably `l1_generate`'s `temperature=creativity`, the
    # dominant run-to-run noise source. Bound only inside an inner asyncio task.
    if config_overrides := get_optimizer_config_overrides():
        merged = {**merged, **config_overrides}
    llm_client = get_llm_client(merged["provider"])

    cache_key: str | None = None
    cached_payload: dict[str, Any] | None = None
    if context.cache is not None:
        cache_key = hash_call(
            messages=messages,
            model=merged.get("model"),
            provider=merged["provider"],
            temperature=merged["temperature"],
            json_schema=response_schema,
            response_model=response_model.__name__ if response_model else None,
            seed=merged.get("seed"),
        )
        cached_payload = context.cache.load(cache_key)

    _t0 = time.monotonic()

    # Pairs the LLMCallStartRecord — appended BEFORE the SDK call, so `in_flight` is readable
    # mid-call — with the eventual LLMCallRecord. Empty when no ledger is bound.
    call_id = uuid.uuid4().hex if context.ledger is not None else ""

    if cached_payload is not None:
        response = LLMResponse.model_validate(cached_payload)
        # ``parsed`` is typed ``Any``, so `model_validate` leaves the saved dict a dict.
        # Re-validate against the known model so consumers keep attribute access.
        if response_model is not None and isinstance(response.parsed, dict):
            response.parsed = response_model.model_validate(response.parsed)
        duration_s = round(time.monotonic() - _t0, 2)
        logger.debug("OptimizerReuseCache hit for %s (%s)", node or "llm_call", cache_key)
    else:
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        if context.ledger is not None:
            context.ledger.append(
                LLMCallStartRecord(
                    call_id=call_id,
                    node=node or "llm_call",
                    round=context.round_num,
                    candidate_idx=context.candidate_idx,
                    model=merged.get("model"),
                    started_at_ms=int(time.time() * 1000),
                    prompt_chars=prompt_chars,
                    injection_chars=dict(context.injection_chars),
                )
            )
        # Lets the operator tell an in-flight call from a frozen process without opening
        # dashboard.json. The node's OWN ceiling, not one line across all of them: crossing it
        # means a bound at some item's PRODUCTION site failed, and the fix is there.
        budget = OPTIMIZER_PROMPT_BUDGET_CHARS.get(node or "")
        oversize = budget is not None and prompt_chars > budget
        log = logger.warning if oversize else logger.info
        # An over-budget line that does not name the panel sends the reader to go and find it,
        # which is how this warning came to fire on 23% of `l1_generate` calls unacted-on. The
        # heaviest three are printed only on the warning path, so a healthy call stays one line.
        heaviest = (
            " · heaviest: "
            + ", ".join(
                f"{name} {chars:,}c"
                for name, chars in sorted(context.injection_chars.items(), key=lambda kv: -kv[1])[
                    :3
                ]
            )
            if oversize and context.injection_chars
            else ""
        )
        log(
            "→ optimizer call: %s · %s · %d-char prompt%s%s",
            node or "llm_call",
            merged["model"],
            prompt_chars,
            f" (over its {budget}-char budget — mandatory floor)" if oversize else "",
            heaviest,
        )
        # Keeps a live elapsed counter on both surfaces while the SDK call blocks for minutes.
        # Cancelled on every path by the `finally` below.
        heartbeat_task: asyncio.Task[None] | None = None
        if context.ledger is not None:
            heartbeat_task = asyncio.create_task(
                heartbeat(
                    context.ledger,
                    call_id=call_id,
                    node=node or "llm_call",
                    round_num=context.round_num,
                    start_monotonic=_t0,
                    # WHO the wait belongs to. A bare tick proves the process is alive and
                    # says nothing about why it is quiet, so a slow provider read as a stalled
                    # loop on every surface — an operator called a healthy 4-minute call a hang,
                    # which is the whole reason this argument exists. The model is knowable only
                    # here. The elapsed is NOT composed in: it rides `elapsed_s` on the same
                    # record, so no duration formatter is duplicated down into this layer.
                    detail_fn=lambda: (
                        f"provider {merged.get('model') or '(unnamed)'} has not answered"
                    ),
                )
            )
        # Bounded honor-Retry-After loop: if the header is missing or attempts run out, the SDK
        # exception surfaces unchanged.
        try:
            for attempt in range(MAX_429_ATTEMPTS):
                try:
                    response = await _chat_under_deadline(
                        llm_client,
                        node_label=node or "llm_call",
                        messages=messages,
                        model=merged.get("model"),
                        temperature=merged["temperature"],
                        max_tokens=merged.get("max_tokens"),
                        response_model=response_model,
                        response_schema=response_schema,
                        reasoning_effort=merged.get("reasoning_effort"),
                        seed=merged.get("seed"),
                    )
                    break
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 429:
                        raise
                    resp = getattr(exc, "response", None)
                    headers = getattr(resp, "headers", None) if resp is not None else None
                    body = getattr(resp, "text", None) if resp is not None else None
                    if body is None:
                        body = str(exc)
                    decision = decide_429_wait(headers, body, attempt)
                    if decision is None:
                        raise
                    label = node or "llm_call"
                    logger.warning(
                        "Rate limit on %s [%s] (attempt %d/%d); waiting %.1fs",
                        label,
                        decision.scope,
                        attempt + 1,
                        MAX_429_ATTEMPTS,
                        decision.seconds,
                    )
                    await wait_with_countdown(decision.seconds, f"{label} {decision.scope}")
        except OptimizerPromptParseError as parse_err:
            # A call that failed to parse was billed exactly like one that parsed, and the
            # raise happens upstream of the success path's usage block — so this is the ONLY
            # metering point on this path, and without it every malformed response is invisible
            # to the ledger, the dashboard and the spend gate alike.
            logger.error(
                "%s: optimizer call failed to parse — %s",
                node or "llm_call",
                parse_err.diagnosis(),
            )
            emit_token_usage(
                node=node or "llm_call",
                kind="optimizer",
                input_tokens=parse_err.prompt_tokens,
                output_tokens=parse_err.completion_tokens,
                reasoning_tokens=parse_err.reasoning_tokens,
                duration_s=round(time.monotonic() - _t0, 2),
                model=parse_err.model or merged.get("model"),
                provider=merged["provider"],
            )
            raise
        finally:
            # Whether the call succeeded or raised — an in-flight task would otherwise survive
            # the function exit and keep appending against a closed call.
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The cancel is expected; anything else is a real fault (a failed ledger
                    # append) that would otherwise vanish on this teardown path.
                    logger.warning(
                        "heartbeat task for %s raised on teardown",
                        node or "llm_call",
                        exc_info=True,
                    )

        duration_s = round(time.monotonic() - _t0, 2)

    # THE metering point: both branches converge here, so a round-trip is metered by ARRIVING
    # rather than by each branch remembering to. Only the parse failure skips it, and it meters
    # itself before raising. A cache hit is metered too, flagged — it spends nothing, but the
    # search still MADE the call, so incurred cost stays invariant to our cache history and the
    # always-warmest L4 origin arm does not read as free.
    emit_token_usage(
        node=node or "llm_call",
        kind="optimizer",
        input_tokens=response.usage.get("prompt_tokens", 0),
        output_tokens=response.usage.get("completion_tokens", 0),
        reasoning_tokens=response.usage.get("reasoning_tokens", 0),
        cache_read_tokens=response.usage.get("cache_read_tokens", 0),
        cache_write_tokens=response.usage.get("cache_write_tokens", 0),
        provider=merged["provider"],
        duration_s=duration_s,
        model=response.model,
        cost_usd=response.cost_usd,
        cached=cached_payload is not None,
    )

    # Meter FIRST, then store: the provider has already billed this call, so nothing that can
    # fail belongs between the response and its record. `cache.save` writes a file, and a disk
    # error above the emit loses the row silently — a missing record reads exactly like a call
    # that never happened.
    # Never cache a response carrying no payload. Empty content is a TRANSIENT provider
    # failure, and storing it makes it PERMANENT: the key is the prompt hash, so every later
    # call replays the emptiness and the caller sees a zero-candidate round forever. The cache
    # is tenant-global, so this outlives the run that hit it.
    usable = bool(response.content.strip()) or response.parsed is not None
    if cached_payload is None and context.cache is not None and cache_key is not None and usable:
        context.cache.save(cache_key, response.model_dump())

    if context.ledger is not None:
        payload: dict[str, Any] = {
            "type": node or "llm_call",
            "config": {
                "model": merged.get("model"),
                "temperature": merged["temperature"],
                "max_tokens": merged.get("max_tokens"),
            },
            "response": _ledger_response_payload(response),
            "usage": response.usage,
            "model": response.model,
            "duration_s": duration_s,
            # Non-zero ⇒ the JSON only landed after an extra round-trip — the audit trail's
            # read on prompt parse quality, rolled up per cycle in ``review.md``.
            "schema_repair_attempts": response.schema_repair_attempts,
        }
        # EVIDENCE FOR A HUMAN, never an input to the loop: nothing downstream reads this key
        # and nothing may start. Omitted when empty so a non-reasoning model's block stays clean.
        if response.reasoning:
            payload["reasoning"] = response.reasoning[:_REASONING_LEDGER_CAP]
        if cached_payload is not None:
            payload["cached"] = True
        if trace_meta:
            payload.update(trace_meta)
        else:
            payload["messages"] = messages
        context.ledger.append(
            LLMCallRecord(
                node=node or "llm_call",
                round=context.round_num,
                candidate_idx=context.candidate_idx,
                call_id=call_id,
                payload=payload,
            )
        )

    return response


async def run_optimizer_node(
    *,
    template_name: str,
    prompt_vars: dict[str, Any],
    temperature: float | None = None,
    response_model: type[BaseModel] | None = None,
    response_schema: dict[str, Any] | None = None,
    user_content: str | None = None,
    context: LLMCallContext | None = None,
    template: PromptTemplate | None = None,
) -> tuple[Any, str, int]:
    """Load prompt template, compile, call → ``(parsed, prompt_text, repair_attempts)``. A non-zero
    third element means a full schema-repair round-trip was paid — roughly twice the cost."""
    if template is None:
        template = load_optimizer_prompt(template_name)
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
            "template_fields": template.prompt_field_dict(),
            "variables": prompt_vars,
        },
        **overrides,
    )
    return extract_parsed_json(response), prompt, response.schema_repair_attempts
