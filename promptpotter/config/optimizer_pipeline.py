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
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign.round_recorder import RoundRecorder

from promptpotter.services.llm_client import LLMClientBase, LLMResponse

logger = logging.getLogger(__name__)

_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """Load optimizer_pipeline.json as PipelineSchema (cached)."""
    from promptpotter.models.pipeline_schema import load_pipeline_from_dict

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

# -- Round recorder (set by CLI/webapp, None = no tracing) ----------------

_recorder: RoundRecorder | None = None


def set_round_recorder(recorder: RoundRecorder | None) -> None:
    """Wire the round recorder for LLM trace capture. None = disable."""
    global _recorder
    _recorder = recorder


def get_round_recorder() -> RoundRecorder | None:
    """Return the active round recorder (for non-LLM actions)."""
    return _recorder


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
        config = get_node_config(node) if node else {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}

    import time as _time
    _t0 = _time.monotonic()

    response = await llm_client.chat(
        messages=messages,
        model=merged.get("model"),
        temperature=merged["temperature"],
        max_tokens=merged["max_tokens"],
        output_format=merged["output_format"],
    )

    # Trace to round recorder if active
    if _recorder is not None:
        duration_s = round(_time.monotonic() - _t0, 2)

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
