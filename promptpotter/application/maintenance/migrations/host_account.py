"""The host before it was the declared account: the claim marker's account records its email (else
`HOST_ADMIN_EMAIL`) under the marker's id, and the marker is a retired file the landing drops. A
marker naming no opened account loses nothing: the host's next sign-in claims."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.settings import settings
from promptpotter.infrastructure.identity.paths import IDENTITY_DIR
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged
from promptpotter.infrastructure.store.user_store import USER_FILE
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    marker = projects_root.parent / IDENTITY_DIR / "default_claimed.json"
    held = read_as_staged(marker, before) or b""
    if not held.strip():
        return []
    user_id = json.loads(held)["user_id"]
    path = projects_root / user_id / USER_FILE
    record = read_as_staged(path, before) or b""
    if user_id == TERMINAL_IDENTITY_ID or not record.strip():
        return []
    user = json.loads(record)
    user |= {
        "user_id": user_id,
        "tenant_id": user_id,
        "email": user["email"] or settings.HOST_ADMIN_EMAIL.strip() or None,
    }
    data = json.dumps(user, indent=2, ensure_ascii=False).encode("utf-8")
    return [Staged(path, data, f"{user_id}: the host's claim moves onto its account")]
