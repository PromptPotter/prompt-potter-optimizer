"""One-shot migration: back-populate ``dashboard.json::rounds[]`` + ``origin`` block. Idempotent.

Reads each ``dashboard.json``, rebuilds ``rounds[]`` from sibling ``rounds/round_NNNN.json``,
converts the old ``origin_accuracy`` / ``origin_samples`` scalars into the nested ``origin`` block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _build_round_summary_dict(round_file: Path) -> dict[str, Any] | None:
    """One ``RoundSummary``-shaped dict from a ``round_NNNN.json``; ``None`` if unreadable.

    Joins ``candidate_scores`` (compact ``C{round}.{idx}`` label) with ``scoreboard``
    (accuracy / composite / winner-flag) by ``candidate_id``.
    """
    try:
        doc = json.loads(round_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    score_rows = doc.get("candidate_scores") or []
    by_cid: dict[str, dict[str, Any]] = {}
    for s in score_rows:
        if not isinstance(s, dict):
            continue
        cid = str(s.get("candidate_id") or "")
        if cid:
            by_cid[cid] = s
    scoreboard = doc.get("scoreboard") or []
    candidates: list[dict[str, Any]] = []
    for row in scoreboard:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("candidate_id") or "")
        score_row = by_cid.get(cid, {})
        compact_label = str(score_row.get("label") or "")
        changes = str(
            score_row.get("changes_description")
            or row.get("changes_description")
            or row.get("label")
            or ""
        )
        # Old rounds carry only ``total`` — use it as fallback for scored/expected.
        scored = row.get("scored_samples")
        if not isinstance(scored, int):
            scored = int(row.get("total") or 0)
        expected = row.get("expected_samples")
        if not isinstance(expected, int):
            expected = int(row.get("total") or 0)
        candidates.append(
            {
                "candidate_id": cid,
                "label": compact_label,
                "accuracy": float(row.get("accuracy") or 0.0),
                "composite_fitness": float(row.get("composite_fitness") or 0.0),
                "scored_samples": scored,
                "expected_samples": expected,
                "is_winner": bool(row.get("is_winner") or False),
                "evaluators": {
                    str(k): float(v)
                    for k, v in (row.get("evaluators") or {}).items()
                    if isinstance(v, int | float)
                },
                "changes_description": changes,
            }
        )
    return {
        "round": int(doc.get("round") or 0),
        "accuracy": float(doc.get("accuracy") or 0.0),
        "composite_fitness": float(doc.get("composite_fitness") or 0.0),
        "candidates": candidates,
    }


def _origin_block(dash: dict[str, Any]) -> dict[str, Any]:
    """Build the new ``origin: {accuracy, samples}`` block from the old fields."""
    raw_origin = dash.get("origin")
    # Already migrated — preserve it.
    if isinstance(raw_origin, dict):
        accuracy = raw_origin.get("accuracy")
        samples = raw_origin.get("samples")
    else:
        accuracy = dash.get("origin_accuracy")
        if accuracy is None and isinstance(raw_origin, int | float):
            accuracy = raw_origin
        samples = dash.get("origin_samples")
    return {
        "accuracy": float(accuracy) if isinstance(accuracy, int | float) else 0.0,
        "samples": int(samples) if isinstance(samples, int) else 0,
    }


def _migrate(dash_path: Path) -> tuple[bool, str]:
    """Return ``(changed, reason)`` for one ``dashboard.json``."""
    try:
        dash = json.loads(dash_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"unreadable ({exc})"

    cycle_dir = dash_path.parent
    rounds_dir = cycle_dir / "rounds"
    round_files = sorted(rounds_dir.glob("round_*.json")) if rounds_dir.is_dir() else []
    expected_rounds = [
        s for s in (_build_round_summary_dict(f) for f in round_files) if s is not None
    ]
    expected_rounds.sort(key=lambda r: int(r.get("round") or 0))

    new_dash = dict(dash)
    new_dash["origin"] = _origin_block(dash)
    new_dash.pop("origin_accuracy", None)
    new_dash.pop("origin_samples", None)
    new_dash["rounds"] = expected_rounds

    if new_dash == dash:
        return False, "no-op"

    tmp = dash_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(new_dash, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(dash_path)
    return True, f"{len(expected_rounds)} rounds"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=".promptpotter",
        help="workspace root (default: .promptpotter)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"no workspace at {root}", file=sys.stderr)
        return 1

    n_total = 0
    n_changed = 0
    for dash_path in sorted(root.rglob("dashboard.json")):
        n_total += 1
        if args.dry_run:
            rounds_dir = dash_path.parent / "rounds"
            n = len(list(rounds_dir.glob("round_*.json"))) if rounds_dir.is_dir() else 0
            print(f"  would-migrate ({n} rounds): {dash_path}")
            continue
        changed, reason = _migrate(dash_path)
        if changed:
            n_changed += 1
            print(f"  migrated ({reason}): {dash_path}")
        else:
            print(f"  skip ({reason}): {dash_path}")

    verb = "would migrate" if args.dry_run else "migrated"
    print(f"\n{verb} {n_changed}/{n_total} dashboards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
