"""LLM-assisted decomposition into Layer 1 prompt fields, domain context,
and eval dataset loading.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.optimizer_pipeline import llm_call
from promptpotter.config.optimizer_prompt_loader import load_optimizer_prompt
from promptpotter.models.task_context import TaskContext
from promptpotter.services.llm_client import LLMClientBase
from promptpotter.services.project_store import ProjectStore
from promptpotter.services.stores.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.hashing import HASH_TRUNCATE
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)


@dataclass
class TaskContextResult:
    """Result from ``decompose_task_context()``."""

    task_context: TaskContext
    consultation: str | None
    was_cached: bool


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
        return TaskContextResult(task_context=TaskContext(), consultation=None, was_cached=False)

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
    task_context = TaskContext.from_dict(tc_dict)

    return TaskContextResult(
        task_context=task_context,
        consultation=result.get("consultation"),
        was_cached=was_cached,
    )


def _generic_resolve_gt(exp_data: dict) -> dict[str, str]:
    """Build ``{query: ground_truth}`` from evaluation_results (generic fallback)."""
    gt_map: dict[str, str] = {}
    runs = exp_data.get("runs", [])
    if not runs:
        return gt_map
    for er in runs[0].get("evaluation_results", []):
        q = er.get("query", "")
        gt = er.get("ground_truth", "")
        if q and gt:
            gt_map[q] = gt
    return gt_map


def _extract_eval_from_traces(
    exp_data: dict,
    schema: PipelineSchema,
    backend_name: str | None = None,
) -> list:
    """Build eval data from Langfuse-style traces in synced experiment data.

    Each trace in ``runs[0].traces[]`` carries named observations with
    pipeline step outputs.  Ground truth is resolved via a registered
    connector resolver (or generic query-string matching as fallback).

    Observation extraction is driven by the schema's ``obs_extraction_map()``.

    Returns:
        List of eval-data dicts (may be empty).
    """
    from promptpotter.config.connectors import TRACE_GT_RESOLVERS, ensure_connectors_loaded

    ensure_connectors_loaded()
    resolver = TRACE_GT_RESOLVERS.get((backend_name or "").lower())

    # Pre-build generic fallback map if no connector resolver
    generic_gt = _generic_resolve_gt(exp_data) if not resolver else {}

    runs = exp_data.get("runs", [])
    if not runs:
        return []

    traces = runs[0].get("traces", [])
    if not traces:
        return []

    dataset = []
    for trace in traces:
        query = (trace.get("input") or {}).get("query", "")
        if not query:
            continue

        ground_truth = resolver(exp_data, query) if resolver else generic_gt.get(query)
        if not ground_truth:
            continue

        # Build pipeline_data from observations using declarative mapping
        pipeline_data: dict = {}
        llm_provider: str | None = None

        _obs_map = schema.obs_extraction_map()

        for obs in trace.get("observations", []):
            name = obs.get("name", "")
            output = obs.get("output")
            if not name or not output:
                continue

            for field in _obs_map.get(name, []):
                if field.output_field is None:
                    pipeline_data[field.pipeline_key] = output
                else:
                    val = output.get(field.output_field)
                    if val is not None:
                        pipeline_data[field.pipeline_key] = val

                if field.is_llm and not llm_provider:
                    llm_provider = (obs.get("metadata") or {}).get("model")

        # Gate: required pipeline key must be present
        if not pipeline_data.get(schema.required_pipeline_key()):
            continue

        if llm_provider:
            pipeline_data["llm_provider"] = llm_provider

        # Total time from trace scores (latency_ms → seconds)
        for score in trace.get("scores", []):
            if score.get("name") == "latency_ms" and score.get("value"):
                pipeline_data["total_time"] = score["value"] / 1000.0
                break

        dataset.append(
            {
                "query": query,
                "ground_truth": ground_truth,
                "pipeline_data": pipeline_data,
                "status": "success",
            }
        )

    return dataset


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    sample_size: int = 0,
    schema: PipelineSchema | None = None,
) -> list:
    """Load per-query evaluation data from synced experiments or stored replays.

    Priority:
        1. Langfuse-style traces from ``runs[0].traces[]``
        2. Stored replay executions
        3. Empty list if neither found
    """
    exp_data = store.backends.load_sync(
        backend_id,
        f"experiments/{experiment_id}.json",
    )

    if exp_data:
        if schema is None:
            from promptpotter.models.pipeline_schema import PipelineSchema

            schema = PipelineSchema()
        backend = store.backends.get(backend_id)
        backend_name = backend.name if backend else None
        dataset = _extract_eval_from_traces(exp_data, schema=schema, backend_name=backend_name)
        if dataset:
            if sample_size > 0 and len(dataset) > sample_size:
                rng = random.Random(42)
                dataset = rng.sample(dataset, sample_size)
            return dataset

    executions = store.backends.list_executions(backend_id)
    for ex_summary in executions:
        if ex_summary.get("experiment_id") == experiment_id:
            execution = store.backends.load_execution(
                backend_id,
                ex_summary["execution_id"],
            )
            if execution and schema is not None:
                req_key = schema.required_pipeline_key()
                dataset = [
                    r.model_dump()
                    for r in execution.results
                    if r.status == "success" and r.pipeline_data and r.pipeline_data.get(req_key)
                ]
                if dataset:
                    if sample_size > 0 and len(dataset) > sample_size:
                        rng = random.Random(42)
                        dataset = rng.sample(dataset, sample_size)
                    return dataset

    return []
