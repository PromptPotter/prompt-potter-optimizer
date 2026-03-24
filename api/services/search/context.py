"""LLM-assisted context restructuring into Layer 1 fields and domain context."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.models.hashing import HASH_TRUNCATE
from api.services.llm_client import LLMClientBase
from api.services.stores.base import read_json_optional, validate_path_component, write_json

logger = logging.getLogger(__name__)


async def restructure_context(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
    improvement_areas: str = "",
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        improvement_areas: Optional domain-expert observations about where
            improvement is most likely.

    Returns:
        Dict of structured Layer 1 field values, a ``task_context`` sub-dict
        with domain fields (domain, pipeline_purpose, data_characteristics,
        optimization_goals, key_challenges), and a ``consultation`` string
        when improvement_areas is provided.
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

    if improvement_areas:
        user_content += (
            "\n\nThe user has identified the following areas where improvement "
            "is most likely:\n"
            f"{improvement_areas}\n\n"
            "Take these observations into account when structuring the fields "
            "and provide strategic advice in the consultation field."
        )

    if improvement_areas:
        consultation_instruction = (
            "Return a JSON object with these keys plus a \"consultation\" key. "
            "The consultation value should be a natural-language paragraph of "
            "strategic advice on how to approach optimization given the user's "
            "identified improvement areas. Use empty string for Layer 1 fields "
            "that don't apply. Be concise and actionable."
        )
    else:
        consultation_instruction = (
            "Return a JSON object with exactly these keys. Use empty string for "
            "fields that don't apply. Be concise and actionable."
        )

    system_prompt = load_optimizer_prompt("restructure").compile_prompt(
        consultation_instruction=consultation_instruction,
    )

    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    for key in ("persona", "task_intent", "problem_description",
                "instruction", "thinking_style", "answer_format"):
        result.setdefault(key, "")

    if improvement_areas:
        result.setdefault("consultation", "")

    # Ensure task_context sub-dict exists with domain fields
    tc = result.setdefault("task_context", {})
    for key in ("domain", "pipeline_purpose", "data_characteristics",
                "optimization_goals", "key_challenges"):
        tc.setdefault(key, "")

    return result


# ---------------------------------------------------------------------------
# Restructure cache — alias-aware disk cache for LLM decomposition results
# ---------------------------------------------------------------------------



def _restructure_cache_path(base_dir: Path, backend_id: str) -> Path:
    validate_path_component(backend_id)
    return base_dir / backend_id / "restructure_cache.json"


def load_cached_restructure(
    base_dir: Path,
    backend_id: str,
    alias_hashes: set[str],
    improvement_areas: str,
) -> dict | None:
    """Scan *alias_hashes* for a cached restructure matching *improvement_areas*."""
    cache = read_json_optional(_restructure_cache_path(base_dir, backend_id))
    if not cache:
        return None
    for h in alias_hashes:
        entry = cache.get(h)
        if entry and entry.get("improvement_areas", "") == improvement_areas:
            return entry["layer1_fields"]
    return None


def save_restructure_cache(
    base_dir: Path,
    backend_id: str,
    rp_hash: str,
    improvement_areas: str,
    layer1_fields: dict,
) -> None:
    """Persist restructure output keyed by *rp_hash*."""
    path = _restructure_cache_path(base_dir, backend_id)
    cache = read_json_optional(path) or {}
    cache[rp_hash] = {
        "improvement_areas": improvement_areas,
        "layer1_fields": layer1_fields,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, cache)


async def restructure_context_cached(
    context_input: Any,
    llm_client: LLMClientBase,
    *,
    model: str | None = None,
    improvement_areas: str = "",
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
        cached = load_cached_restructure(
            store_base_dir, backend_id, alias_hashes, improvement_areas,
        )
        if cached is not None:
            logger.info("restructure_context_cached: hit (alias group)")
            return cached, True

    # --- cache miss: call LLM ---
    layer1_fields = await restructure_context(
        context_input, llm_client,
        model=model,
        improvement_areas=improvement_areas,
    )

    # --- save to cache ---
    if can_cache:
        save_key = rp_hash
        if not save_key:
            instruction = (
                context_input if isinstance(context_input, str)
                else json.dumps(context_input, sort_keys=True)
            )
            save_key = hashlib.sha256(instruction.encode()).hexdigest()[:HASH_TRUNCATE]
        save_restructure_cache(
            store_base_dir, backend_id, save_key, improvement_areas, layer1_fields,
        )

    return layer1_fields, False
