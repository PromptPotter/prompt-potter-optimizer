"""What was done in one workspace, read off its ledgers; `account_spend.py` reads what it cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import (
    AccountEventRecord,
    BankedCommand,
    CommandRecord,
    TrailTombstoneRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_spend import sandbox_cycle_dirs
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandboxes_dir,
    read_sandbox_owner,
)
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
    records: ClassVar[RecordClasses] = (AccountEventRecord, CommandRecord, TrailTombstoneRecord)

    def __init__(self) -> None:
        self._found: list[TrailLine] = []
        self._banked: set[tuple[str, str]] = set()

    def feed(
        self, offset: int, record: AccountEventRecord | CommandRecord | TrailTombstoneRecord
    ) -> None:
        match record:
            case AccountEventRecord():
                self._found.append(
                    TrailLine(record.timestamp, record.event, record.subject, record.user_id)
                )
            case CommandRecord():
                self._found.append(
                    TrailLine(record.timestamp, record.kind, "", record.issued_by_user_id)
                )
            case TrailTombstoneRecord():
                self._banked.add((record.campaign_id, record.cycle_id))
                self._found.extend(
                    TrailLine(done.at, done.kind, done.subject, done.user_id)
                    for done in record.commands
                )

    def value(self) -> tuple[tuple[TrailLine, ...], frozenset[tuple[str, str]]]:
        return tuple(self._found), frozenset(self._banked)


# A roster is per PROCESS (`LedgerIndex._screened`): once a read names this one, every ledger that
# process indexes keeps its commands. The admin bot, the `trail` verb and a delete pay that.
_TRAIL_FOLDS = (_Trail,)


def _cycle_lines(cycle_dir: Path, *, inside: str = "") -> list[TrailLine]:
    """``inside`` names the campaign whose inner sandbox holds the cycle."""
    address = f"{cycle_dir.parent.parent.name}/{cycle_dir.name}"
    lines, _ = LedgerIndex.of(CycleLayout(cycle_dir).ledger, _TRAIL_FOLDS).view(_Trail)
    return [line._replace(subject=f"{inside}>{address}" if inside else address) for line in lines]


def workspace_lines(campaigns: CampaignStore) -> Iterator[TrailLine]:
    """Unordered: the workspace's own ledgers, then the cycles of every inner sandbox it owns."""
    lines, _ = LedgerIndex.of(CycleEventLog.workspace_path(campaigns.workspace), _TRAIL_FOLDS).view(
        _Trail
    )
    yield from lines
    for campaign_dir in campaigns.iter_campaign_dirs():
        for cycle_dir in campaigns.campaign_cycle_dirs(campaign_dir.name):
            yield from _cycle_lines(cycle_dir)
    inner = inner_sandboxes_dir(campaigns.workspace.parent)
    for sandbox in sorted(inner.iterdir()) if inner.is_dir() else ():
        owner = read_sandbox_owner(sandbox) if sandbox.is_dir() else None
        if owner is not None and owner.tenant_id == campaigns.workspace.name:
            for cycle_dir in sandbox_cycle_dirs(sandbox):
                yield from _cycle_lines(cycle_dir, inside=owner.campaign_id)


def bank_trail(
    *, workspace: WorkspaceDir, cycle_dirs: list[Path], campaign_id: str, cycle_id: str = ""
) -> None:
    """Destroyers only, beside `bank_spend`: that a thing was commanded outlives the thing.

    A tombstone already standing is a crashed delete's, kept for the retry.
    """
    commands = [
        BankedCommand(at=line.at, kind=line.what, subject=line.subject, user_id=line.actor)
        for cycle_dir in cycle_dirs
        # A cycle outside the workspace is an inner sandbox's, and `campaign_id` names its owner.
        for line in _cycle_lines(
            cycle_dir, inside="" if cycle_dir.is_relative_to(workspace) else campaign_id
        )
    ]
    if not commands:
        return
    log = CycleEventLog.open_workspace(workspace)
    _, banked = LedgerIndex.of(log.path, _TRAIL_FOLDS).view(_Trail)
    if (campaign_id, cycle_id) not in banked:
        log.append(
            TrailTombstoneRecord(
                campaign_id=campaign_id, cycle_id=cycle_id, commands=tuple(commands)
            )
        )


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)


__all__ = ["TrailLine", "bank_trail", "in_time_order", "workspace_lines"]
