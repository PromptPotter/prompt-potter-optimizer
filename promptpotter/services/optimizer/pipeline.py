"""Optimizer pipeline — schema loader, LLM call primitive, and prompt preparation.

``get_optimizer_schema()`` loads the optimizer pipeline declaration
(``optimizer_pipeline.json``) as a ``PipelineSchema`` — the same model
used for target pipelines.

``llm_call()`` is a thin config-driven wrapper over ``LLMClientBase.chat()``
that reads defaults from a node's config dict and allows runtime overrides.
Every optimizer pipeline node uses this instead of calling ``chat()`` directly.

Prompt preparation: loading optimizer meta-prompt templates (Langfuse or
local JSON), decomposing monolithic prompts into 8 canonical fields via LLM,
and decomposing task descriptions into structured TaskDecomposition.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign.persistence_emitter import RoundRecorder

from promptpotter.models.opt_search_point import PromptTemplate
from promptpotter.models.search_point import TaskDecomposition
from promptpotter.services.llm_client import LLMClientBase, LLMResponse
from promptpotter.services.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.hashing import HASH_TRUNCATE
from promptpotter.shared.llm_parsing import extract_parsed_json

logger = logging.getLogger(__name__)

_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """Load optimizer_pipeline.json as PipelineSchema (cached)."""
    from promptpotter.models.pipeline_schema import PipelineNode

    data = json.loads(_PIPELINE_PATH.read_text())
    nodes = [
        PipelineNode(
            name=name,
            current_config=node_data.get("config", {}),
            param_keys=set(node_data.get("optimizer", {}).get("param_keys", [])),
        )
        for name, node_data in data.get("nodes", {}).items()
    ]
    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )


_LLM_DEFAULTS = {"temperature": 0.0, "max_tokens": 1000, "output_format": "text"}

# -- Round recorder (per-task context, not a module global) ----------------

_recorder_var: contextvars.ContextVar[RoundRecorder | None] = contextvars.ContextVar(
    "round_recorder",
    default=None,
)


def set_round_recorder(recorder: RoundRecorder | None) -> None:
    """Wire the round recorder for LLM trace capture. None = disable."""
    _recorder_var.set(recorder)


def get_round_recorder() -> RoundRecorder | None:
    """Return the active round recorder (for non-LLM actions)."""
    return _recorder_var.get()


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict | None = None,
    trace_meta: dict | None = None,
    **overrides,
) -> LLMResponse:
    """Execute an LLM call with config defaults and runtime overrides.

    Provide ``node`` to auto-load config from ``optimizer_pipeline.json``,
    or ``config`` to pass a config dict directly.  At least one is required.

    Precedence: ``_LLM_DEFAULTS < config < overrides``.

    If a :class:`RoundRecorder` is active (via :func:`set_round_recorder`),
    the call is traced: messages, config, response, and optional
    ``trace_meta`` (template_name, variables) are recorded as an action.
    """
    if config is None:
        if node:
            schema_node = get_optimizer_schema().get_node(node)
            if schema_node is None:
                raise KeyError(f"Unknown optimizer node: {node}")
            config = schema_node.current_config
        else:
            config = {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}

    _t0 = time.monotonic()

    response = await llm_client.chat(
        messages=messages,
        model=merged.get("model"),
        temperature=merged["temperature"],
        max_tokens=merged["max_tokens"],
        output_format=merged["output_format"],
    )

    # Trace to round recorder if active
    _recorder = _recorder_var.get()
    if _recorder is not None:
        duration_s = round(time.monotonic() - _t0, 2)

        # Parse JSON responses into structured objects (not escaped strings)
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
                "max_tokens": merged["max_tokens"],
            },
            "response": response_data,
            "usage": response.usage,
            "model": response.model,
            "duration_s": duration_s,
        }
        if trace_meta:
            # template_name, template_fields, variables from call site
            action.update(trace_meta)
        else:
            # Fallback: store compiled messages when no decomposition available
            action["messages"] = messages
        _recorder.add_action(action)

    return response


# ---------------------------------------------------------------------------
# Prompt template loading (from Langfuse or local JSON)
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).parent / "prompts"


@functools.lru_cache(maxsize=32)
def _load_local(name: str) -> PromptTemplate:
    path = _PROMPT_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**data)


_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch from Langfuse prompt registry by *production* label.

    Returns ``None`` on any failure (credentials missing, network error,
    prompt not found).  Langfuse SDK caches internally for
    ``_LANGFUSE_CACHE_TTL`` seconds.
    """
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        from promptpotter.services.tracing.langfuse_client import LangfuseLogger

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
    """Load an optimizer prompt template as a PromptTemplate.

    Resolution order:
    1. Langfuse prompt registry (by ``production`` label, SDK-cached)
    2. Local JSON default in ``promptpotter/services/optimizer/prompts/{name}.json``
    """
    lf_prompt = _try_langfuse(name)
    return lf_prompt or _load_local(name)


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push all local JSON defaults to the Langfuse prompt registry.

    For each JSON file, creates a new prompt version with:
    - ``prompt`` = assembled template text (``render()``) for Langfuse UI display
    - ``config`` = full PromptTemplate dict (for reconstruction on fetch)
    - ``labels`` = ``[label]``
    - ``tags`` = ``["optimizer", "meta-prompt"]``

    Returns ``{name: success_bool}`` mapping.
    """
    from promptpotter.services.tracing.langfuse_client import LangfuseLogger

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
                commit_message=f"Push local default for {name}",
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
    """List available optimizer prompt names from local JSON files."""
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Prompt field decomposition (monolithic prompt → 8 canonical fields)
# ---------------------------------------------------------------------------


async def decompose_prompt_fields(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).

    Returns:
        Dict of structured Layer 1 field values and a ``task_context`` sub-dict
        with domain fields (domain, pipeline_purpose, data_characteristics,
        optimization_goals, key_challenges).
    """
    if isinstance(context_input, dict):
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    consultation_instruction = (
        "Return a JSON object with exactly these keys. Use empty string for "
        "fields that don't apply. Be concise and actionable."
    )

    system_prompt = load_optimizer_prompt("restructure").compile_prompt(
        consultation_instruction=consultation_instruction,
    )

    response = await llm_call(
        llm_client,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        node="restructure",
        model=model,
    )
    result = extract_parsed_json(response)

    for key in (
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
    ):
        result.setdefault(key, "")

    # Ensure task_context sub-dict exists with domain fields
    tc = result.setdefault("task_context", {})
    for key in (
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
    ):
        tc.setdefault(key, "")

    return result


def _decomposition_cache_path(base_dir: Path, backend_id: str) -> Path:
    validate_path_component(backend_id)
    return base_dir / backend_id / "restructure_cache.json"


def load_cached_decomposition(
    base_dir: Path,
    backend_id: str,
    alias_hashes: set[str],
) -> dict | None:
    """Scan *alias_hashes* for a cached restructure result."""
    cache = read_json_optional(_decomposition_cache_path(base_dir, backend_id))
    if not cache:
        return None
    for h in alias_hashes:
        entry = cache.get(h)
        if entry:
            return entry["layer1_fields"]
    return None


def save_decomposition_cache(
    base_dir: Path,
    backend_id: str,
    rp_hash: str,
    layer1_fields: dict,
) -> None:
    """Persist restructure output keyed by *rp_hash*."""
    path = _decomposition_cache_path(base_dir, backend_id)
    cache = read_json_optional(path) or {}
    cache[rp_hash] = {
        "layer1_fields": layer1_fields,
        "cached_at": datetime.now(UTC).isoformat(),
    }
    write_json(path, cache)


async def decompose_prompt_fields_cached(
    context_input: Any,
    llm_client: LLMClientBase,
    *,
    model: str | None = None,
    store_base_dir: Path | None = None,
    backend_id: str = "",
    alias_hashes: set[str] | None = None,
    rp_hash: str = "",
    force: bool = False,
) -> tuple[dict, bool]:
    """LLM restructure with alias-aware disk caching.

    Checks *alias_hashes* against the restructure cache before calling the LLM.
    On miss, saves under *rp_hash* (caller-provided) so the key is guaranteed
    to be in the alias set on subsequent lookups.

    Returns:
        ``(layer1_fields, was_cached)`` tuple.
    """
    can_cache = bool(store_base_dir and backend_id)

    # --- cache lookup ---
    if can_cache and not force and alias_hashes:
        assert store_base_dir is not None
        cached = load_cached_decomposition(
            store_base_dir,
            backend_id,
            alias_hashes,
        )
        if cached is not None:
            logger.debug("decompose_prompt_fields_cached: hit (alias group)")
            return cached, True

    # --- cache miss: call LLM ---
    layer1_fields = await decompose_prompt_fields(
        context_input,
        llm_client,
        model=model,
    )

    # --- save to cache ---
    if can_cache:
        assert store_base_dir is not None
        save_key = rp_hash
        if not save_key:
            instruction = (
                context_input
                if isinstance(context_input, str)
                else json.dumps(context_input, sort_keys=True)
            )
            save_key = hashlib.sha256(instruction.encode()).hexdigest()[:HASH_TRUNCATE]
        save_decomposition_cache(
            store_base_dir,
            backend_id,
            save_key,
            layer1_fields,
        )

    return layer1_fields, False


# ---------------------------------------------------------------------------
# Task description decomposition (raw text → TaskDecomposition fields)
# ---------------------------------------------------------------------------


@dataclass
class TaskContextResult:
    """Result from ``decompose_task_context()``."""

    task_context: TaskDecomposition
    consultation: str | None
    was_cached: bool


async def decompose_task_context(
    task_description: str,
    llm_client: LLMClientBase,
    model: str,
    store_base_dir: Path | None = None,
    backend_id: str = "",
) -> TaskContextResult:
    """Decompose a task description into structured domain context fields via LLM.

    Calls ``decompose_prompt_fields_cached()`` and extracts the ``task_context``
    sub-dict.

    Returns:
        TaskContextResult with task_context dict, optional consultation text,
        and cache-hit flag.
    """
    if not task_description:
        return TaskContextResult(
            task_context=TaskDecomposition(), consultation=None, was_cached=False
        )

    # Content-hash for caching
    rp_hash = hashlib.sha256(
        f"task_ctx:{task_description}".encode(),
    ).hexdigest()[:16]

    result, was_cached = await decompose_prompt_fields_cached(
        task_description,
        llm_client,
        model=model,
        store_base_dir=store_base_dir,
        backend_id=backend_id,
        rp_hash=rp_hash,
    )

    tc_dict = result.get("task_context", {})
    tc_dict["raw_description"] = task_description
    task_context = TaskDecomposition.from_dict(tc_dict)

    return TaskContextResult(
        task_context=task_context,
        consultation=result.get("consultation"),
        was_cached=was_cached,
    )
