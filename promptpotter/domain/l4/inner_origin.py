from __future__ import annotations

from promptpotter.domain.pipeline_overlay import node_config_items

__all__ = ["INNER_ORIGIN_KEY", "inner_origin_of"]

# Measurement identity, never a wire tunable: the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def inner_origin_of(pipeline_params: object) -> str | None:
    """The one reader of ``INNER_ORIGIN_KEY``, on whichever node carries it: the stamped chain head differs per optimizer."""
    if not isinstance(pipeline_params, dict):
        return None
    for _node, config in node_config_items(pipeline_params):
        if isinstance(value := config.get(INNER_ORIGIN_KEY), str) and value:
            return value
    return None
