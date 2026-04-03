"""Optimizer pipeline schema loader and LLM call primitive.

``get_optimizer_schema()`` loads the optimizer pipeline declaration
(``optimizer_pipeline.json``) as a ``PipelineSchema`` — the same model
used for target pipelines.

``llm_call()`` is a thin config-driven wrapper over ``LLMClientBase.chat()``
that reads defaults from a node's config dict and allows runtime overrides.
Every optimizer pipeline node uses this instead of calling ``chat()`` directly.
"""

from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema

from api.services.llm_client import LLMClientBase, LLMResponse

logger = logging.getLogger(__name__)

_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
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


_LLM_DEFAULTS = {"temperature": 0.0, "max_tokens": 1000, "output_format": "text"}


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict | None = None,
    **overrides,
) -> LLMResponse:
    """Execute an LLM call with config defaults and runtime overrides.

    Provide ``node`` to auto-load config from ``optimizer_pipeline.json``,
    or ``config`` to pass a config dict directly.  At least one is required.

    Precedence: ``_LLM_DEFAULTS < config < overrides``.
    """
    if config is None:
        config = get_node_config(node) if node else {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}
    return await llm_client.chat(
        messages=messages,
        model=merged.get("model"),
        temperature=merged["temperature"],
        max_tokens=merged["max_tokens"],
        output_format=merged["output_format"],
    )
