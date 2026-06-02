"""Pure ``index.json`` shape helpers — round summary + fresh-sibling blob."""

from __future__ import annotations

from typing import Any


def round_summary(round_data: dict[str, Any]) -> dict[str, Any]:
    """Projection of round detail into the ``index.json::rounds`` shape."""
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
    """Clean-slate sibling index inheriting type + identity ``header`` from the parent.

    ``sibling_kind ∈ {fork, diag, sweep}``. ``backend_id`` / ``dataset_name`` ride the
    inherited ``header`` block (the single identity home built by
    ``_build_index_header``) — no top-level copy.
    """
    return {
        "type": parent_index.get("type", "optimization_loop"),
        "connector_type": parent_index.get("connector_type", ""),
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
