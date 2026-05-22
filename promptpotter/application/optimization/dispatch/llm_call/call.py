"""The optimizer LLM call itself — ``llm_call`` + ``run_optimizer_node``.

``llm_call`` is the chokepoint: every meta-prompt call goes through it for
429-retry, the wall-clock deadline, the in-flight heartbeat, token-usage
emit, the audit-trail ledger record, and the cross-cycle response cache.
``run_optimizer_node`` is the template → compile → call → parse wrapper the
L1/L2/L3 sites use.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    get_optimizer_schema,
    load_optimizer_prompt,
)
from promptpotter.application.optimization.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)
from promptpotter.config.settings import (
    OPTIMIZER_CALL_DEADLINE_S,
    OPTIMIZER_PROMPT_WARN_CHARS,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.run_records import (
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.infrastructure.llm import (
    MAX_429_ATTEMPTS,
    LLMClientBase,
    LLMResponse,
    TokenUsage,
    diagnose_rate_limit_scope,
    emit_token_usage,
    extract_parsed_json,
    parse_retry_after,
    wait_with_countdown,
)
from promptpotter.infrastructure.store.stores import OptimizerCallCache, hash_call

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["llm_call", "run_optimizer_node"]


_LLM_DEFAULTS: dict[str, Any] = {"temperature": 0.0}


HEARTBEAT_INTERVAL_S = 15.0
"""Seconds between in-flight progress ticks.

15s is a compromise: short enough that the operator sees a fresh
counter several times during a typical 60-120s optimizer call, long
enough that 5-15s critique calls finish without ever emitting one. The
ledger pays one append per tick - at four optimizer calls/round and
~90s average call duration that's ~24 records/round, negligible."""


async def _heartbeat(
    ledger: CycleEventLog,
    *,
    call_id: str,
    node: str,
    round_num: int | None,
    start_monotonic: float,
) -> None:
    """Periodically append :class:`LLMCallProgressRecord` while a call is open.

    Cancelled by the ``finally`` block in :func:`llm_call` when the SDK
    call returns (success, last 429 retry, or raise). The
    :class:`asyncio.CancelledError` is swallowed at the cancel site so
    cancellation looks like a clean exit.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        elapsed = time.monotonic() - start_monotonic
        ledger.append(
            LLMCallProgressRecord(
                call_id=call_id,
                node=node,
                round=round_num,
                elapsed_s=elapsed,
            )
        )


async def _chat_under_deadline(
    llm_client: LLMClientBase,
    *,
    node_label: str,
    **chat_kwargs: Any,
) -> LLMResponse:
    """Run one optimizer chat call under a total wall-clock deadline.

    The provider SDK's ``timeout`` is a per-read-gap timeout, not a total
    one: a reasoning model streaming a large output slowly never trips it
    and the call can hang indefinitely (a live ``l1_generate`` sat 315s+
    with no response). :func:`asyncio.timeout` is the hard wall-clock
    bound the SDK's timeout is not.

    A first timeout is treated as transient (provider hiccup) and the call
    is retried once; a second raises :class:`TimeoutError` to the caller.
    :func:`run_round_loop` turns that into ``StopReason.OPTIMIZER_TIMEOUT``
    — a graceful, operator-recoverable halt.
    """
    for attempt in range(2):
        try:
            async with asyncio.timeout(OPTIMIZER_CALL_DEADLINE_S):
                return await llm_client.chat(**chat_kwargs)
        except TimeoutError:
            if attempt == 0:
                logger.warning(
                    "optimizer call %s exceeded the %.0fs deadline — retrying once",
                    node_label,
                    OPTIMIZER_CALL_DEADLINE_S,
                )
                continue
            logger.error(
                "optimizer call %s exceeded the %.0fs deadline twice — halting",
                node_label,
                OPTIMIZER_CALL_DEADLINE_S,
            )
            raise
    raise AssertionError("unreachable — the loop returns or raises on every path")


def _ledger_response_payload(response: LLMResponse) -> Any:
    """Materialize ``response.parsed`` into a JSON-safe shape for the ledger.

    Typed Pydantic instances dump cleanly; raw dicts pass through; ``None``
    (text-mode call) falls back to the raw content string. The prior
    ``json.loads(response.content)`` re-parse is unnecessary now that
    ``chat()`` populates ``parsed`` itself.
    """
    if response.parsed is None:
        return response.content
    if isinstance(response.parsed, BaseModel):
        return response.parsed.model_dump()
    return response.parsed


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict[str, Any] | None = None,
    trace_meta: dict[str, Any] | None = None,
    response_model: type[BaseModel] | None = None,
    response_schema: dict[str, Any] | None = None,
    ledger: CycleEventLog | None = None,
    round_num: int | None = None,
    candidate_idx: int | None = None,
    cache: OptimizerCallCache | None = None,
    **overrides: Any,
) -> LLMResponse:
    """LLM call with config-driven defaults; precedence: _LLM_DEFAULTS < config < overrides.

    *response_model* defaults to ``OPTIMIZER_RESPONSE_MODELS[node]`` when a
    *node* is supplied — every optimizer node has a Pydantic model, so
    callers get a typed ``response.parsed`` for free. Pass *response_schema*
    to override the wire JSON Schema (used by ``l1_generate`` whose schema
    is built dynamically per backend ``PipelineSchema``); the parsed
    content is still validated against *response_model* for type-level
    guarantee on the Python side.

    When *cache* is provided, the resolved ``(messages, model, temperature,
    response_model, response_schema, provider)`` tuple is hashed and
    looked up before firing the LLM. A hit replays the stored
    ``LLMResponse`` (and emits an ``LLMCallRecord`` with ``cached: true``)
    instead of calling the provider — cross-cycle and cross-fork by
    construction.

    When *ledger* is provided, an :class:`LLMCallRecord` is appended
    after each successful call (or cache hit) — the audit-trail
    projection picks it up via ``on_record`` and shapes it into the
    round's ``nodes.<node>`` block. Callers MUST pass the ledger; the
    direct ``recorder.add_action`` write path is gone.
    """
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

    cache_key: str | None = None
    cached_payload: dict[str, Any] | None = None
    if cache is not None:
        cache_key = hash_call(
            messages=messages,
            model=merged.get("model"),
            provider=type(llm_client).__name__,
            temperature=merged["temperature"],
            json_schema=response_schema,
            response_model=response_model.__name__ if response_model else None,
        )
        cached_payload = cache.load(cache_key)

    _t0 = time.monotonic()

    # ``call_id`` pairs the LLMCallStartRecord (appended before the SDK call,
    # so the operator/AI can read "currently calling X for Ys" off
    # dashboard.json::in_flight even mid-call) with the eventual
    # LLMCallRecord. Empty string when no ledger is bound (call-site tests,
    # one-shot tools).
    call_id = uuid.uuid4().hex if ledger is not None else ""

    if cached_payload is not None:
        response = LLMResponse.model_validate(cached_payload)
        # ``response.parsed`` was dumped to a dict by ``model_dump()`` at save
        # time; ``LLMResponse.parsed`` is typed ``Any``, so model_validate
        # doesn't re-instantiate it as a BaseModel. Re-validate against the
        # known response_model so consumers can use attribute access.
        if response_model is not None and isinstance(response.parsed, dict):
            response.parsed = response_model.model_validate(response.parsed)
        duration_s = round(time.monotonic() - _t0, 2)
        logger.debug("OptimizerCallCache hit for %s (%s)", node or "llm_call", cache_key)
    else:
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        if ledger is not None:
            ledger.append(
                LLMCallStartRecord(
                    call_id=call_id,
                    node=node or "llm_call",
                    round=round_num,
                    candidate_idx=candidate_idx,
                    model=merged.get("model"),
                    started_at_ms=int(time.time() * 1000),
                    prompt_chars=prompt_chars,
                )
            )
        # Pre-call info line — surfaces what we're waiting on so the operator
        # can distinguish "in-flight LLM call" from "frozen process" without
        # opening dashboard.json. Lands in terminal AND output.log. An
        # oversized prompt logs at warn level so it stands out in output.log
        # the same way the CLI marker turns yellow.
        oversize = prompt_chars > OPTIMIZER_PROMPT_WARN_CHARS
        log = logger.warning if oversize else logger.info
        log(
            "→ optimizer call: %s · %s · %d-char prompt%s",
            node or "llm_call",
            merged.get("model") or "(default)",
            prompt_chars,
            f" (over the {OPTIMIZER_PROMPT_WARN_CHARS}-char warn line)" if oversize else "",
        )
        # Heartbeat task — appends LLMCallProgressRecord every
        # HEARTBEAT_INTERVAL_S so the CLI display + webapp dashboard
        # show a live elapsed counter while the SDK call blocks for
        # 30-200s. Cancelled on completion (any path) by the finally
        # block below; short calls under HEARTBEAT_INTERVAL_S never tick.
        heartbeat_task: asyncio.Task[None] | None = None
        if ledger is not None:
            heartbeat_task = asyncio.create_task(
                _heartbeat(
                    ledger,
                    call_id=call_id,
                    node=node or "llm_call",
                    round_num=round_num,
                    start_monotonic=_t0,
                )
            )
        # 429 honor-Retry-After loop, bounded. Server sets the header per RFC 7231;
        # if missing or attempts run out, surface the SDK exception unchanged.
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
                    wait = parse_retry_after(headers)
                    if wait is None or wait <= 0 or attempt == MAX_429_ATTEMPTS - 1:
                        raise
                    kind = diagnose_rate_limit_scope(headers, body)
                    label = node or "llm_call"
                    logger.warning(
                        "Rate limit on %s [%s] (attempt %d/%d); waiting %.1fs",
                        label,
                        kind,
                        attempt + 1,
                        MAX_429_ATTEMPTS,
                        wait,
                    )
                    await wait_with_countdown(wait + 1.0, f"{label} {kind}")
        finally:
            # Cancel the heartbeat whether the call succeeded or raised —
            # an in-flight task would otherwise survive the function exit
            # and keep appending progress records against a closed call.
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The cancel above is expected; anything else means the
                    # heartbeat hit a real fault (e.g. a failed ledger append)
                    # that would otherwise vanish on this teardown path.
                    logger.warning(
                        "heartbeat task for %s raised on teardown",
                        node or "llm_call",
                        exc_info=True,
                    )

        duration_s = round(time.monotonic() - _t0, 2)

        # OpenRouter returns ``usage.cost``/``total_cost`` with USD already
        # computed; other providers leave this slot empty and the spend
        # projection rate-tables the tokens.
        cost_raw = response.usage.get("cost") or response.usage.get("total_cost")
        emit_token_usage(
            TokenUsage(
                node=node or "llm_call",
                kind="optimizer",
                input_tokens=response.usage.get("prompt_tokens", 0),
                output_tokens=response.usage.get("completion_tokens", 0),
                duration_s=duration_s,
                model=response.model,
                cost_usd=float(cost_raw) if cost_raw is not None else None,
            )
        )

        if cache is not None and cache_key is not None:
            cache.save(cache_key, response.model_dump())

    if ledger is not None:
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
            # Non-zero ⇒ the parsed JSON only landed after an extra
            # round-trip; surfaces L1-prompt parse quality in the audit
            # trail and feeds the per-cycle schema-repair roll-up in
            # ``review.md``.
            "schema_repair_attempts": response.schema_repair_attempts,
        }
        if cached_payload is not None:
            payload["cached"] = True
        if trace_meta:
            payload.update(trace_meta)
        else:
            payload["messages"] = messages
        ledger.append(
            LLMCallRecord(
                node=node or "llm_call",
                round=round_num,
                candidate_idx=candidate_idx,
                call_id=call_id,
                payload=payload,
            )
        )

    return response


async def run_optimizer_node(
    *,
    template_name: str,
    prompt_vars: dict[str, Any],
    llm_client: LLMClientBase,
    model: str | None,
    temperature: float = 0.0,
    response_schema: dict[str, Any] | None = None,
    user_content: str | None = None,
    ledger: CycleEventLog | None = None,
    round_num: int | None = None,
    candidate_idx: int | None = None,
    template: PromptTemplate | None = None,
    optimizer_call_cache: OptimizerCallCache | None = None,
) -> tuple[Any, str]:
    """Load prompt template, compile, call LLM → (parsed_result, prompt_text).

    The response model is looked up by ``template_name`` in
    :data:`OPTIMIZER_RESPONSE_MODELS`; the typed Pydantic instance lands on
    ``LLMResponse.parsed`` and is returned to the caller as the first
    element of the tuple. Callers that need a dict shape (the
    audit-trail) call ``.model_dump()`` themselves.

    When *template* is provided, it overrides the load-from-name path (used
    by L1's ``l1_template_override`` channel — L2 can rewrite L1's prompt
    body by writing ``template_override`` on its OSP). The trace metadata
    still records ``template_name`` so observability stays continuous.

    When *response_schema* is supplied, it overrides the wire JSON Schema
    derived from the response model — used by ``l1_generate`` whose
    schema is built dynamically per backend ``PipelineSchema``.

    When *optimizer_call_cache* is provided, it is forwarded to :func:`llm_call`
    for content-addressed cross-cycle reuse of optimizer LLM responses.
    """
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
    response = await llm_call(
        llm_client,
        messages=messages,
        node=template_name,
        model=model,
        temperature=temperature,
        response_schema=response_schema,
        ledger=ledger,
        round_num=round_num,
        candidate_idx=candidate_idx,
        cache=optimizer_call_cache,
        trace_meta={
            "template_name": template_name,
            "template_fields": template.prompt_field_dict(),
            "variables": prompt_vars,
        },
    )
    return extract_parsed_json(response), prompt
