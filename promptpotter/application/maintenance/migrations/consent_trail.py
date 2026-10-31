"""Sign-ins, openings and terms acceptances leave the workspace ledger, which is run data, for the
account's consent trail; the account events left there lose the `device` only a sign-in carried."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.io import read_lines, rewrite_lines
from promptpotter.infrastructure.store.user_store import (
    ConsentEvent,
    ConsentLine,
    UserStore,
    account_dirs,
)

_CONSENT = frozenset(get_args(ConsentEvent))


def _account_event(raw: str) -> dict[str, Any] | None:
    record = json.loads(raw)
    return record if record.get("record_type") == "account_event" else None


def _consent_of(raw: str) -> ConsentLine | None:
    record = _account_event(raw)
    if record is None or record["event"] not in _CONSENT:
        return None
    return ConsentLine(
        event=record["event"],
        user_id=record["user_id"],
        subject=record.get("subject", ""),
        device=record.get("device"),
        at=record["timestamp"],
    )


def _reshaped(lines: list[str]) -> tuple[list[str] | None, None]:
    kept: list[str] = []
    for raw in lines:
        record = _account_event(raw)
        if record is None:
            kept.append(raw)
        elif record["event"] not in _CONSENT:
            record.pop("device", None)
            kept.append(json.dumps(record, ensure_ascii=False) + "\n")
    return (None if kept == lines else kept), None


def migrate(projects_root: Path) -> list[str]:
    said: list[str] = []
    for workspace in account_dirs(projects_root):
        ledger = CycleEventLog.workspace_path(workspace)
        users = UserStore(workspace)
        held = set(users.consent_trail())
        moved = [
            line
            for raw in read_lines(ledger)
            if (line := _consent_of(raw)) is not None and line not in held
        ]
        # Appended before the ledger drops them, so a crash between the two loses no consent.
        for line in moved:
            users.record(line)
        rewrite_lines(ledger, _reshaped)
        if moved:
            said.append(f"{workspace.name}: {len(moved)} consent line(s) off the workspace ledger")
    return said
