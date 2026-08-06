"""The optimizer LLM call itself — ``llm_call`` + ``run_optimizer_node``.

``llm_call`` is the chokepoint: every optimizer prompt call goes through it for
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
from dataclasses import dataclass
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
    OPTIMIZER_PROMPT_WARN_CHARS,
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
from promptpotter.infrastructure.store.stores import OptimizerCallCache, hash_call

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["LLMCallContext", "llm_call", "run_optimizer_node"]


@dataclass(frozen=True)
class LLMCallContext:
    """Audit + cache context for one optimizer LLM call.

    Bundles the four kwargs that always travel together across
    ``llm_call`` and ``run_optimizer_node``: the ledger that receives
    start/progress/end records, the round/candidate identity those records
    carry, and the cross-cycle response cache. Defaults to all-``None`` for
    callers that need no audit trail; note ``llm_call`` consults and writes
    ``cache`` only when it is not ``None``, so an omitted cache silently
    re-spends on every call.
    """

    ledger: CycleEventLog | None = None
    round_num: int | None = None
    candidate_idx: int | None = None
    cache: OptimizerCallCache | None = None


_LLM_DEFAULTS: dict[str, Any] = {"temperature": 0.0}


# A single logical optimizer call may fire ONE schema-repair retry inside
# chat() — a full second round-trip the client appends on schema-noncompliant
# output (openai_compat.py, ~2x latency; capped at one). OPTIMIZER_CALL_DEADLINE_S
# is the per-round-trip ceiling; the logical-call wall budgets for both so a
# healthy-but-slow reasoning model that needs a repair doesn't false-halt (the
# bug: initial ~150s + repair ~150s = ~300s tripped a flat 180s ceiling even
# though neither round-trip hung).
_MAX_ROUND_TRIPS_PER_CALL = 2

# Head-cap on the model's own thinking channel before it rides the ledger. A
# reasoning model can emit tens of KB per call and this lands in every round file;
# the head is where the approach is stated, so a cap keeps the audit twin readable
# without losing the part an operator reads. Matches TermNorm's reasoning_trace_cap
# so both thinking channels truncate alike. ANALYTICAL ONLY — see
# ``LLMResponse.reasoning``: nothing may branch on this value.
_REASONING_LEDGER_CAP = 4000


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

    **The retry lands on the ledger.** It doubles this call's wall budget, and
    under L4 that time is spent against the OUTER per-sample wall — so a retry
    only the server log knows about is a state nothing downstream can read.

    The wall budget is the per-round-trip ceiling times the worst-case
    round-trip count, because one ``chat()`` may include a schema-repair
    retry (a second full call); see ``_MAX_ROUND_TRIPS_PER_CALL``.
    """
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
    """LLM call with config-driven defaults; precedence: _LLM_DEFAULTS < config < overrides.

    ``provider`` and ``model`` resolve from the optimizer node's config (sourced
    from ``promptpotter/assets/optimizer/pipeline.yaml`` via ``node``) like every other
    tunable — the client is built here from ``merged["provider"]``, not passed in.

    *response_model* defaults to ``OPTIMIZER_RESPONSE_MODELS[node]`` when a
    *node* is supplied — every optimizer node has a Pydantic model, so
    callers get a typed ``response.parsed`` for free. Pass *response_schema*
    to override the wire JSON Schema (used by ``l1_generate`` whose schema
    is built dynamically per backend ``PipelineSchema``); the parsed
    content is still validated against *response_model* for type-level
    guarantee on the Python side.

    *context* bundles the four call-audit kwargs (ledger, round, candidate,
    cache). When ``context.cache`` is set, the resolved ``(messages, model,
    temperature, response_model, response_schema, provider)`` tuple is
    hashed and looked up before firing the LLM; a hit replays the stored
    ``LLMResponse`` (and emits an ``LLMCallRecord`` with ``cached: true``)
    instead of calling the provider. When ``context.ledger`` is set, an
    :class:`LLMCallRecord` is appended after each successful call or cache
    hit — the audit-trail projection picks it up via ``on_record`` and
    shapes it into the round's ``nodes.<node>`` block. Callers MUST pass
    the ledger via ``context``; the direct ``recorder.add_action`` write
    path is gone.
    """
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
    # Specimen model — the outer L4 cycle evolving the inner OPTIMIZER's model as a searchpoint.
    # A SINGLE model the outer carrier node set, fanned onto every inner node (`resolve_node_override`
    # returns it for all). Beats the node's file config, stays UNDER the instrument clamp below
    # (which pins only temperature+seed, so no collision). `None` on every normal call → no-op;
    # `hash_call` already keys on `merged["model"]`, so the swap gets its own cache key + searchpoint
    # identity for free.
    if node and (specimen := resolve_node_override(node)).model:
        merged["model"] = specimen.model
        if specimen.provider:
            merged["provider"] = specimen.provider
    # Per-run tunable clamp (L4 inner-cycle determinism), applied LAST so it beats
    # both the node's file config and any per-call override — notably `l1_generate`'s
    # `temperature=creativity`, the dominant run-to-run noise source. Bound only inside
    # an inner asyncio task (`shared.instrument.enter_instrument_mode`); `None` on every
    # normal call, so this is a no-op outside L4 inner campaigns.
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

    # ``call_id`` pairs the LLMCallStartRecord (appended before the SDK call,
    # so the operator/AI can read "currently calling X for Ys" off
    # dashboard.json::in_flight even mid-call) with the eventual
    # LLMCallRecord. Empty string when no ledger is bound (call-site tests,
    # one-shot tools).
    call_id = uuid.uuid4().hex if context.ledger is not None else ""

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
                )
            )
        # Pre-call info line — surfaces what we're waiting on so the operator
        # can distinguish "in-flight LLM call" from "frozen process" without
        # opening dashboard.json. Routes through Python logging to the terminal.
        # An oversized prompt logs at warn level so it stands out the same way
        # the CLI marker turns yellow.
        oversize = prompt_chars > OPTIMIZER_PROMPT_WARN_CHARS
        log = logger.warning if oversize else logger.info
        log(
            "→ optimizer call: %s · %s · %d-char prompt%s",
            node or "llm_call",
            merged["model"],
            prompt_chars,
            f" (over the {OPTIMIZER_PROMPT_WARN_CHARS}-char warn line)" if oversize else "",
        )
        # Heartbeat task — appends LLMCallProgressRecord every
        # HEARTBEAT_INTERVAL_S so the CLI display + webapp dashboard
        # show a live elapsed counter while the SDK call blocks for
        # 30-200s. Cancelled on completion (any path) by the finally
        # block below; short calls under HEARTBEAT_INTERVAL_S never tick.
        heartbeat_task: asyncio.Task[None] | None = None
        if context.ledger is not None:
            heartbeat_task = asyncio.create_task(
                heartbeat(
                    context.ledger,
                    call_id=call_id,
                    node=node or "llm_call",
                    round_num=context.round_num,
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
            # A call that failed to parse was billed exactly like one that parsed. Meter it
            # here — the raise happens upstream of the success path's usage block, so this is
            # the ONLY metering point on the parse-failure path. Without it the two round-trips
            # of every empty/malformed response are invisible to the ledger, to
            # `dashboard.json::spend`, and to the `spend_budget_usd` gate meant to bound the run.
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

    # THE metering point: both branches above converge here, so a round-trip that returns is
    # metered by arriving rather than by each branch remembering to. The one path that skips it
    # is the parse failure, which raises and meters itself — it never reaches this line.
    #
    # A cache hit is metered too, flagged: it spends nothing, but the search still MADE the call
    # and the cached payload carries the tokens it cost, so incurred cost stays invariant to our
    # cache history — without it the L4 origin arm, always the warmest, reads as free. `cached`
    # is the same expression the ledger payload below stamps, so the two cannot disagree.
    # OpenRouter reports `usage.cost`/`total_cost` already in USD; other providers leave it
    # empty and the spend projection rate-tables the tokens.
    cost_raw = response.usage.get("cost") or response.usage.get("total_cost")
    emit_token_usage(
        node=node or "llm_call",
        kind="optimizer",
        input_tokens=response.usage.get("prompt_tokens", 0),
        output_tokens=response.usage.get("completion_tokens", 0),
        reasoning_tokens=response.usage.get("reasoning_tokens", 0),
        provider=merged["provider"],
        duration_s=duration_s,
        model=response.model,
        cost_usd=float(cost_raw) if cost_raw is not None else None,
        cached=cached_payload is not None,
    )

    # Meter first, THEN store — the provider has already billed this call, so nothing that
    # can fail belongs between the response and its record. `cache.save` writes a file, and
    # with it above the emit a disk error lost the row for a call we paid for: no ledger
    # entry, no dashboard increment, no contribution to the budget gate, and the loss is
    # silent because a missing record reads exactly like a call that never happened.
    #
    # Never cache a response that carries no payload. A provider returning empty content
    # (`finish_reason: stop`, 2 completion tokens, schema repair exhausted) is a TRANSIENT
    # failure; storing it converts that flake into a PERMANENT one, because the key is the
    # prompt hash and every later call replays the emptiness. The caller then sees a
    # zero-candidate round forever and reads it as provider flakiness. Harmless while the
    # cache was per-sandbox and thrown away with the run; load-bearing now that it is
    # tenant-global (`Stores.shared_root`). A hit has nothing to store — it came FROM here.
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
            # Non-zero ⇒ the parsed JSON only landed after an extra
            # round-trip; surfaces L1-prompt parse quality in the audit
            # trail and feeds the per-cycle schema-repair roll-up in
            # ``review.md``.
            "schema_repair_attempts": response.schema_repair_attempts,
        }
        # The model's own thinking, carried to the audit twin + the operator's node
        # detail. Omitted when empty so a non-reasoning model's block stays clean.
        # It is EVIDENCE FOR A HUMAN, never an input to the loop — nothing downstream
        # reads this key, and nothing may start (see ``LLMResponse.reasoning``).
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
    """Load prompt template, compile, call LLM → (parsed_result, prompt_text, repair_attempts).

    Provider, model, and the default temperature come from the node's config in
    ``promptpotter/assets/optimizer/pipeline.yaml`` (resolved inside :func:`llm_call`).
    Pass *temperature* only to override that default — the L1 generator does, with
    its escalation-driven creativity; every other node defers to the file.

    The response model is looked up by ``template_name`` in
    :data:`OPTIMIZER_RESPONSE_MODELS`; the typed Pydantic instance lands on
    ``LLMResponse.parsed`` and is returned to the caller as the first
    element of the tuple. Callers that need a dict shape (the
    audit-trail) call ``.model_dump()`` themselves.

    The third element is ``response.schema_repair_attempts`` — non-zero ⇒ the
    provider's first response failed schema validation and a full repair
    round-trip was paid (~2x cost + latency; see ``openai_compat.py``). The
    loop sites record it via the ledger payload and discard the return value;
    the non-ledger ``checkin`` resolver reads it live to surface a degraded
    turn to the operator (``origin_resolve.py``).

    When *template* is provided, it overrides the load-from-name path (used
    by L1's ``l1_template_override`` channel — L2 can rewrite L1's prompt
    body by writing ``template_override`` on its OSP). The trace metadata
    still records ``template_name`` so observability stays continuous.

    When *response_schema* is supplied, it overrides the wire JSON Schema
    derived from the response model — used by ``l1_generate`` whose
    schema is built dynamically per backend ``PipelineSchema``.

    *response_model* overrides the ``OPTIMIZER_RESPONSE_MODELS`` lookup. ``l1_generate``
    passes one when a field RENAME is in force: the wire schema advertises the new keys and
    the model aliases them back (``build_l1_response_model``). Both derive from
    ``effective_l1_field_names``, so the two can never disagree.

    *context* bundles the audit/cache kwargs (ledger, round, candidate,
    cache); forwarded verbatim to :func:`llm_call`.
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
