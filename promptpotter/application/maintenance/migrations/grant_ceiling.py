"""`grants.json` before the delegate spend ceiling went: each grant drops `spend_ceiling_usd`, its
delegator and capabilities kept. No writer ever set one, so no bound is lost."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.infrastructure.identity.paths import IDENTITY_DIR, IdentityPaths
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    path = IdentityPaths(projects_root.parent / IDENTITY_DIR).grants
    held = read_as_staged(path, before) or b""
    grants = json.loads(held).get("grants", {}) if held.strip() else {}
    moved = [sub for sub, grant in grants.items() if "spend_ceiling_usd" in grant]
    if not moved:
        return []
    for sub in moved:
        del grants[sub]["spend_ceiling_usd"]
    data = json.dumps({"grants": grants}, indent=2, ensure_ascii=False).encode("utf-8")
    return [Staged(path, data, f"{len(moved)} grant(s) lose the delegate spend ceiling")]
