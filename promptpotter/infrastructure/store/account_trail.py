"""What was done in one account, read off its consent trail, its command log and its ledgers;
`account_spend.py` reads what it cost."""

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
    BankedStop,
    CommandSubject,
    Device,
    RunPhaseRecord,
    TrailTombstoneRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_spend import bank_spend, sandbox_cycle_dirs
from promptpotter.infrastructure.store.command_log import CommandLog
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
    from promptpotter.domain.cycle_paths import Subject
    from promptpotter.domain.spend import Budget, CeilingMeter
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


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
# process indexes keeps its stops. The admin bot, the `trail` verb and a delete pay that.
_TRAIL_FOLDS = (_Trail,)


def _cycle_lines(cycle_dir: Path, *, inside: str = "") -> list[TrailLine]:
    """``inside`` names the campaign whose inner sandbox holds the cycle."""
    campaign_id = cycle_dir.parent.parent.name
    address = f"{campaign_id}/{cycle_dir.name}"
    where = f"{inside}>{address}" if inside else address
    lines, _ = LedgerIndex.of(CycleLayout(cycle_dir).ledger, _TRAIL_FOLDS).view(_Trail)
    return [
        line._replace(
            subject=f"{where}: {line.subject}" if line.subject else where,
            campaign_id=inside or campaign_id,
        )
        for line in lines
    ]


def _where(spelled: str) -> tuple[str, str]:
    """A filed subject as the trail spells it, and the campaign it is the account's line of: an
    inner course under the campaign whose sandbox holds it (``owner>campaign/cycle``), a draft as
    ``drafts/<id>``, the workspace blank."""
    if (draft_id := CommandSubject.draft_of(spelled)) is not None:
        return f"{DRAFTS_DIR}/{draft_id}", ""
    subject = CommandSubject.subject_of(spelled)
    if subject is None:
        return "", ""
    address = (
        f"{subject.campaign_id}/{subject.cycle_id}" if subject.cycle_id else subject.campaign_id
    )
    if not subject.inside:
        return address, subject.campaign_id
    owner = subject.inside[-1].campaign_id
    return f"{owner}>{address}", owner


def _command_lines(workspace: WorkspaceDir) -> Iterator[TrailLine]:
    """Every command the account's log filed, at the address it was filed on — a draft's at the
    cycle its mint landed it on; no delete reaches the log, so a removed campaign's or sandbox's
    commands are read here too."""
    book = CommandLog(workspace).book()
    for state in book.commands.values():
        filed = state.filed
        where, campaign_id = _where(book.landed.get(filed.subject, filed.subject))
        yield TrailLine(filed.timestamp, filed.kind, where, filed.issued_by.id, campaign_id)


def workspace_lines(campaigns: CampaignStore) -> Iterator[TrailLine]:
    """Unordered: the account's consent trail, its command log, the workspace's own ledgers,
    then the cycles of every inner sandbox it owns."""
    yield from (
        TrailLine(line.at, line.event, line.subject, line.user_id, "", line.device)
        for line in UserStore(campaigns.workspace).consent_trail()
    )
    lines, _ = LedgerIndex.of(CycleEventLog.workspace_path(campaigns.workspace), _TRAIL_FOLDS).view(
        _Trail
    )
    yield from lines
    yield from _command_lines(campaigns.workspace)
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
    """A campaign destroyer's one bank: what it cost, and how its runs stopped. What was asked of
    it stays on the account's command log, which no delete reaches. A tombstone already standing
    is a crashed delete's, kept for the retry."""
    bank_spend(workspace=workspace, cycle_dirs=cycle_dirs, subject=subject, metered=metered)
    # A cycle outside the workspace is an inner sandbox's, and the campaign names its owner.
    stops = [
        line
        for cycle_dir in cycle_dirs
        for line in _cycle_lines(
            cycle_dir,
            inside="" if cycle_dir.is_relative_to(workspace) else subject.campaign_id,
        )
    ]
    if not stops:
        return
    log = CycleEventLog.open_workspace(workspace)
    _, banked = LedgerIndex.of(log.path, _TRAIL_FOLDS).view(_Trail)
    if subject not in banked:
        log.append(
            TrailTombstoneRecord(
                campaign_id=subject.campaign_id,
                cycle_id=subject.cycle_id,
                stops=tuple(BankedStop(at=line.at, subject=line.subject) for line in stops),
            )
        )


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)
