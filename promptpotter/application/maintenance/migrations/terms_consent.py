"""`user.json` before a terms acceptance became its consent line alone: each account drops
`terms_accepted`, and an acceptance its trail does not hold is appended there, stamped as the
account recorded it, so no consent is asked again."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.io import read_bytes_optional
from promptpotter.infrastructure.store.user_store import (
    CONSENT_FILE,
    USER_FILE,
    ConsentLine,
    account_dirs,
)


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for workspace in account_dirs(projects_root):
        user_path = workspace / USER_FILE
        user = json.loads(read_bytes_optional(user_path) or b"{}")
        if "terms_accepted" not in user:
            continue
        accepted: dict[str, dict[str, str]] = user.pop("terms_accepted") or {}
        trail_path = workspace / CONSENT_FILE
        trail = read_bytes_optional(trail_path) or b""
        held = {
            (line.user_id, line.subject)
            for raw in trail.splitlines()
            if raw.strip()
            and (line := ConsentLine.model_validate_json(raw)).event == "terms_accepted"
        }
        owed = [
            ConsentLine(
                event="terms_accepted",
                user_id=principal,
                subject=record["version"],
                at=record["accepted_at"],
            ).model_dump_json()
            for principal, record in sorted(accepted.items())
            if (principal, record["version"]) not in held
        ]
        data = json.dumps(user, indent=2, ensure_ascii=False).encode("utf-8")
        out.append(Staged(user_path, data, f"{workspace.name} drops its terms record"))
        if owed:
            lines = (trail if not trail or trail.endswith(b"\n") else trail + b"\n") + "".join(
                f"{line}\n" for line in owed
            ).encode("utf-8")
            out.append(
                Staged(trail_path, lines, f"{workspace.name} keeps {len(owed)} acceptance(s)")
            )
    return out
