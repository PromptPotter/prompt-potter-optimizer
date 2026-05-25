"""One-shot on-disk migration: lift OSP memory fields under `memory` nested key.

Walks `.promptpotter/projects/*/campaigns/*/**/*.json` and, for every nested
`opt_search_point` dict it finds, moves these six fields under `memory`:

- wounds
- l1_layout
- l1_overrides
- l1_supplemental_rules
- l1_situational_examples
- task_context

Idempotent: skips an OSP that already has a `memory` key. Pretty-prints
JSON back with sort_keys=False to preserve key order on unchanged sections.

Usage: ``python scripts/migrate_to_l2l3_memory.py [--dry-run]``
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

MEMORY_FIELDS = (
    "wounds",
    "l1_layout",
    "l1_overrides",
    "l1_supplemental_rules",
    "l1_situational_examples",
    "task_context",
)


def migrate_osp(osp: dict) -> bool:
    """Lift the six memory fields under `memory`. Returns True if modified."""
    if "memory" in osp:
        return False
    memory_payload = {f: osp.pop(f) for f in MEMORY_FIELDS if f in osp}
    if not memory_payload:
        return False
    osp["memory"] = memory_payload
    return True


def walk(obj: object) -> int:
    """Recurse; migrate every nested opt_search_point dict. Returns count migrated."""
    n = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "opt_search_point" and isinstance(v, dict):
                if migrate_osp(v):
                    n += 1
            else:
                n += walk(v)
    elif isinstance(obj, list):
        for item in obj:
            n += walk(item)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=".promptpotter/projects", help="Workspace tenant root.")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    if not root.exists():
        print(f"No workspace at {root}", file=sys.stderr)
        return 1

    total_files_touched = 0
    total_osps_migrated = 0
    for p in root.rglob("*.json"):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeError, FileNotFoundError):
            continue
        if "opt_search_point" not in text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        n = walk(data)
        if n == 0:
            continue
        total_files_touched += 1
        total_osps_migrated += n
        if not args.dry_run:
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    verb = "would migrate" if args.dry_run else "migrated"
    print(f"{verb}: {total_osps_migrated} OSPs across {total_files_touched} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
