"""The host's claim marker before its `renamed` flag went: the flag is dropped, the claim kept."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.infrastructure.identity.paths import IDENTITY_DIR, IdentityPaths
from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.io import read_text_optional


def staged(projects_root: Path) -> list[Staged]:
    marker = IdentityPaths(projects_root.parent / IDENTITY_DIR).default_claim_marker
    held = read_text_optional(marker)
    claim = json.loads(held) if held.strip() else {}
    if "renamed" not in claim:
        return []
    del claim["renamed"]
    data = json.dumps(claim, indent=2, ensure_ascii=False).encode("utf-8")
    return [Staged(marker, data, f"{claim['user_id']}: the claim's `renamed` dropped")]
