"""`user.json` as v0.8.19 wrote it holds one terms acceptance (`terms_accepted: {version,
accepted_at}`), the newest, which a delegate's accept may have written over its delegator's. Each
account drops it, and an acceptance its trail does not hold is appended there under the principal
whose trail line it is (matched on `accepted_at`), else the account's own id, stamped as the account
recorded it, so no consent is asked again."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.infrastructure.store.data_root import Staged, read_as_staged
from promptpotter.infrastructure.store.user_store import (
    CONSENT_FILE,
    USER_FILE,
    ConsentLine,
    account_dirs,
)


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    out: list[Staged] = []
    for workspace in account_dirs(projects_root):
        user_path = workspace / USER_FILE
        user = json.loads(read_as_staged(user_path, before) or b"{}")
        if "terms_accepted" not in user:
            continue
        record: dict[str, str] | None = user.pop("terms_accepted")
        trail_path = workspace / CONSENT_FILE
        trail = read_as_staged(trail_path, before) or b""
        accepts = [
            line
            for raw in trail.splitlines()
            if raw.strip()
            and (line := ConsentLine.model_validate_json(raw)).event == "terms_accepted"
        ]
        owed: list[str] = []
        if record is not None:
            by = next(
                (line.user_id for line in accepts if line.at == record["accepted_at"]),
                user["user_id"],
            )
            if all((line.user_id, line.subject) != (by, record["version"]) for line in accepts):
                owed.append(
                    ConsentLine(
                        event="terms_accepted",
                        user_id=by,
                        subject=record["version"],
                        at=record["accepted_at"],
                    ).model_dump_json()
                )
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
