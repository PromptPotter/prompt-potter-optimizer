from __future__ import annotations

__all__ = ["INNER_ORIGIN_KEY", "instrument_of"]

# Reserved per-node config key carrying the inner-origin fingerprint: measurement identity, never a
# wire tunable — the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def instrument_of(pipeline_params: object) -> str | None:
    """The inner origin a searchpoint was measured on, or ``None``: the one reader of
    ``INNER_ORIGIN_KEY``, so the mint-time cohort warning and the evidence roster agree on it."""
    if not isinstance(pipeline_params, dict):
        return None
    node = pipeline_params.get("l1_generate")
    value = node.get(INNER_ORIGIN_KEY) if isinstance(node, dict) else None
    return value if isinstance(value, str) and value else None
