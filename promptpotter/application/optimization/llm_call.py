"""Optimizer LLM call primitive + prompt loading.

The single chokepoint every optimizer node passes through. Two responsibilities:

1. **LLM call primitive** (``llm_call``, ``run_optimizer_node``,
   ``get_optimizer_schema``) — schema loader + 429-Retry-After loop +
   token emission + recorder hook + optional cross-cycle cache.

2. **Prompt loading** (``load_optimizer_prompt``, ``_load_local``,
   ``_try_langfuse``, ``push_all_to_langfuse``, ``list_optimizer_prompts``,
   ``compute_optimizer_prompt_hashes``) — Langfuse production label →
   local optimizer-manifest registry fallback.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm import (
    MAX_429_ATTEMPTS,
    LLMClientBase,
    LLMResponse,
    TokenUsage,
    emit_token_usage,
    extract_parsed_json,
    parse_retry_after,
    wait_with_countdown,
)
from promptpotter.infrastructure.store.optimizer_call_cache import (
    OptimizerCallCache,
    hash_call,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections import AuditTrailProjection

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


_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


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
    from promptpotter.application.pipeline_discovery import parse_resolved_schema
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


_LLM_DEFAULTS = {"temperature": 0.0, "output_format": "text"}


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict | None = None,
    trace_meta: dict | None = None,
    json_schema: dict | None = None,
    recorder: AuditTrailProjection | None = None,
    cache: OptimizerCallCache | None = None,
    **overrides,
) -> LLMResponse:
    """LLM call with config-driven defaults; precedence: _LLM_DEFAULTS < config < overrides.

    When *json_schema* is not passed and the node carries an
    ``output_schema.json_schema`` (resolved from the top-level
    ``resolved_schemas`` registry in ``optimizer_pipeline.json``), that
    schema is auto-pulled — same TermNorm-style indirection used for
    backend pipelines.

    When *cache* is provided, the resolved ``(messages, model, temperature,
    json_schema, provider)`` tuple is hashed and looked up before firing
    the LLM. A hit replays the stored ``LLMResponse`` (and feeds the
    recorder with ``cached: true``) instead of calling the provider —
    cross-cycle and cross-fork by construction.
    """
    if config is None:
        if node:
            schema_node = get_optimizer_schema().get_node(node)
            if schema_node is None:
                raise KeyError(f"Unknown optimizer node: {node}")
            config = schema_node.current_config
            if json_schema is None and schema_node.output_schema is not None:
                resolved = schema_node.output_schema.json_schema
                if resolved:
                    json_schema = resolved
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
            json_schema=json_schema,
        )
        cached_payload = cache.load(cache_key)

    _t0 = time.monotonic()

    if cached_payload is not None:
        response = LLMResponse.model_validate(cached_payload)
        duration_s = round(time.monotonic() - _t0, 2)
        logger.debug("OptimizerCallCache hit for %s (%s)", node or "llm_call", cache_key)
    else:
        effective_output_format = cast(
            Literal["text", "json", "json_schema"],
            "json_schema" if json_schema else merged["output_format"],
        )

        # 429 honor-Retry-After loop, bounded. Server sets the header per RFC 7231;
        # if missing or attempts run out, surface the SDK exception unchanged.
        for attempt in range(MAX_429_ATTEMPTS):
            try:
                response = await llm_client.chat(
                    messages=messages,
                    model=merged.get("model"),
                    temperature=merged["temperature"],
                    max_tokens=merged.get("max_tokens"),
                    output_format=effective_output_format,
                    json_schema=json_schema,
                )
                break
            except Exception as exc:
                if getattr(exc, "status_code", None) != 429:
                    raise
                resp = getattr(exc, "response", None)
                wait = parse_retry_after(
                    getattr(resp, "headers", None) if resp is not None else None
                )
                if wait is None or wait <= 0 or attempt == MAX_429_ATTEMPTS - 1:
                    raise
                logger.warning(
                    "Rate limit on %s (attempt %d/%d); waiting %.1fs",
                    node or "llm_call",
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    wait,
                )
                await wait_with_countdown(wait + 1.0, node or "optimizer")

        duration_s = round(time.monotonic() - _t0, 2)

        emit_token_usage(
            TokenUsage(
                node=node or "llm_call",
                kind="optimizer",
                input_tokens=response.usage.get("prompt_tokens", 0),
                output_tokens=response.usage.get("completion_tokens", 0),
                duration_s=duration_s,
            )
        )

        if cache is not None and cache_key is not None:
            cache.save(cache_key, response.model_dump())

    if recorder is not None:
        response_data: dict | str
        try:
            response_data = json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            response_data = response.content

        action: dict = {
            "type": node or "llm_call",
            "config": {
                "model": merged.get("model"),
                "temperature": merged["temperature"],
                "max_tokens": merged.get("max_tokens"),
            },
            "response": response_data,
            "usage": response.usage,
            "model": response.model,
            "duration_s": duration_s,
        }
        if cached_payload is not None:
            action["cached"] = True
        if trace_meta:
            action.update(trace_meta)
        else:
            action["messages"] = messages
        recorder.add_action(action)

    return response


async def run_optimizer_node(
    *,
    template_name: str,
    compile_vars: dict,
    llm_client: LLMClientBase,
    model: str | None,
    temperature: float = 0.0,
    json_schema: dict | None = None,
    user_content: str | None = None,
    recorder: AuditTrailProjection | None = None,
    template: PromptTemplate | None = None,
    cache: OptimizerCallCache | None = None,
) -> tuple[Any, str]:
    """Load prompt template, compile, call LLM, parse JSON → (parsed_result, prompt_text).

    When *template* is provided, it overrides the load-from-name path (used
    by L1's ``l1_template_override`` channel — L2 can rewrite L1's prompt
    body by writing ``template_override`` on its OSP). The trace metadata
    still records ``template_name`` so observability stays continuous.

    When *cache* is provided, it is forwarded to :func:`llm_call` for
    content-addressed cross-cycle reuse of optimizer LLM responses.
    """
    if template is None:
        template = load_optimizer_prompt(template_name)
    prompt = template.compile_prompt(**compile_vars)
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
        json_schema=json_schema,
        recorder=recorder,
        cache=cache,
        trace_meta={
            "template_name": template_name,
            "template_fields": template.prompt_field_dict(),
            "variables": compile_vars,
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


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch prompt from Langfuse 'production' label (None on any failure)."""
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        from promptpotter.infrastructure.tracing import LangfuseLogger

        lf = LangfuseLogger.get_instance()
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
    """Load optimizer prompt: Langfuse production → manifest registry fallback."""
    lf_prompt = _try_langfuse(name)
    return lf_prompt or _load_local(name)


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push manifest-registry prompt defaults to Langfuse; returns {name: success}."""
    from promptpotter.infrastructure.tracing import LangfuseLogger

    lf = LangfuseLogger.get_instance()
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
    so M10's cross-cycle leaderboard can join cycles by ``l1_generate_hash`` etc.
    """
    out: dict[str, str] = {}
    for name in list_optimizer_prompts():
        tpl = load_optimizer_prompt(name)
        out[name] = hashlib.sha256(tpl.model_dump_json().encode("utf-8")).hexdigest()[:16]
    return out
