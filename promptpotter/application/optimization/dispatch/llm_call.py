"""Optimizer LLM chokepoint — every meta-prompt call goes through here.

``llm_call`` (429-retry + token emit + recorder + cross-cycle cache),
``run_optimizer_node`` (template → compile → call → parse), schema loader,
and Langfuse-production → local-manifest prompt loading.

Every optimizer node dispatches a Pydantic response model from
:data:`optimizer_schemas.OPTIMIZER_RESPONSE_MODELS` so the LLM call
returns a typed instance on ``LLMResponse.parsed`` — server-side
``response_format`` validation plus client-side ``model_validate`` on
the parsed JSON. The hand-rolled JSON repair path only runs when no
model is configured for the node.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.application.optimization.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)
from promptpotter.config.settings import (
    OPTIMIZER_CALL_DEADLINE_S,
    OPTIMIZER_PROMPT_WARN_CHARS,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
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

__all__ = [
    "compute_optimizer_prompt_hashes",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "llm_call",
    "load_optimizer_prompt",
    "push_all_to_langfuse",
    "run_optimizer_node",
]


_REPO_ROOT = Path(__file__).resolve().parents[4]
_PIPELINE_PATH = _REPO_ROOT / "datasets" / "_optimizer" / "pipeline.json"


@functools.lru_cache(maxsize=1)
def _load_optimizer_manifest() -> dict[str, Any]:
    """Read the on-disk optimizer-pipeline manifest (cached).

    Single source of truth for nodes, schemas, and prompts. Mirrors
    TermNorm's ``GET /pipeline`` response shape: ``nodes`` reference
    ``schema_family``/``schema_version`` + ``prompt_family``/``prompt_version``,
    and the bodies live in the top-level ``resolved_schemas`` /
    ``resolved_prompts`` registries.
    """
    return json.loads(_PIPELINE_PATH.read_text(encoding="utf-8"))


def _resolved_key(family: str, version: Any) -> str:
    return f"{family}/{version}" if version is not None else family


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """Load optimizer_pipeline.json as PipelineSchema (cached).

    Mirrors TermNorm's pipeline-schema convention: each node's structured
    output schema is referenced via ``config.schema_family`` /
    ``config.schema_version`` and resolved against the top-level
    ``resolved_schemas`` registry — same shape ``parse_pipeline_response``
    uses for backend pipelines, so the optimizer is itself a pipeline that
    can later be optimized.
    """
    from promptpotter.domain.pipeline_parsing import parse_resolved_schema
    from promptpotter.domain.pipeline_schema import PipelineNode

    data = _load_optimizer_manifest()
    resolved_schemas = data.get("resolved_schemas", {})

    nodes: list[PipelineNode] = []
    for name, node_data in data.get("nodes", {}).items():
        nc = node_data.get("config", {})
        kwargs: dict[str, Any] = {
            "name": name,
            "current_config": nc,
            "param_keys": set(node_data.get("optimizer", {}).get("param_keys", [])),
        }
        if sf := nc.get("schema_family"):
            key = _resolved_key(sf, nc.get("schema_version"))
            if key in resolved_schemas:
                kwargs["output_schema"] = parse_resolved_schema(resolved_schemas[key])
        nodes.append(PipelineNode(**kwargs))

    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )


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
    config: dict | None = None,
    trace_meta: dict | None = None,
    response_model: type[BaseModel] | None = None,
    response_schema: dict | None = None,
    ledger: CycleEventLog | None = None,
    round_num: int | None = None,
    candidate_idx: int | None = None,
    cache: OptimizerCallCache | None = None,
    **overrides,
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
    cached_payload: dict | None = None
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
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat_task

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
        payload: dict = {
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
    prompt_vars: dict,
    llm_client: LLMClientBase,
    model: str | None,
    temperature: float = 0.0,
    response_schema: dict | None = None,
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
    element of the tuple. Callers that need a dict shape (legacy
    consumers, audit-trail) call ``.model_dump()`` themselves.

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


_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


def _resolved_prompt_for_node(name: str) -> dict | None:
    """Look up a node's prompt body in ``resolved_prompts``.

    Joins the node's ``config.prompt_family``/``prompt_version`` against
    the manifest's ``resolved_prompts`` registry — same TermNorm-style
    indirection used for backend pipelines.
    """
    data = _load_optimizer_manifest()
    node_cfg = data.get("nodes", {}).get(name, {}).get("config", {})
    family = node_cfg.get("prompt_family")
    if not family:
        return None
    key = _resolved_key(family, node_cfg.get("prompt_version"))
    body = data.get("resolved_prompts", {}).get(key)
    return body if isinstance(body, dict) else None


@functools.lru_cache(maxsize=32)
def _load_local(name: str) -> PromptTemplate:
    body = _resolved_prompt_for_node(name)
    if body is None:
        raise KeyError(
            f"Optimizer prompt '{name}' not found in resolved_prompts registry "
            f"(check nodes.{name}.config.prompt_family/version)."
        )
    return PromptTemplate(**body)


@functools.cache
def _prompt_langfuse() -> Any:
    """Process-wide LangfuseLogger for optimizer prompt fetch/push (separate from trace logger)."""
    from promptpotter.infrastructure.tracing import LangfuseLogger

    return LangfuseLogger()


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch prompt from Langfuse 'production' label (None on any failure)."""
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        lf = _prompt_langfuse()
        if not lf.enabled or not lf.client:
            return None

        prompt_client = lf.client.get_prompt(
            name=f"{_LANGFUSE_PREFIX}{name}",
            label="production",
            cache_ttl_seconds=_LANGFUSE_CACHE_TTL,
        )
        config = getattr(prompt_client, "config", None)
        if not config or not isinstance(config, dict):
            return None

        return PromptTemplate(**config)
    except Exception:
        logger.debug("Langfuse prompt fetch failed for %s", name, exc_info=True)
        return None


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Load optimizer prompt: Langfuse production → manifest registry fallback.

    Every load runs through :func:`dispatch_hub.validate_template`, so a
    template that references a slot not in
    :data:`dispatch_hub.INJECTIONS` (and not in the per-template extras list)
    raises at load time rather than silently rendering empty.
    """
    from promptpotter.application.optimization.dispatch.hub import validate_template

    lf_prompt = _try_langfuse(name)
    template = lf_prompt or _load_local(name)
    validate_template(name, template)
    return template


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push manifest-registry prompt defaults to Langfuse; returns {name: success}."""
    lf = _prompt_langfuse()
    if not lf.enabled or not lf.client:
        logger.warning("push_all_to_langfuse: Langfuse not available")
        return {}

    results: dict[str, bool] = {}
    for name in list_optimizer_prompts():
        try:
            tpl = _load_local(name)
            lf.client.create_prompt(
                name=f"{_LANGFUSE_PREFIX}{name}",
                prompt=tpl.render(),
                config=tpl.model_dump(),
                labels=[label],
                tags=["optimizer", "meta-prompt"],
                commit_message=f"Push manifest default for {name}",
            )
            results[name] = True
            logger.info("Pushed optimizer prompt %s to Langfuse", name)
        except Exception:
            logger.warning("Failed to push %s to Langfuse", name, exc_info=True)
            results[name] = False

    # Clear local cache so next load picks up Langfuse versions
    _load_local.cache_clear()
    return results


def list_optimizer_prompts() -> list[str]:
    """Names of nodes that declare a ``prompt_family`` in the manifest."""
    data = _load_optimizer_manifest()
    return sorted(
        name
        for name, node in data.get("nodes", {}).items()
        if node.get("config", {}).get("prompt_family")
    )


def compute_optimizer_prompt_hashes() -> dict[str, str]:
    """SHA-256 (16-char prefix) of each optimizer prompt's loaded content.

    Hashes the deterministic ``model_dump_json()`` of the loaded
    ``PromptTemplate`` (so the hash reflects what was actually used,
    Langfuse-overridden or local). Persisted to ``index.json::final.prompt_hashes``
    so cross-cycle audits can join cycles by ``l1_generate_hash`` etc.
    """
    out: dict[str, str] = {}
    for name in list_optimizer_prompts():
        tpl = load_optimizer_prompt(name)
        out[name] = hashlib.sha256(tpl.model_dump_json().encode("utf-8")).hexdigest()[:16]
    return out
