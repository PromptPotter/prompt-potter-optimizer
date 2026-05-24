"""One-shot migration: back-populate ``dashboard.json::rounds[]`` + origin block.

The webapp's display-source unification (``docs/specs/webapp-display-source-
unification.md``) moved the completed-round summary surface onto
``dashboard.json::rounds[]`` and replaced the top-level scalars
``origin_accuracy`` + ``origin_samples`` with a nested ``origin: {accuracy,
samples}`` block. The new projection writes both shapes at round close —
but dashboards last persisted by the prior projection remain on disk with
the old shape, so the webapp shows "No rounds on disk yet" for every
frozen pre-shipment cycle.

This script reads each ``dashboard.json`` on disk and:

* builds ``rounds[]`` from the sibling ``rounds/round_NNNN.json`` files,
* converts top-level ``origin_accuracy`` / ``origin_samples`` (and the
  prior scalar ``origin: float``) into the new ``origin: {accuracy,
  samples}`` block, then strips the obsolete top-level scalars,
* atomically writes the result back.

Idempotent: re-running on a migrated file is a no-op.

Run from project root::

    python scripts/migrate_dashboard_rounds.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _build_round_summary_dict(round_file: Path) -> dict[str, Any] | None:
    """One ``RoundSummary``-shaped dict from a ``round_NNNN.json`` on disk.

    Returns ``None`` if the file is unreadable / malformed. The chart's
    x-axis label is the compact ``C{round}.{idx}`` form — carried on
    ``candidate_scores[*].label`` in the on-disk file. The ``scoreboard``
    is the source for accuracy / composite / winner-flag, but its own
    ``label`` field stores the long-form description and is NOT the
    chart label. We join the two by ``candidate_id`` so each emitted
    summary row carries the compact label + the description that the
    UI's tooltips expect on ``changes_description``.
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
        # Compact "C{round}.{idx}" label lives on candidate_scores; the
        # scoreboard's `label` is the long-form description (the
        # ``changes_description``) and would otherwise leak onto the x-axis.
        compact_label = str(score_row.get("label") or "")
        changes = str(
            score_row.get("changes_description")
            or row.get("changes_description")
            or row.get("label")
            or ""
        )
        # ``scored_samples`` / ``expected_samples`` only exist on freshly-
        # written rounds. Old rounds carry ``hits``/``total`` — same total,
        # different field name. Use ``total`` as best-effort fallback for
        # both since we have no separate "expected" record.
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
    """Build the new ``origin: {accuracy, samples}`` block from the old fields.

    Pulls from the prior top-level ``origin_accuracy`` and ``origin_samples``
    scalars (the old projection's outputs). Falls back to the scalar
    ``origin: float`` when ``origin_accuracy`` is absent — earlier
    projection versions stored just that.
    """
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
    """Return ``(changed, reason)`` for one ``dashboard.json``.

    The migration is shape-driven: a dashboard with the new origin block
    AND a populated ``rounds[]`` matching the disk is considered done.
    """
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
            # Simulate without writing — peek at the file to count round
            # files that would feed the new ``rounds[]``.
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
