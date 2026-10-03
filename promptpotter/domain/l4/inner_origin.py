from __future__ import annotations

from promptpotter.domain.pipeline_overlay import node_config_items

__all__ = ["INNER_ORIGIN_KEY", "instrument_of"]

# Reserved per-node config key carrying the inner-origin fingerprint: measurement identity, never a
# wire tunable — the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def instrument_of(pipeline_params: object) -> str | None:
    """The inner origin a searchpoint was measured on, or ``None``: the one reader of
    ``INNER_ORIGIN_KEY``, so the mint-time cohort warning and the evidence roster agree on it.
    Found on whichever node carries it — the connector stamps the inner optimizer's chain head,
    and that node is a different one per optimizer."""
    if not isinstance(pipeline_params, dict):
        return None
    for _node, config in node_config_items(pipeline_params):
        if isinstance(value := config.get(INNER_ORIGIN_KEY), str) and value:
            return value
    return None
