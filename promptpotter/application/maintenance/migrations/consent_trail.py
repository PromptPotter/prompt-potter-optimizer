"""A workspace ledger older than the consent trail holds the account's sign-ins, openings and terms
acceptances: each is staged onto its `consent.jsonl`. The ledger is run data and is never rewritten;
its account lines do not load, so the landing that carries them is asked to reset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.io import read_lines
from promptpotter.infrastructure.store.user_store import (
    CONSENT_FILE,
    ConsentEvent,
    ConsentLine,
    UserStore,
    account_dirs,
)

_CONSENT = frozenset(get_args(ConsentEvent))


def _consent_of(raw: str) -> ConsentLine | None:
    record = json.loads(raw)
    if record.get("record_type") != "account_event" or record["event"] not in _CONSENT:
        return None
    return ConsentLine(
        event=record["event"],
        user_id=record["user_id"],
        subject=record.get("subject", ""),
        device=record.get("device"),
        at=record["timestamp"],
    )


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for workspace in account_dirs(projects_root):
        held = UserStore(workspace).consent_trail()
        moved = [
            line
            for raw in read_lines(CycleEventLog.workspace_path(workspace))
            if (line := _consent_of(raw)) is not None and line not in held
        ]
        if not moved:
            continue
        trail = Path(workspace) / CONSENT_FILE
        lines = [
            *read_lines(trail),
            *(
                json.dumps(line.model_dump(mode="json"), ensure_ascii=False) + "\n"
                for line in moved
            ),
        ]
        out.append(
            Staged(
                trail,
                "".join(lines).encode("utf-8"),
                f"{workspace.name}: {len(moved)} consent line(s) off the workspace ledger",
            )
        )
    return out
