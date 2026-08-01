"""Per-sample chronological measurement series at cycle + campaign scope.

Walks per-cycle ``rounds/round_*.json`` directly so in-flight cycles land before archive.
Errored items dropped to match the Rasch / heatmap observation set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "campaign_measurement_series",
    "cycle_measurement_series",
]


def cycle_measurement_series(
    store: Stores,
    campaign_id: str,
    cycle_id: str,
    sample_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    """Walk one cycle's ``rounds/round_*.json`` → per-sample series.

    Ord = ``{round:04d}/{cand_idx:02d}``; candidates absent from the scoreboard sink to slot 99.
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    rounds_dir = CycleLayout(cycle_dir).rounds
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    if not rounds_dir.is_dir():
        return out
    for round_path in sorted(rounds_dir.glob("round_*.json")):
        doc = read_json_tolerant(round_path)
        if not isinstance(doc, dict):
            continue
        round_no = doc.get("round")
        if not isinstance(round_no, int):
            continue
        idx_of: dict[str, int] = {}
        for i, c in enumerate(doc.get("scoreboard") or []):
            cid = c.get("candidate_id") if isinstance(c, dict) else None
            if isinstance(cid, str):
                idx_of[cid] = i
        acr = doc.get("all_candidate_results") or {}
        if not isinstance(acr, dict):
            continue
        for cand_id, results in acr.items():
            ci = idx_of.get(cand_id, 99)
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                sid = item.get("sample_id")
                fitness = item.get("fitness")
                # An unscored row carries no verdict — skipped, as before. The guard reads
                # ``fitness`` because that is the field a scored row is guaranteed to have.
                if not isinstance(sid, int) or not isinstance(fitness, int | float):
                    continue
                if sid not in sample_ids:
                    continue
                if is_error_result(item):
                    continue
                out[sid].append(
                    {
                        "ord": f"{round_no:04d}/{ci:02d}",
                        "fitness": float(fitness),
                        "label": f"R{round_no} cand {ci}",
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out


def campaign_measurement_series(
    store: Stores,
    campaign_id: str,
    sample_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    """Pool every cycle's round-file series across one campaign; ord prefixed by cycle id."""
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    for entry in store.campaigns.enumerate_cycles():
        if entry["campaign_id"] != campaign_id:
            continue
        cid = entry["cycle_id"]
        per_cycle = cycle_measurement_series(store, campaign_id, cid, sample_ids)
        for sid, dots in per_cycle.items():
            for d in dots:
                out[sid].append(
                    {
                        "ord": f"{cid}/{d['ord']}",
                        "fitness": d["fitness"],
                        "label": d["label"],
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out
