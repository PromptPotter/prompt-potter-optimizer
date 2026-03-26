"""Optimizer pipeline schema loader and LLM call primitive.

``get_optimizer_schema()`` loads the optimizer pipeline declaration
(``optimizer_pipeline.json``) as a ``PipelineSchema`` — the same model
used for target pipelines.

``llm_call()`` is a thin config-driven wrapper over ``LLMClientBase.chat()``
that reads defaults from a node's config dict and allows runtime overrides.
Every optimizer pipeline node uses this instead of calling ``chat()`` directly.
"""

import functools
import json
import logging
from pathlib import Path

from api.services.llm_client import LLMClientBase, LLMResponse

logger = logging.getLogger(__name__)

_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


@functools.lru_cache(maxsize=1)
def get_optimizer_schema():
    """Load optimizer_pipeline.json as PipelineSchema (cached)."""
    from api.models.pipeline_schema import load_pipeline_from_dict

    data = json.loads(_PIPELINE_PATH.read_text())
    return load_pipeline_from_dict(data)


def get_node_config(node_name: str) -> dict:
    """Get a node's config dict from the optimizer pipeline.

    Returns ``PipelineNode.current_config`` for the named node.
    """
    node = get_optimizer_schema().get_node(node_name)
    if node is None:
        raise KeyError(f"Unknown optimizer node: {node_name}")
    return node.current_config


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
