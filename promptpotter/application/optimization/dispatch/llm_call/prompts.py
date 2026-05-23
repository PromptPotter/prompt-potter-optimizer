"""Optimizer-pipeline manifest loading — schemas + meta-prompt templates.

Single source of truth for optimizer nodes, schemas, and prompts is
``datasets/_optimizer/pipeline.json``. It mirrors TermNorm's
``GET /pipeline`` response shape: ``nodes`` reference
``schema_family``/``schema_version`` + ``prompt_family``/``prompt_version``,
and the bodies live in the top-level ``resolved_schemas`` /
``resolved_prompts`` registries. Prompt loading prefers a Langfuse
``production`` label and falls back to the local manifest registry.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)

__all__ = [
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "load_optimizer_prompt",
    "push_all_to_langfuse",
]

_REPO_ROOT = Path(__file__).resolve().parents[5]
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
    manifest: dict[str, Any] = json.loads(_PIPELINE_PATH.read_text(encoding="utf-8"))
    return manifest


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


_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


def _resolved_prompt_for_node(name: str) -> dict[str, Any] | None:
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


def combined_optimizer_prompt_hash() -> str:
    """One 12-hex hash over the whole optimizer meta-prompt set.

    Folds the per-prompt hashes from :func:`compute_optimizer_prompt_hashes`
    into a single deterministic digest. Recorded on ``campaign.json`` as
    ``optimizer_prompt_hash``; resume compares the stored value against a
    fresh recomputation and warns on drift. Not part of ``campaign_id``
    (campaign ids are random per ``new`` call) — it's a drift-detection
    property only.
    """
    per_prompt = compute_optimizer_prompt_hashes()
    blob = json.dumps(per_prompt, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
