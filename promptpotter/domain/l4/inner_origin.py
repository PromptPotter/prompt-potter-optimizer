from __future__ import annotations

from promptpotter.domain.pipeline_params import PipelineParams

# Measurement identity, never a wire tunable: the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def inner_origin_of(pipeline_params: PipelineParams | None) -> str | None:
    """The one reader of ``INNER_ORIGIN_KEY``, on whichever node carries it: the stamped chain head differs per optimizer."""
    for config in pipeline_params.nodes.values() if pipeline_params else ():
        if isinstance(value := config.get(INNER_ORIGIN_KEY), str) and value:
            return value
    return None
