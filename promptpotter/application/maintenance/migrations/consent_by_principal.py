"""An account file older than per-principal consent holds one acceptance, the newest, which a
delegate's accept may have written over its delegator's. Each principal's latest acceptance is
rebuilt from the consent trail, whose `terms_accepted` line carries its version as `subject`; the
file's own is kept under the principal its trail line names, else under the account's own id."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.io import read_text_optional
from promptpotter.infrastructure.store.user_store import USER_FILE, UserStore, account_dirs


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for workspace in account_dirs(projects_root):
        path = Path(workspace) / USER_FILE
        held = read_text_optional(path)
        user = json.loads(held) if held.strip() else {}
        if "terms_accepted" not in user:
            continue
        consent = user["terms_accepted"]
        if consent is None:
            del user["terms_accepted"]
        elif "version" in consent:
            # Trail order is time order: the last line per principal is its latest acceptance.
            accepted = {
                line.user_id: {"version": line.subject, "accepted_at": line.at}
                for line in UserStore(workspace).consent_trail()
                if line.event == "terms_accepted" and line.subject
            }
            by = next(
                (
                    p
                    for p, theirs in accepted.items()
                    if theirs["accepted_at"] == consent["accepted_at"]
                ),
                user["user_id"],
            )
            user["terms_accepted"] = {**accepted, by: consent}
        else:
            continue
        data = json.dumps(user, indent=2, ensure_ascii=False).encode("utf-8")
        out.append(Staged(path, data, f"{workspace.name}: consent keyed by its principal"))
    return out
