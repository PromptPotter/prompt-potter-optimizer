"""
Shared LLM call primitive — the node standard's execution layer.

``llm_call()`` is a thin wrapper over ``LLMClientBase.chat()`` that reads
defaults from a config dict (typically from a ``PipelineNode.current_config``)
and allows runtime overrides.  Every optimizer pipeline node uses this
instead of calling ``chat()`` directly.

``get_optimizer_schema()`` loads the optimizer pipeline declaration as a
``PipelineSchema`` — the same model used for target pipelines. This unifies
the twin: both TermNorm and optimizer pipelines parse into PipelineSchema.
"""

import json
import logging
from pathlib import Path

from api.services.llm_client import LLMClientBase, LLMResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline config loading (cached)
# ---------------------------------------------------------------------------

_OPTIMIZER_SCHEMA = None


def get_optimizer_schema():
    """Load optimizer_pipeline.json as PipelineSchema (cached)."""
    global _OPTIMIZER_SCHEMA  # noqa: PLW0603
    if _OPTIMIZER_SCHEMA is None:
        from api.models.pipeline_schema import load_pipeline_from_dict
        path = Path(__file__).resolve().parents[1] / "config" / "optimizer_pipeline.json"
        data = json.loads(path.read_text())
        _OPTIMIZER_SCHEMA = load_pipeline_from_dict(data)
    return _OPTIMIZER_SCHEMA


def get_node_config(node_name: str) -> dict:
    """Get a node's config dict from the optimizer pipeline.

    Returns ``PipelineNode.current_config`` for the named node.
    """
    node = get_optimizer_schema().get_step(node_name)
    if node is None:
        raise KeyError(f"Unknown optimizer node: {node_name}")
    return node.current_config


# Backward compat alias
def get_optimizer_pipeline() -> dict:
    """Load optimizer_pipeline.json as raw dict (legacy — prefer get_optimizer_schema)."""
    path = Path(__file__).resolve().parents[1] / "config" / "optimizer_pipeline.json"
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# LLM call primitive
# ---------------------------------------------------------------------------


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    config: dict,
    **overrides,
) -> LLMResponse:
    """Execute an LLM call with config defaults and runtime overrides.

    Args:
        llm_client: The LLM client instance.
        messages: Chat messages (role/content dicts).
        config: Node config dict (from ``PipelineNode.current_config``).
            Provides defaults for model, temperature, max_tokens, output_format.
        **overrides: Runtime overrides — any key present here wins over config.
            Common: ``model``, ``temperature``.

    Returns:
        LLMResponse from the underlying chat() call.
    """
    return await llm_client.chat(
        messages=messages,
        model=overrides.get("model", config.get("model")),
        temperature=overrides.get("temperature", config.get("temperature", 0.0)),
        max_tokens=overrides.get("max_tokens", config.get("max_tokens", 1000)),
        output_format=overrides.get("output_format", config.get("output_format", "text")),
    )
