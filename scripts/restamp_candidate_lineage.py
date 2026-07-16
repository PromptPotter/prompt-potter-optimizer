"""Backfill the candidate tier onto every on-disk ledger.

A candidate's identity is now written at MINT (``CandidateMintedRecord``), because identity is
not a measurement and must not share the measurement's durability: a round whose producer died
mid-flight used to leave its candidates unnameable even though their work — at L4, whole inner
campaigns — sat finished on disk.

Cycles written before that record existed carry the tier only in fragments, and the fragments
disagree about WHAT they know. Two facts, two witnesses, and the difference is the whole point:

- **The name** rides ``candidate_scored`` (id + label, per candidate, before the round closes)
  and ``rounds/round_NNNN.json::candidate_scores``. Neither carries a parent link — the round
  file is the MEASUREMENT record, and a measurement has no lineage.
- **The edge** rides ``.runtime/cache/candidates/round_NNNN.json`` alone — the mint-time cache,
  the only pre-existing witness holding ``osp.lineage.parent_id``. It has no round 0 and is
  deleted by ``--keep-results``, so it is not a source the SERVED tree may depend on; reading it
  once, here, is how the edge becomes durable.

So: prefer the mint cache (name AND edge); fall back to the round file (name only). A candidate
the ledger already NAMES still gets its record when that record would add the edge — "named" is
not "placed", and the first pass of this script conflated them.

**Round 0 is the exception, and it is not a gap.** The cache never holds it, so C0's edge comes
from ``index.json::fork.from_candidate_id`` — a fork's C0 descends from the parent cycle's
candidate it was cut from. A root's C0 descends from nothing, and ``None`` is the true answer.

The appended record's ``timestamp`` is the backfill's, not the mint's, and says so: the mint
moment is not recoverable, and inventing one would be a worse lie than a late one.

Usage::

    python scripts/restamp_candidate_lineage.py            # dry run — reports, writes nothing
    python scripts/restamp_candidate_lineage.py --apply    # append
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

from promptpotter.domain.run_records import CandidateMintedRecord

BACKFILL_SOURCE = "restamp:round_file"


def _read_json(path: pathlib.Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! unreadable {path}: {exc}", file=sys.stderr)
        return None


def _origin_parent_id(cycle_dir: pathlib.Path) -> str | None:
    """C0's parent — the parent cycle's candidate this fork was cut from, or ``None``.

    A root cycle's C0 descends from nothing. A fork's descends from the candidate named in
    ``index.json::fork.from_candidate_id``; that is the same edge an inner run draws through
    ``spawned_by``, which is why the tree can treat a fork and an L4 run as one kind of thing.
    """
    index = _read_json(cycle_dir / "index.json")
    if not isinstance(index, dict):
        return None
    fork = index.get("fork")
    if not isinstance(fork, dict):
        return None
    parent = fork.get("from_candidate_id")
    return parent if isinstance(parent, str) and parent else None


def _from_mint_cache(cycle_dir: pathlib.Path) -> dict[tuple[int, int], CandidateMintedRecord]:
    """Every candidate the mint cache holds — the only witness carrying the EDGE."""
    out: dict[tuple[int, int], CandidateMintedRecord] = {}
    for path in sorted((cycle_dir / ".runtime" / "cache" / "candidates").glob("round_*.json")):
        try:
            rnd = int(path.stem.removeprefix("round_"))
        except ValueError:
            continue
        rows = _read_json(path)
        if not isinstance(rows, list):
            continue
        for idx, row in enumerate(rows):
            lineage = (row or {}).get("osp", {}).get("lineage") if isinstance(row, dict) else None
            if not isinstance(lineage, dict) or not lineage.get("id"):
                continue
            out[(rnd, idx)] = CandidateMintedRecord(
                round=rnd,
                idx=idx,
                candidate_id=str(lineage["id"]),
                parent_id=str(lineage["parent_id"]) if lineage.get("parent_id") else None,
                label=f"C{rnd}.{idx + 1}" if rnd else "C0",
                changes_description=str(lineage.get("changes_description") or ""),
                source=BACKFILL_SOURCE,
            )
    return out


def _from_round_files(cycle_dir: pathlib.Path) -> dict[tuple[int, int], CandidateMintedRecord]:
    """Every candidate the round files name. Name only — a measurement carries no lineage."""
    out: dict[tuple[int, int], CandidateMintedRecord] = {}
    for path in sorted((cycle_dir / "rounds").glob("round_*.json")):
        data = _read_json(path)
        if not isinstance(data, dict) or not isinstance(rnd := data.get("round"), int):
            continue
        for idx, cand in enumerate(data.get("candidate_scores") or []):
            if not isinstance(cand, dict) or not cand.get("candidate_id") or not cand.get("label"):
                continue
            out[(rnd, idx)] = CandidateMintedRecord(
                round=rnd,
                idx=idx,
                candidate_id=str(cand["candidate_id"]),
                parent_id=_origin_parent_id(cycle_dir) if rnd == 0 else None,
                label=str(cand["label"]),
                changes_description=str(cand.get("changes_description") or ""),
                source=BACKFILL_SOURCE,
            )
    return out


def _minted_edges(ledger: pathlib.Path) -> dict[tuple[int, int], str | None]:
    """``(round, idx) -> parent_id`` for every ``candidate_minted`` record already on *ledger*.

    Read raw rather than through the scan, which folds mint and score together and so cannot
    answer "is this row already MINTED?" — the question idempotency turns on. Last write wins,
    matching the fold.
    """
    out: dict[tuple[int, int], str | None] = {}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("record_type") != "candidate_minted":
            continue
        rnd, idx = rec.get("round"), rec.get("idx")
        if isinstance(rnd, int) and isinstance(idx, int):
            out[(rnd, idx)] = rec.get("parent_id")
    return out


def _backfill(cycle_dir: pathlib.Path, *, apply: bool, tally: Counter[str]) -> None:
    ledger = cycle_dir / ".runtime" / "ledger.jsonl"
    if not ledger.exists():
        tally["no ledger"] += 1
        return

    # The round file is the fallback, the cache is the truth — so the cache wins the merge.
    candidates = _from_round_files(cycle_dir) | _from_mint_cache(cycle_dir)
    minted = _minted_edges(ledger)
    # Idempotent on the EDGE, not on presence: re-running must be a no-op, but the first pass
    # of this script wrote every record with a null parent (it read the round file, which has
    # no lineage), so a null-parent record is not proof the row is placed — only proof it was
    # named. Re-stamp exactly the rows whose recorded edge differs from the one we hold.
    missing = [
        rec
        for key, rec in sorted(candidates.items())
        if key not in minted or minted[key] != rec.parent_id
    ]
    if not missing:
        tally["already placed"] += 1
        return

    for record in missing:
        edge = record.parent_id[:8] if record.parent_id else "(root)"
        print(
            f"  + {cycle_dir.name} r{record.round} idx{record.idx} "
            f"{record.label} ({record.candidate_id[:8]}) parent={edge}"
        )
        if apply:
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(record.model_dump_json() + "\n")
        tally["appended" if apply else "would append"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Append (default: report only).")
    ap.add_argument("--root", default=".promptpotter", help="Workspace root.")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    if not root.exists():
        print(f"No workspace at {root}", file=sys.stderr)
        return 1

    tally: Counter[str] = Counter()
    # Sandboxes nest under `.inner/`, so one recursive glob covers every depth.
    for cycle_dir in sorted(root.glob("**/campaigns/*/cycles/*")):
        if cycle_dir.is_dir():
            _backfill(cycle_dir, apply=args.apply, tally=tally)

    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(tally.items())) or "nothing found")
    if not args.apply and tally["would append"]:
        print("\nDry run. Re-run with --apply to append.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
