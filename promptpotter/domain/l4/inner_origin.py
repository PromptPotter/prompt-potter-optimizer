from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_params import PipelineParams

# Measurement identity, never a wire tunable: the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def inner_origin_of(pipeline_params: PipelineParams | None) -> str | None:
    """The one reader of ``INNER_ORIGIN_KEY``, on whichever node carries it: the stamped chain head differs per optimizer."""
    for config in () if pipeline_params is None else pipeline_params.nodes.values():
        if isinstance(value := config.value(INNER_ORIGIN_KEY), str) and value:
            return value
    return None
