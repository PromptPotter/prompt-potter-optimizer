"""Cross-cycle wound inheritance — surface sibling runtime_failures to a fresh fork.

When `optimize --fork-on-divergence` mints a sibling cycle, the new cycle's
`opt_sp.wounds.runtime_failures` starts empty even though sibling forks of
the same root may carry hard-won evidence about which configs degrade
(empty_response, HTTP 413, etc.). This helper walks the sibling fork tree
and aggregates that evidence so the new cycle's L1 sees it from round 1.

Pure read — no I/O writes. The aggregation is *additive*: deduped by
(source, dominant_warning, observed_config) tuple, same key the
intra-cycle dedup in `Cycle.absorb_round` uses. The model-aware filter
in `_r_runtime_failures` still drops entries that don't match the new
cycle's pipeline config — that's intentional, this just makes them
available for the renderer to filter.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.escalation_signals import RuntimeFailure

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

# Stop reasons that mean "this sibling ran to a meaningful boundary" — its
# terminal opt_sp carries trustworthy runtime_failures evidence.
_FINISHED_STOP_REASONS = frozenset(
    {"max_rounds", "goal_reached", "infinite_stall", "perfect_score", "l3_patience"}
)


def _rf_dedup_key(rf_dict: dict[str, Any]) -> tuple[str, str, str]:
    """Mirror of `Cycle._rf_dedup_key` so cross-cycle dedup matches intra-cycle."""
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
    """Aggregate runtime_failures from finished sibling cycles of `root_cycle_id`.

    Walks the campaign's flat `cycles/` dir; for each cycle that shares the
    same family root and has a finished `index.json::stop_reason`, reads the
    last round_NNNN.json's terminal `opt_search_point.wounds.runtime_failures`.
    Returns a deduped list of `RuntimeFailure` objects.

    `exclude_cycle_id` skips one specific sibling (used at fork-mint time
    to avoid inheriting from oneself when the new fork is already on disk).

    Best-effort: malformed JSON or missing files are skipped silently.
    """
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
        idx_path = sibling / "index.json"
        if not idx_path.is_file():
            continue
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if idx.get("stop_reason") not in _FINISHED_STOP_REASONS:
            continue
        n_rounds = int(idx.get("n_rounds") or 0)
        if n_rounds <= 0:
            continue
        last_round_path = sibling / "rounds" / f"round_{n_rounds:04d}.json"
        if not last_round_path.is_file():
            continue
        try:
            round_data = json.loads(last_round_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
