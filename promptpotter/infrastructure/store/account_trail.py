"""What was done in one workspace, read off its consent trail, command log and ledgers; `account_spend.py` reads what it cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.command_events import draft_of, subject_of
from promptpotter.domain.cycle_paths import Subject, WorkspaceDir
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import (
    AccountEventRecord,
    BankedStop,
    Device,
    RunPhaseRecord,
    TrailTombstoneRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_spend import bank_spend, sandbox_cycle_dirs
from promptpotter.infrastructure.store.layout import (
    DRAFTS_DIR,
    CycleLayout,
    inner_sandboxes_dir,
    read_sandbox_owner,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.domain.spend import Budget, CeilingMeter
    from promptpotter.infrastructure.store.stores import Stores


RUN_STOPPED = "run_stopped"


class TrailLine(NamedTuple):
    at: str
    what: str
    # Where a command was addressed (campaign/cycle, blank on the workspace); what an account event
    # or a consent line carried; a run's stop is its address, then why.
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
        RunPhaseRecord,
        TrailTombstoneRecord,
    )

    def __init__(self) -> None:
        self._found: list[TrailLine] = []
        self._banked: set[Subject] = set()

    def feed(self, record: AccountEventRecord | RunPhaseRecord | TrailTombstoneRecord) -> None:
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
                    TrailLine(record.timestamp, record.event, record.subject, record.user_id, "")
                )
            case TrailTombstoneRecord():
                self._banked.add(record.subject)
                self._found.extend(
                    TrailLine(stop.at, RUN_STOPPED, stop.subject, "", record.campaign_id)
                    for stop in record.stops
                )

    def value(self) -> tuple[tuple[TrailLine, ...], frozenset[Subject]]:
        return tuple(self._found), frozenset(self._banked)


# A roster is per PROCESS (`LedgerIndex._screened`): once a read names this one, every ledger that
# process indexes keeps its run stops. The admin bot, the `trail` verb and a delete pay that.
LedgerIndex.readers(_Trail)


def _where(campaign_id: str, cycle_id: str, inside: str) -> str:
    return f"{inside}>{campaign_id}/{cycle_id}" if inside else f"{campaign_id}/{cycle_id}"


def _cycle_lines(cycle_dir: Path, *, inside: str = "") -> list[TrailLine]:
    """``inside`` names the campaign whose inner sandbox holds the cycle."""
    campaign_id = cycle_dir.parent.parent.name
    where = _where(campaign_id, cycle_dir.name, inside)
    lines, _ = LedgerIndex.of(CycleLayout(cycle_dir).ledger).view(_Trail)
    return [
        line._replace(
            subject=f"{where}: {line.subject}" if line.subject else where,
            campaign_id=inside or campaign_id,
        )
        for line in lines
    ]


def _command_line(spelled: str, at: str, kind: str, actor: str) -> TrailLine:
    """A course's command is addressed as its cycle, in the sandbox chain above it; a draft's as
    one; a campaign's and the workspace's as nothing."""
    if (draft_id := draft_of(spelled)) is not None:
        return TrailLine(at, kind, f"{DRAFTS_DIR}/{draft_id}", actor, "")
    subject = subject_of(spelled)
    if subject is None or not subject.cycle_id:
        return TrailLine(at, kind, "", actor, "")
    inside = subject.inside[-1].campaign_id if subject.inside else ""
    where = _where(subject.campaign_id, subject.cycle_id, inside)
    owner = subject.inside[0].campaign_id if subject.inside else subject.campaign_id
    return TrailLine(at, kind, where, actor, owner)


def workspace_lines(stores: Stores) -> Iterator[TrailLine]:
    """Unordered: the account's consent trail and command log, the workspace's own ledgers, then the
    cycles of every inner sandbox it owns."""
    campaigns = stores.campaigns
    yield from (
        TrailLine(line.at, line.event, line.subject, line.user_id, "", line.device)
        for line in UserStore(campaigns.workspace).consent_trail()
    )
    for spelled, command in stores.commands.book().lines():
        yield _command_line(spelled, command.timestamp, command.kind, command.issued_by.id)
    lines, _ = LedgerIndex.of(CycleEventLog.workspace_path(campaigns.workspace)).view(_Trail)
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


def bank_before_removal(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    subject: Subject,
    metered: dict[CeilingMeter, Budget],
) -> None:
    """A campaign destroyer's one bank; a tombstone already standing is a crashed delete's, kept for the retry."""
    bank_spend(workspace=workspace, cycle_dirs=cycle_dirs, subject=subject, metered=metered)
    bank_trail(workspace=workspace, cycle_dirs=cycle_dirs, subject=subject)


def bank_trail(*, workspace: WorkspaceDir, cycle_dirs: list[Path], subject: Subject) -> None:
    """The run stops alone: what was commanded is the account log's, which no delete reaches."""
    stops = [
        BankedStop(at=line.at, subject=line.subject)
        for cycle_dir in cycle_dirs
        # A cycle outside the workspace is an inner sandbox's, and the campaign names its owner.
        for line in _cycle_lines(
            cycle_dir, inside="" if cycle_dir.is_relative_to(workspace) else subject.campaign_id
        )
    ]
    if not stops:
        return
    log = CycleEventLog.open_workspace(workspace)
    _, banked = LedgerIndex.of(log.path).view(_Trail)
    if subject not in banked:
        log.append(
            TrailTombstoneRecord(
                campaign_id=subject.campaign_id, cycle_id=subject.cycle_id, stops=tuple(stops)
            )
        )


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)
