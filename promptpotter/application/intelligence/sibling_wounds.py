"""Cross-cycle wound inheritance — surface sibling runtime_failures to a fresh fork."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.escalation_signals import RuntimeFailure, rf_dedup_key
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import campaign_cycles_dir

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def _ran_to_completion(raw_stop_reason: Any) -> bool:
    """Sibling reached a natural conclusion, so its terminal opt_sp carries trustworthy
    runtime_failures.

    Classified through the canonical ``StopReason`` table, never a hand-written name allowlist.
    """
    if not isinstance(raw_stop_reason, str):
        return False
    try:
        reason = StopReason(raw_stop_reason)
    except ValueError:
        logger.warning(
            "sibling carries an unknown stop_reason %r — skipped; its wounds are not inherited",
            raw_stop_reason,
        )
        return False
    return stop_reason_outcome(reason) is StopOutcome.SUCCESS


def gather_sibling_runtime_failures(
    stores: Stores,
    campaign_id: str,
    root_cycle_id: str,
    backend_id: str,
    exclude_cycle_id: str | None = None,
) -> list[RuntimeFailure]:
    """Aggregate deduped runtime_failures from finished sibling cycles of ``root_cycle_id``."""
    from promptpotter.infrastructure.store.layout import root_cycle_id as _root_of

    out: list[RuntimeFailure] = []
    seen_keys: set[tuple[str, str, str]] = set()

    cycles_dir = campaign_cycles_dir(stores.campaigns.campaign_root_dir(campaign_id))
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
        if not _ran_to_completion(idx.get("stop_reason")):
            continue
        n_rounds = int(idx.get("n_rounds") or 0)
        if n_rounds <= 0:
            continue
        round_data = read_json_tolerant(sibling / "rounds" / f"round_{n_rounds:04d}.json")
        if not isinstance(round_data, dict):
            continue
        osp = round_data.get("opt_search_point") or {}
        memory = osp.get("memory") or {}
        wounds = memory.get("wounds") or {}
        for rf_dict in wounds.get("runtime_failures") or []:
            key = rf_dedup_key(rf_dict)
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
