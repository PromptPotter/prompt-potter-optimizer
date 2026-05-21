"""Pure ``index.json`` shape helpers — round summary + fresh-sibling blob.

Used by :class:`CampaignStore` when projecting a round_data detail into
the index's ``rounds[]`` shape and when minting a clean-slate sibling
cycle that inherits parent metadata.

The cycle ``index.json`` carries no ``config`` snapshot — the frozen
``CampaignConfig`` lives once in the campaign manifest (``campaign.json``).
"""

from __future__ import annotations

from typing import Any


def round_summary(round_data: dict[str, Any]) -> dict[str, Any]:
    """Projection of a round_data detail into the ``index.json::rounds`` shape."""
    round_num = round_data.get("round", 0)
    return {
        "round_id": round_data.get("round_id", f"round_{round_num}"),
        "round": round_num,
        "label": round_data.get("label", ""),
        "prompt_fields_id": round_data.get("prompt_fields_id", ""),
        "accuracy": round_data.get("accuracy", 0.0),
        "hits": round_data.get("hits", 0),
        "total": round_data.get("total", 0),
        "improved": round_data.get("improved", False),
        "created_at": round_data.get("created_at", ""),
    }


def fresh_sibling_index_blob(
    parent_index: dict[str, Any],
    parent_cycle_id: str,
    sibling_kind: str,
    forked_at: str,
    **extras: Any,
) -> dict[str, Any]:
    """Clean-slate sibling index inheriting type/backend from the parent.

    ``sibling_kind`` is one of ``fork`` / ``diag`` / ``sweep`` — recorded
    in ``index.json`` so directory layout stays flat. No ``campaign_id``
    field (the campaign owns identity); no ``config`` (the manifest owns
    the snapshot).
    """
    return {
        "type": parent_index.get("type", "optimization_loop"),
        "connector_type": parent_index.get("connector_type", ""),
        "backend_id": parent_index.get("backend_id", ""),
        "header": parent_index.get("header", {}),
        "parent_cycle_id": parent_cycle_id,
        "parent_session_id": parent_index.get("parent_session_id", ""),
        "sibling_kind": sibling_kind,
        "forked_from_round": 0,
        "forked_at": forked_at,
        "rounds": [],
        "n_rounds": 0,
        "best_accuracy": 0.0,
        "best_round_id": None,
        "origin_accuracy": parent_index.get("origin_accuracy", 0.0),
        "status": "active",
        "created_at": forked_at,
        "updated_at": forked_at,
        **extras,
    }


__all__ = ["fresh_sibling_index_blob", "round_summary"]
