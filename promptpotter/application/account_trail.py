"""The operator-admin read (ADR-0004) of what an account did; spend is `jobs/install_spend.py`."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import AccountEvent, AccountEventRecord, CommandRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_trail
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import accounts_by_email
from promptpotter.shared.identity import acting_principal_id


class TrailLine(NamedTuple):
    at: str
    what: str
    # The campaign and cycle of a command on a cycle's ledger; an account event's detail.
    subject: str
    actor: str


def record_account_event(stores: Stores, event: AccountEvent, **detail: str) -> None:
    CycleEventLog.open_workspace(stores.base_dir).append(
        AccountEventRecord(event=event, user_id=acting_principal_id(stores.identity), detail=detail)
    )


def _line(record: AccountEventRecord | CommandRecord, subject: str) -> TrailLine:
    if isinstance(record, AccountEventRecord):
        return TrailLine(
            record.timestamp, record.event, " ".join(record.detail.values()), record.user_id
        )
    return TrailLine(record.timestamp, record.kind, subject, record.issued_by_user_id)


def _in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    return sorted(lines, key=lambda line: datetime.fromisoformat(line.at))


def account_trail(workspace: WorkspaceDir) -> list[TrailLine]:
    campaigns = CampaignStore(workspace)
    ledgers = [(CycleEventLog.workspace_path(workspace), "")]
    ledgers += [
        (ledger, f"{campaign_dir.name}/{CycleLayout.of_ledger(ledger).cycle_dir.name}")
        for campaign_dir in campaigns.iter_campaign_dirs()
        for ledger in campaigns.campaign_cycle_ledgers(campaign_dir.name)
    ]
    return _in_time_order(
        _line(record, subject)
        for ledger, subject in ledgers
        for record in scan_ledger_trail(ledger)
    )


class EmailTrail(NamedTuple):
    # None where no readable account carries the email.
    lines: list[TrailLine] | None
    # Accounts whose `user.json` could not be read, so none of them could be matched.
    unreadable: list[str]


def trail_by_email(projects_root: Path, email: str) -> EmailTrail:
    """``actor`` is blank on the account's own lines."""
    accounts, unreadable = accounts_by_email(projects_root, email)
    if not accounts:
        return EmailTrail(None, unreadable)
    own = {user.user_id for _, user in accounts}
    lines = _in_time_order(line for workspace, _ in accounts for line in account_trail(workspace))
    return EmailTrail(
        [line._replace(actor="" if line.actor in own else line.actor) for line in lines],
        unreadable,
    )


def trail_row(line: TrailLine) -> str:
    stamp = line.at[:19].replace("T", " ")
    actor = f"by {line.actor}" if line.actor else ""
    return " ".join(part for part in (stamp, line.what, line.subject, actor) if part)


def unreadable_note(unreadable: list[str]) -> str:
    return f"Could not read: {', '.join(unreadable)}" if unreadable else ""


__all__ = [
    "EmailTrail",
    "TrailLine",
    "account_trail",
    "record_account_event",
    "trail_by_email",
    "trail_row",
    "unreadable_note",
]
