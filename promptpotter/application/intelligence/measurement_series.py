"""Per-sample chronological measurement series for the cycle + campaign scopes.

Sibling to :func:`promptpotter.infrastructure.store.archive_views.measurement_series_for_samples`,
which serves the dataset (cross-campaign) scope. All three feed the
read-only ``/datasets/{name}/measurement-series`` endpoint that powers
the hard-sample-leaderboard heatmap.

The two functions here walk per-cycle ``rounds/round_*.json`` files
directly — series for an in-flight cycle land before the run is archived,
and a campaign-pooled view needs the same in-flight visibility across
every cycle in the campaign. Errored items (predicate
:func:`promptpotter.shared.errors.is_error_result`) are dropped so the
Rasch fit and the heatmap see the same observation set.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store import cycle_dir_for
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
    """Walk one cycle's ``rounds/round_*.json``, returning per-sample series.

    Ord = ``{round:04d}/{cand_idx:02d}`` so series sort chronologically by
    round then by scoreboard position within the round. Candidates not
    appearing in the round's scoreboard sink to slot 99.

    Samples in *sample_ids* with no measurements come back as empty lists;
    samples outside *sample_ids* are skipped. Missing or unreadable round
    files are silently skipped — a cycle still mid-round may have an
    open file.
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    rounds_dir = cycle_dir / "rounds"
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    if not rounds_dir.is_dir():
        return out
    for round_path in sorted(rounds_dir.glob("round_*.json")):
        try:
            doc = json.loads(round_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
                hit = item.get("hit")
                if not isinstance(sid, int) or not isinstance(hit, bool):
                    continue
                if sid not in sample_ids:
                    continue
                if is_error_result(item):
                    continue
                out[sid].append(
                    {
                        "ord": f"{round_no:04d}/{ci:02d}",
                        "hit": hit,
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
    """Pool every cycle's round-file series across one campaign.

    Ord is prefixed with the cycle id so series from different cycles in
    the campaign stay distinct under lexicographic sort.
    """
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
                        "hit": d["hit"],
                        "label": d["label"],
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out
