"""What was done in one workspace, read off its ledgers; `account_spend.py` reads what it cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import (
    AccountEventRecord,
    BankedCommand,
    BankedSubject,
    CommandRecord,
    Device,
    RunPhaseRecord,
    TrailTombstoneRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_spend import bank_spend, sandbox_cycle_dirs
from promptpotter.infrastructure.store.layout import (
    DRAFTS_DIR,
    CycleLayout,
    draft_dirs,
    inner_sandboxes_dir,
    read_sandbox_owner,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.domain.spend import Budget, CeilingMeter
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


RUN_STOPPED = "run_stopped"


class TrailLine(NamedTuple):
    at: str
    what: str
    # Where a command was addressed (campaign/cycle, blank on the workspace); what an account event
    # carried; a run's stop is its address, then why.
    subject: str
    # The acting principal: a delegate's own id on a line in its delegator's workspace; blank on a stop.
    actor: str
    # The campaign a command acted on, blank on the workspace, a draft and an account event.
    campaign_id: str
    # What a sign-in or an opening came from; unset on every other line.
    device: Device | None = None


class _Trail:
    records: ClassVar[RecordClasses] = (
        AccountEventRecord,
        CommandRecord,
        RunPhaseRecord,
        TrailTombstoneRecord,
    )

    def __init__(self) -> None:
        self._found: list[TrailLine] = []
        self._banked: set[BankedSubject] = set()

    def feed(
        self, record: AccountEventRecord | CommandRecord | RunPhaseRecord | TrailTombstoneRecord
    ) -> None:
        match record:
            case RunPhaseRecord(stop_reason=StopReason() as reason) if (
                stop_reason_outcome(reason) is not StopOutcome.SUCCESS
            ):
                said = STOP_REASON_INFO[reason].label
                self._found.append(
                    TrailLine(
                        record.timestamp,
                        RUN_STOPPED,
                        f"{said} ({record.detail})" if record.detail else said,
                        "",
                        "",
                    )
                )
            case AccountEventRecord():
                self._found.append(
                    TrailLine(
                        record.timestamp,
                        record.event,
                        record.subject,
                        record.user_id,
                        "",
                        record.device,
                    )
                )
            case CommandRecord():
                self._found.append(
                    TrailLine(record.timestamp, record.kind, "", record.issued_by.id, "")
                )
            case TrailTombstoneRecord():
                self._banked.add(record.subject)
                self._found.extend(
                    TrailLine(done.at, done.kind, done.subject, done.user_id, record.campaign_id)
                    for done in record.commands
                )

    def value(self) -> tuple[tuple[TrailLine, ...], frozenset[BankedSubject]]:
        return tuple(self._found), frozenset(self._banked)


# A roster is per PROCESS (`LedgerIndex._screened`): once a read names this one, every ledger that
# process indexes keeps its commands. The admin bot, the `trail` verb and a delete pay that.
_TRAIL_FOLDS = (_Trail,)


def _cycle_lines(cycle_dir: Path, *, inside: str = "") -> list[TrailLine]:
    """``inside`` names the campaign whose inner sandbox holds the cycle; a draft is addressed as one."""
    parent = cycle_dir.parent
    campaign_id = "" if parent.name == DRAFTS_DIR else parent.parent.name
    address = f"{campaign_id or parent.name}/{cycle_dir.name}"
    where = f"{inside}>{address}" if inside else address
    lines, _ = LedgerIndex.of(CycleLayout(cycle_dir).ledger, _TRAIL_FOLDS).view(_Trail)
    return [
        line._replace(
            subject=f"{where}: {line.subject}" if line.subject else where,
            campaign_id=inside or campaign_id,
        )
        for line in lines
    ]


def workspace_lines(campaigns: CampaignStore) -> Iterator[TrailLine]:
    """Unordered: the workspace's own ledgers, then the cycles of every inner sandbox it owns."""
    lines, _ = LedgerIndex.of(CycleEventLog.workspace_path(campaigns.workspace), _TRAIL_FOLDS).view(
        _Trail
    )
    yield from lines
    for campaign_dir in campaigns.iter_campaign_dirs():
        for cycle_dir in campaigns.campaign_cycle_dirs(campaign_dir.name):
            yield from _cycle_lines(cycle_dir)
    for draft_dir in draft_dirs(campaigns.workspace):
        yield from _cycle_lines(draft_dir)
    inner = inner_sandboxes_dir(campaigns.workspace.parent)
    for sandbox in sorted(inner.iterdir()) if inner.is_dir() else ():
        owner = read_sandbox_owner(sandbox) if sandbox.is_dir() else None
        if owner is not None and owner.tenant_id == campaigns.workspace.name:
            for cycle_dir in sandbox_cycle_dirs(sandbox):
                yield from _cycle_lines(cycle_dir, inside=owner.campaign_id)


def bank_before_removal(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    subject: BankedSubject,
    metered: dict[CeilingMeter, Budget],
) -> None:
    """Every destroyer's one bank; a tombstone already standing is a crashed delete's, kept for the retry."""
    bank_spend(workspace=workspace, cycle_dirs=cycle_dirs, subject=subject, metered=metered)
    commands = [
        BankedCommand(at=line.at, kind=line.what, subject=line.subject, user_id=line.actor)
        for cycle_dir in cycle_dirs
        # A cycle outside the workspace is an inner sandbox's, and the campaign names its owner.
        for line in _cycle_lines(
            cycle_dir, inside="" if cycle_dir.is_relative_to(workspace) else subject.campaign_id
        )
    ]
    if not commands:
        return
    log = CycleEventLog.open_workspace(workspace)
    _, banked = LedgerIndex.of(log.path, _TRAIL_FOLDS).view(_Trail)
    if subject not in banked:
        log.append(TrailTombstoneRecord(**subject._asdict(), commands=tuple(commands)))


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)
