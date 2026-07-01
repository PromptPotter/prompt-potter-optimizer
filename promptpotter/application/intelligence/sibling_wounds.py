"""Cross-cycle wound inheritance — surface sibling runtime_failures to a fresh fork."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.escalation_signals import RuntimeFailure
from promptpotter.infrastructure.store.io import read_json_tolerant

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

# Sibling ran to a meaningful boundary — terminal opt_sp carries trustworthy runtime_failures.
_FINISHED_STOP_REASONS = frozenset(
    {"max_rounds", "goal_reached", "infinite_stall", "perfect_score", "l3_patience"}
)


def _rf_dedup_key(rf_dict: dict[str, Any]) -> tuple[str, str, str]:
    """Mirror of ``Cycle._rf_dedup_key`` so cross-cycle dedup matches intra-cycle."""
    cfg = rf_dict.get("observed_config") or {}
    return (
        rf_dict.get("source", ""),
        rf_dict.get("dominant_warning", ""),
        json.dumps(cfg, sort_keys=True, default=str),
    )


def gather_sibling_runtime_failures(
    stores: Stores,
    campaign_id: str,
    root_cycle_id: str,
    backend_id: str,
    exclude_cycle_id: str | None = None,
) -> list[RuntimeFailure]:
    """Aggregate deduped runtime_failures from finished sibling cycles of ``root_cycle_id``."""
    from promptpotter.infrastructure.store import root_cycle_id as _root_of

    out: list[RuntimeFailure] = []
    seen_keys: set[tuple[str, str, str]] = set()

    cycles_dir = stores.campaigns.campaign_root_dir(campaign_id) / "cycles"
    if not cycles_dir.exists():
        return out

    sibling_dirs: list[Any] = [
        d for d in sorted(cycles_dir.iterdir()) if d.is_dir() and _root_of(d.name) == root_cycle_id
    ]

    for sibling in sibling_dirs:
        if exclude_cycle_id and sibling.name == exclude_cycle_id:
            continue
        idx = read_json_tolerant(sibling / "index.json")
        if not isinstance(idx, dict):
            continue
        if idx.get("stop_reason") not in _FINISHED_STOP_REASONS:
            continue
        n_rounds = int(idx.get("n_rounds") or 0)
        if n_rounds <= 0:
            continue
        round_data = read_json_tolerant(sibling / "rounds" / f"round_{n_rounds:04d}.json")
        if not isinstance(round_data, dict):
            continue
        osp = round_data.get("opt_search_point") or {}
        wounds = osp.get("wounds") or {}
        for rf_dict in wounds.get("runtime_failures") or []:
            key = _rf_dedup_key(rf_dict)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            try:
                out.append(RuntimeFailure(**rf_dict))
            except (TypeError, ValueError):
                continue

    logger.debug(
        "gather_sibling_runtime_failures(%s, %s): %d failures from %d siblings",
        root_cycle_id,
        backend_id,
        len(out),
        len(sibling_dirs),
    )
    return out


__all__ = ["gather_sibling_runtime_failures"]
