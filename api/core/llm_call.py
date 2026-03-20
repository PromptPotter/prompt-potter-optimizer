"""
Shared LLM call primitive — the building block standard's execution layer.

``llm_call()`` is a thin wrapper over ``LLMClientBase.chat()`` that reads
defaults from a config dict (typically from ``optimizer_pipeline.json``)
and allows runtime overrides.  Every optimizer pipeline step uses this
instead of calling ``chat()`` directly.

``get_node_config(node_name)`` loads node configs from the optimizer
pipeline declaration (``api/config/optimizer_pipeline.json``).
"""

import json
import logging
from pathlib import Path

from api.services.llm_client import LLMClientBase, LLMResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline config loading (cached)
# ---------------------------------------------------------------------------

_PIPELINE: dict | None = None


def get_optimizer_pipeline() -> dict:
    """Load optimizer_pipeline.json (cached after first call)."""
    global _PIPELINE  # noqa: PLW0603
    if _PIPELINE is None:
        path = Path(__file__).resolve().parents[1] / "config" / "optimizer_pipeline.json"
        _PIPELINE = json.loads(path.read_text())
    return _PIPELINE


def get_node_config(node_name: str) -> dict:
    """Get a node's config dict from the optimizer pipeline."""
    return get_optimizer_pipeline()["nodes"][node_name]["config"]


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
        config: Node config dict from ``optimizer_pipeline.json``.
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
