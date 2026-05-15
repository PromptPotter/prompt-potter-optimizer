"""One-shot disk migration — Phases 1 & 2 of state-sync cleanup.

**Phase 1 — identity collapse.** Strips ``campaign_id`` from every
``index.json``. Directory name is the authoritative cycle identity
post-migration; ``CampaignStore.load`` injects ``campaign_id`` from
the dir name on read.

**Phase 2 — active-cycle drift fix.** Strips ``cycle_id`` and
``cycle_id_path`` from every ``dashboard.json``. Active-cycle
identity lives only in ``active_session.json``; the dashboard file
describes whatever the active cycle's loop produced and no longer
claims its own identity. Eliminates the family-root-shared file's
cycle-id flipping during multi-variant sweeps.

Reports any cases where the embedded ``campaign_id`` disagreed with
the dir name (e.g. manual ``mv`` backups like ``_pre_wave1to4``) so
the operator can confirm none of them were intentional.

Idempotent — running twice is a no-op.

Usage:
    python scripts/migrate_state_sync_v1.py            # tenant=default
    python scripts/migrate_state_sync_v1.py --tenant foo
    python scripts/migrate_state_sync_v1.py --dry-run

See ``docs/specs/state-sync-cleanup.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _strip_fields_from(path: Path, fields: tuple[str, ...]) -> tuple[bool, dict]:
    """Read, strip, return ``(changed, data)``. Returns ``(False, {})`` on read error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, {}
    if not isinstance(data, dict):
        return False, {}
    changed = False
    for f in fields:
        if f in data:
            data.pop(f)
            changed = True
    return changed, data


def migrate(tenant: str, *, dry_run: bool) -> int:
    """Strip stale identity fields from every index.json + dashboard.json."""
    campaigns_root = Path(".promptpotter") / "projects" / tenant / "campaigns"
    if not campaigns_root.is_dir():
        print(f"no campaigns dir at {campaigns_root} — nothing to do")
        return 0

    index_mutated = 0
    dashboard_mutated = 0
    mismatches: list[tuple[str, str]] = []
    verb = "would strip" if dry_run else "stripped"

    # Phase 1 — index.json::campaign_id
    for index_path in campaigns_root.rglob("index.json"):
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP (unreadable): {index_path}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        dir_name = index_path.parent.name
        embedded = data.get("campaign_id")
        if embedded is None:
            continue
        if embedded != dir_name:
            mismatches.append((dir_name, embedded))
        if not dry_run:
            data.pop("campaign_id", None)
            index_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        index_mutated += 1

    # Phase 2 — dashboard.json::cycle_id + cycle_id_path
    for dash_path in campaigns_root.rglob("dashboard.json"):
        changed, data = _strip_fields_from(dash_path, ("cycle_id", "cycle_id_path"))
        if not changed:
            continue
        if not dry_run:
            dash_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        dashboard_mutated += 1

    print(f"{verb} campaign_id from {index_mutated} index.json file(s)")
    print(f"{verb} cycle_id/cycle_id_path from {dashboard_mutated} dashboard.json file(s)")
    if mismatches:
        print(f"\n{len(mismatches)} pre-existing mismatches (dir != embedded campaign_id):")
        for dir_name, embedded in mismatches:
            print(f"  dir={dir_name!r}  embedded={embedded!r}")
        print(
            "\nThese were resolved silently — dir name wins. Review the list to "
            "confirm none of the backups were intentionally aliased."
        )
    return index_mutated + dashboard_mutated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    migrate(args.tenant, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
