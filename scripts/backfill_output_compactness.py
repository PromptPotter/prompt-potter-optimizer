"""One-off backfill: inject the new ``output_compactness`` evaluator into a
campaign's ALREADY-RECORDED scores, re-derived from the ``step_tokens`` that are
already on disk (purely additive — nothing is deleted or re-measured).

Existing campaigns predate the evaluator, so its What-If card shows greyed
(``applicable: false``) until its value is present in the stored ``evaluators``.
This writes it into each cycle's round files (``candidate_scores[].evaluators`` +
the winner's top-level ``evaluators``) and ``dashboard.json``
(``rounds[].candidates[].evaluators``) so the What-If grid + the lineage mask
pick it up. Idempotent: re-running just overwrites the same derived value.

Usage:
    python scripts/backfill_output_compactness.py <campaign_dir>
e.g.
    python scripts/backfill_output_compactness.py \
      .promptpotter/projects/197ee2cf2aea7b14/campaigns/aime_2025__fa2da1af6342
"""

from __future__ import annotations

import glob
import json
import os
import sys

from promptpotter.application.scoring.evaluators import compute_output_compactness


def _oc(rows: list[dict]) -> float:
    return round(compute_output_compactness(results=rows), 6)


def backfill(campaign_dir: str) -> tuple[int, int, int]:
    cycles = glob.glob(os.path.join(campaign_dir, "cycles", "*"))
    if not cycles:
        return (0, 0, 0)
    rf_written = dash_written = 0
    for cyc in cycles:
        per_round: dict[tuple[int, str], float] = {}
        for rfp in glob.glob(os.path.join(cyc, "rounds", "round_*.json")):
            with open(rfp, encoding="utf-8") as fh:
                d = json.load(fh)
            acr = d.get("all_candidate_results") or {}
            if not acr:
                continue
            oc = {cid: _oc(rows) for cid, rows in acr.items()}
            rnd = d.get("round")
            for cid, v in oc.items():
                per_round[(rnd, cid)] = v
            changed = False
            for cs in d.get("candidate_scores") or []:
                cid = cs.get("candidate_id")
                if cid in oc and isinstance(cs.get("evaluators"), dict):
                    cs["evaluators"]["output_compactness"] = oc[cid]
                    changed = True
            winners = [
                c.get("candidate_id") for c in (d.get("scoreboard") or []) if c.get("is_winner")
            ]
            if winners and winners[0] in oc and isinstance(d.get("evaluators"), dict):
                d["evaluators"]["output_compactness"] = oc[winners[0]]
                changed = True
            if changed:
                with open(rfp, "w", encoding="utf-8") as fh:
                    json.dump(d, fh)
                rf_written += 1
        dp = os.path.join(cyc, "dashboard.json")
        if per_round and os.path.exists(dp):
            with open(dp, encoding="utf-8") as fh:
                dash = json.load(fh)
            ch = False
            for r in dash.get("rounds") or []:
                rnd = r.get("round")
                for c in r.get("candidates") or []:
                    cid = c.get("candidate_id")
                    if (rnd, cid) in per_round and isinstance(c.get("evaluators"), dict):
                        c["evaluators"]["output_compactness"] = per_round[(rnd, cid)]
                        ch = True
            if ch:
                with open(dp, "w", encoding="utf-8") as fh:
                    json.dump(dash, fh)
                dash_written += 1
    return (rf_written, dash_written, len(cycles))


def _targets(path: str) -> list[str]:
    """A single campaign dir (has ``cycles/``), else every campaign dir under it."""
    if os.path.isdir(os.path.join(path, "cycles")):
        return [path]
    return sorted(d for d in glob.glob(os.path.join(path, "*")) if os.path.isdir(d))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    targets = _targets(sys.argv[1])
    tot_rf = tot_dash = tot_cyc = touched = 0
    for camp in targets:
        rf, dash, cyc = backfill(camp)
        if cyc:
            touched += 1
            tot_rf += rf
            tot_dash += dash
            tot_cyc += cyc
            if len(targets) > 1:
                print(f"  {os.path.basename(camp):40} {rf:4} round files, {dash:2} dashboards")
    print(
        f"\nbackfilled output_compactness across {touched} campaign(s): "
        f"{tot_rf} round files, {tot_dash} dashboards, {tot_cyc} cycles"
    )
