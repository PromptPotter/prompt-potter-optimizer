"""One-time restructure pass: raw context → 8-field prompt + TaskDecomposition.

Disk-cached at ``{base_dir}/{backend_id}/restructure_cache.json`` keyed by
content hash; idempotent ``init`` against an unchanged task_description.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.application.optimization.dispatch.llm_call import run_optimizer_node
from promptpotter.application.optimization.dispatch.schemas import RestructureOutput
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm import LLMClientBase
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)

__all__ = [
    "decompose_prompt_fields",
    "decompose_prompt_fields_cached",
    "decompose_task_context",
    "load_cached_decomposition",
    "save_decomposition_cache",
]


async def decompose_prompt_fields(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """LLM-restructure raw context → Layer 1 prompt fields + task_context sub-dict."""
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

    result, _ = await run_optimizer_node(
        template_name="restructure",
        prompt_vars={"consultation_instruction": consultation_instruction},
        llm_client=llm_client,
        model=model,
        user_content=user_content,
    )
    assert isinstance(result, RestructureOutput), (
        f"restructure must return RestructureOutput, got {type(result).__name__}"
    )
    # Pydantic guarantees every Layer-1 + task_context field is present
    # (defaults to empty string on the model). Materialize to dict for
    # cache persistence + downstream consumers that pre-date the typed
    # boundary.
    return result.model_dump()


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
    """Disk-cached decompose_prompt_fields; returns (layer1_fields, was_cached)."""
    can_cache = bool(store_base_dir and backend_id)

    if can_cache and not force and alias_hashes:
        assert store_base_dir is not None
        cached = load_cached_decomposition(store_base_dir, backend_id, alias_hashes)
        if cached is not None:
            logger.debug("decompose_prompt_fields_cached: hit (alias group)")
            return cached, True

    layer1_fields = await decompose_prompt_fields(context_input, llm_client, model=model)

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
        save_decomposition_cache(store_base_dir, backend_id, save_key, layer1_fields)

    return layer1_fields, False


async def decompose_task_context(
    task_description: str,
    llm_client: LLMClientBase,
    model: str,
    store_base_dir: Path | None = None,
    backend_id: str = "",
) -> tuple[TaskDecomposition, str | None, bool]:
    """Decompose task description → ``(task_context, consultation, was_cached)`` (disk-cached)."""
    if not task_description:
        return TaskDecomposition(), None, False

    rp_hash = hashlib.sha256(f"task_ctx:{task_description}".encode()).hexdigest()[:16]

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
    return TaskDecomposition.from_dict(tc_dict), result.get("consultation"), was_cached
