"""One-shot: measurements/{run_id}.json -> measurements/runs/{run_id}.jsonl.

The run detail became an append-only log keyed on ``k`` (one ``"run"`` header row, one
``"m:{sample_id}"`` row per measurement) so the scoring walk stops rewriting the whole file
after every sample. This carries the existing archive across — 90 MB of paid LLM spend that
would otherwise have to be re-measured.

Report-only by default; ``--apply`` writes. Idempotent and resumable: a run already migrated
is skipped, and the ``.json`` is unlinked only after its ``.jsonl`` verifies.

``index.jsonl`` is NOT touched — it keys on ``run_id``, which does not change.

    python scripts/migrate_measurement_details.py            # report
    python scripts/migrate_measurement_details.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

FOLD_KEY = "k"
HEADER_KEY = "run"


def _measurement_key(item: dict[str, object]) -> str:
    sid = item.get("sample_id")
    if isinstance(sid, int):
        return f"m:{sid}"
    digest = hashlib.blake2b(
        json.dumps(item, sort_keys=True, default=str).encode(), digest_size=8
    ).hexdigest()
    return f"m:!{digest}"


def _rows(detail: dict[str, object]) -> list[dict[str, object]]:
    """Header last — it is the commit marker, exactly as ``append_run`` writes it."""
    measurements = detail.get("measurements") or []
    rows: list[dict[str, object]] = [
        {FOLD_KEY: _measurement_key(m), **m} for m in measurements if isinstance(m, dict)
    ]
    rows.append({FOLD_KEY: HEADER_KEY, **{k: v for k, v in detail.items() if k != "measurements"}})
    return rows


def _migrate(src: pathlib.Path, runs_dir: pathlib.Path, *, apply: bool) -> int | None:
    """Measurement count carried across, or ``None`` if *src* is not a measurement detail.

    Positive identification, exactly as ``reindex`` does its GC: a file that does not carry
    a ``content_hash`` is left where it is and COUNTED — never migrated, never deleted, and
    never silently swallowed (this is how the dead ``prompt_aliases.json`` surfaced)."""
    detail = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(detail, dict) or "content_hash" not in detail:
        return None
    rows = _rows(detail)
    n = len(rows) - 1
    if not apply:
        return n

    dst = runs_dir / f"{src.stem}.jsonl"
    dst.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    # Verify before unlinking — a half-migrated run is INVISIBLE afterwards (the reader
    # folds `runs/`, so a leftover `.json` is not "stale", it is gone), and it is paid spend.
    back = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines() if line]
    if len(back) != len(rows) or back[-1].get(FOLD_KEY) != HEADER_KEY:
        raise OSError(f"{dst.name}: round-trip failed, .json left in place")
    src.unlink()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Write (default: report only).")
    ap.add_argument("--root", default=".promptpotter/projects", help="Workspace tenant root.")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    if not root.is_dir():
        print(f"No workspace at {root}", file=sys.stderr)
        return 1

    total_runs = total_meas = 0
    for store in sorted(root.glob("*/measurements")):
        candidates = [
            p for p in sorted(store.glob("*.json")) if not p.name.startswith("hard_samples_")
        ]
        if not candidates:
            continue
        runs_dir = store / "runs"
        if args.apply:
            runs_dir.mkdir(parents=True, exist_ok=True)

        runs = meas = 0
        skipped: list[str] = []
        for src in candidates:
            n = _migrate(src, runs_dir, apply=args.apply)
            if n is None:
                skipped.append(src.name)
                continue
            meas += n
            runs += 1
        print(f"{store}: {runs} run(s), {meas} measurement(s)")
        for name in skipped:
            print(f"  skipped (not a measurement detail, left in place): {name}")
        total_runs += runs
        total_meas += meas

        if args.apply:
            leftover = [
                p.name
                for p in store.glob("*.json")
                if not p.name.startswith("hard_samples_") and p.name not in skipped
            ]
            carried = sum(
                sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line) - 1
                for p in runs_dir.glob("*.jsonl")
            )
            if leftover:
                print(f"  FAILED: {len(leftover)} detail(s) still .json", file=sys.stderr)
                return 1
            if carried < meas:
                print(f"  FAILED: {carried} measurement rows < {meas} read", file=sys.stderr)
                return 1

    verb = "migrated" if args.apply else "would migrate"
    print(f"\n{verb} {total_runs} run(s) / {total_meas} measurement(s)")
    if not args.apply and total_runs:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
