"""What was done in one workspace, read off its consent trail, command log and ledgers; `account_spend.py` reads what it cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple, assert_never

from promptpotter.domain.command_events import draft_of, subject_of
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, Subject, WorkspaceDir
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.records.run_records import (
    AccountEventRecord,
    BankedStop,
    Device,
    Ended,
    Paused,
    RunPhaseRecord,
    TrailTombstoneRecord,
    declared_stop,
)
from promptpotter.domain.spend import Budget, CeilingMeter
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_spend import bank_spend
from promptpotter.infrastructure.store.io import DRAFTS_DIR
from promptpotter.infrastructure.store.layout import (
    inner_sandboxes_dir,
    read_sandbox_owner,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
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


class _TrailView(NamedTuple):
    lines: tuple[TrailLine, ...]
    # The ledger's own abnormal stops, each declared state at its stamp: its cycle's to address.
    stops: tuple[tuple[Paused | Ended, str], ...]
    banked: frozenset[Subject]


class _Trail:
    records: ClassVar[RecordClasses] = (
        AccountEventRecord,
        RunPhaseRecord,
        TrailTombstoneRecord,
    )

    def __init__(self) -> None:
        self._found: list[TrailLine] = []
        self._stops: list[tuple[Paused | Ended, str]] = []
        self._banked: set[Subject] = set()

    def feed(self, record: AccountEventRecord | RunPhaseRecord | TrailTombstoneRecord) -> None:
        match record:
            case RunPhaseRecord(state=Paused() | Ended() as stop) if (
                stop_reason_outcome(stop.stop_reason) is not StopOutcome.SUCCESS
            ):
                self._stops.append((stop, record.timestamp))
            case AccountEventRecord():
                self._found.append(
                    TrailLine(record.timestamp, record.event, record.subject, record.user_id, "")
                )
            case TrailTombstoneRecord():
                self._banked.add(record.subject)
                self._found.extend(_stop_line(record.inside, stop) for stop in record.stops)
            case RunPhaseRecord():
                pass
            case _:
                assert_never(record)

    def value(self) -> _TrailView:
        return _TrailView(tuple(self._found), tuple(self._stops), frozenset(self._banked))


# A roster is per PROCESS (`LedgerIndex._screened`): once a read names this one, every ledger that
# process indexes keeps its run stops. The admin bot, the `trail` verb and a delete pay that.
LedgerIndex.readers(_Trail)


def _where(subject: Subject) -> str:
    """A cycle as the trail names it: under the campaign whose inner sandbox holds it."""
    where = f"{subject.campaign_id}/{subject.cycle_id}"
    return f"{subject.inside[-1].campaign_id}>{where}" if subject.inside else where


def _stop_line(inside: CyclePath, stop: BankedStop) -> TrailLine:
    subject = Subject.of((*inside, stop.hop))
    said = f"{_where(subject)}: {declared_stop(stop.state, stop.at).said}"
    return TrailLine(stop.at, RUN_STOPPED, said, "", subject.root_campaign_id)


def _cycle_stops(hop: CycleHop, ledger: Path) -> list[BankedStop]:
    stops = LedgerIndex.of(ledger).view(_Trail).stops
    return [BankedStop(hop=hop, state=state, at=at) for state, at in stops]


def _command_line(spelled: str, at: str, kind: str, actor: str) -> TrailLine:
    """A cycle's command is addressed as its cycle, in the sandbox chain above it; a draft's as
    one; a campaign's and the workspace's as nothing."""
    if (draft_id := draft_of(spelled)) is not None:
        return TrailLine(at, kind, f"{DRAFTS_DIR}/{draft_id}", actor, "")
    subject = subject_of(spelled)
    if subject is None or not subject.cycle_id:
        return TrailLine(at, kind, "", actor, "")
    return TrailLine(at, kind, _where(subject), actor, subject.root_campaign_id)


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
    yield from LedgerIndex.of(CycleEventLog.workspace_path(campaigns.workspace)).view(_Trail).lines
    for campaign_id in campaigns.list_campaign_ids():
        for hop in campaigns.campaign_cycle_hops(campaign_id):
            yield from (_stop_line((), stop) for stop in _cycle_stops(hop, campaigns.ledger(hop)))
    inner = inner_sandboxes_dir(campaigns.workspace.parent)
    for sandbox in sorted(inner.iterdir()) if inner.is_dir() else ():
        owner = read_sandbox_owner(sandbox) if sandbox.is_dir() else None
        if owner is not None and owner.tenant_id == campaigns.workspace.name:
            inner_store = campaigns.in_sandbox(sandbox)
            inside = (owner.hop,)
            for hop in inner_store.cycle_hops():
                stops = _cycle_stops(hop, inner_store.ledger(hop))
                yield from (_stop_line(inside, stop) for stop in stops)


def bank_before_removal(
    *,
    workspace: WorkspaceDir,
    ledgers: dict[CycleHop, Path],
    subject: Subject,
    metered: dict[CeilingMeter, Budget],
) -> None:
    """A campaign destroyer's one bank, filed at *subject*'s address, whose sandbox chain holds
    the *ledgers*; a tombstone already standing is a crashed delete's, kept for the retry."""
    bank_spend(workspace=workspace, ledgers=[*ledgers.values()], subject=subject, metered=metered)
    stops = [stop for hop, ledger in ledgers.items() for stop in _cycle_stops(hop, ledger)]
    if not stops:
        return
    log = CycleEventLog.open_workspace(workspace)
    if subject not in LedgerIndex.of(log.path).view(_Trail).banked:
        log.append(
            TrailTombstoneRecord(
                campaign_id=subject.campaign_id,
                cycle_id=subject.cycle_id,
                inside=subject.inside,
                stops=tuple(stops),
            )
        )


def in_time_order(lines: Iterable[TrailLine]) -> list[TrailLine]:
    """A stamp that will not parse sorts first rather than costing the read."""
    return sorted(lines, key=lambda line: epoch_seconds(line.at) or 0.0)
