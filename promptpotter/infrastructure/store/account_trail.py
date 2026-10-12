"""What was done in one workspace, read off its ledgers; `account_spend.py` reads what it cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.run_records import AccountEventRecord, CommandRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


class TrailLine(NamedTuple):
    at: str
    what: str
    # Where a command was addressed (campaign/cycle, blank on the workspace); what an account event carried.
    subject: str
    # The acting principal: a delegate's own id on a line in its delegator's workspace.
    actor: str


class _Trail:
    records: ClassVar[RecordClasses] = (AccountEventRecord, CommandRecord)

    def __init__(self) -> None:
        self._found: list[TrailLine] = []

    def feed(self, offset: int, record: AccountEventRecord | CommandRecord) -> None:
        self._found.append(
            TrailLine(record.timestamp, record.event, record.subject, record.user_id)
            if isinstance(record, AccountEventRecord)
            else TrailLine(record.timestamp, record.kind, "", record.issued_by_user_id)
        )

    def value(self) -> tuple[TrailLine, ...]:
        return tuple(self._found)


# A roster is per PROCESS (`LedgerIndex._screened`): once a read names this one, every ledger that
# process indexes keeps its commands. The admin bot and the `trail` verb pay that; the API never asks.
_TRAIL_FOLDS = (_Trail,)


def workspace_lines(campaigns: CampaignStore) -> Iterator[TrailLine]:
    """Unordered. A deleted campaign took its ledgers, and so its lines, with it."""
    workspace_ledger = CycleEventLog.workspace_path(campaigns.workspace)
    yield from LedgerIndex.of(workspace_ledger, _TRAIL_FOLDS).view(_Trail)
    for campaign_dir in campaigns.iter_campaign_dirs():
        for ledger in campaigns.campaign_cycle_ledgers(campaign_dir.name):
            address = f"{campaign_dir.name}/{CycleLayout.of_ledger(ledger).cycle_dir.name}"
            for line in LedgerIndex.of(ledger, _TRAIL_FOLDS).view(_Trail):
                yield line._replace(subject=address)


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)


__all__ = ["TrailLine", "in_time_order", "workspace_lines"]
