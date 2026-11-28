"""A campaign's spend is summed from its ledgers, never `dashboard.json`, whose spend is
cumulative-from-seed and double-counts a fork; the account's is summed from its charges."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.cycle_paths import Subject, WorkspaceDir
from promptpotter.domain.records.money import Bill, Hold
from promptpotter.domain.records.workspace import BankedSpend, SpendTombstoneRecord
from promptpotter.domain.spend import (
    NOTHING_METERED,
    NOTHING_PAID,
    ZERO_SPEND,
    Budget,
    CeilingMeter,
    KeySource,
    PaidSpend,
    UserSpend,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.money_fold import bill_paid, money_on, still_held
from promptpotter.infrastructure.store.read_model import (
    CAMPAIGN,
    CYCLE,
    LedgerIndex,
    RecordClasses,
    enclosing,
)
from promptpotter.infrastructure.store.wallet_store import ChargeBook

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


class _Tombstones:
    records: ClassVar[RecordClasses] = (SpendTombstoneRecord,)

    def __init__(self) -> None:
        self._by_campaign: dict[str, PaidSpend] = {}
        self._metered: dict[tuple[str, CeilingMeter], Budget] = {}
        self._subjects: set[Subject] = set()

    def feed(self, banked: SpendTombstoneRecord) -> None:
        # Filed under the top-level campaign: a sandbox's costs its owner's.
        owner = banked.subject.root_campaign_id
        self._subjects.add(banked.subject)
        spent = NOTHING_PAID
        for paid_by, part in banked.spent.items():
            spent = spent.plus(PaidSpend.of(paid_by, part.spend))
        for meter, counted in banked.metered.items():
            key = (owner, meter)
            self._metered[key] = self._metered.get(key, NOTHING_METERED).plus(counted)
        self._by_campaign[owner] = self._by_campaign.get(owner, NOTHING_PAID).plus(spent)

    def value(self) -> WorkspaceBank:
        return WorkspaceBank(
            dict(self._by_campaign), dict(self._metered), frozenset(self._subjects)
        )


class WorkspaceBank(NamedTuple):
    spent: dict[str, PaidSpend]
    metered: dict[tuple[str, CeilingMeter], Budget]
    subjects: frozenset[Subject]

    def deleted(self, campaign_id: str) -> bool:
        """Its own tombstone IS the delete's commit: rows a torn delete left count for nothing."""
        return Subject(campaign_id, "") in self.subjects


class _Bills:
    """An account's bills, for the activity windows and a coupon's (`account_bill`'s *since*);
    opened by those reads alone, never by a send."""

    records: ClassVar[RecordClasses] = (Bill,)

    def __init__(self) -> None:
        self._bills: list[Bill] = []
        # Per *since*: how many bills its sum has passed, and that sum.
        self._since: dict[str, tuple[int, PaidSpend]] = {}

    def feed(self, bill: Bill) -> None:
        self._bills.append(bill)

    def value(self) -> _Bills:
        return self

    def window(self, since: float, until: float) -> list[Bill]:
        return [
            bill
            for bill in self._bills
            if since <= datetime.fromisoformat(bill.timestamp).timestamp() < until
        ]

    def billed_since(self, since: str) -> PaidSpend:
        """Summed on from the bill its last ask stood at, in filing order: a read under the
        admission gate costs the bills filed since, never the book."""
        start = datetime.fromisoformat(since)
        at, billed = self._since.get(since, (0, NOTHING_PAID))
        fresh = self._bills[at:]
        for bill in fresh:
            if datetime.fromisoformat(bill.timestamp) >= start:
                billed = billed.plus(bill_paid(bill))
        self._since[since] = (at + len(fresh), billed)
        return billed


LedgerIndex.readers(_Tombstones, _Bills)


def billed_spend(ledgers: Iterable[Path]) -> PaidSpend:
    """What the sends filed on *ledgers* billed, each filed once, on the ledger of its run."""
    total = NOTHING_PAID
    for ledger in ledgers:
        total = total.plus(money_on(ledger).spent)
    return total


def unreported_under(charges: ChargeBook, root: Path) -> PaidSpend:
    """What the account's sends that landed under *root* (a ledger, or a campaign's dir) left
    unreported (`Money.unreported_holds`): a deleted ledger's too."""
    under = PurePosixPath(charges.held_on(root))

    def on(hold: Hold) -> bool:
        return hold.ledger is not None and PurePosixPath(hold.ledger).is_relative_to(under)

    whole, unpriced = money_on(charges.path).unreported_holds()
    return still_held(filter(on, whole), filter(on, unpriced))


def cycle_unreported(charges: ChargeBook, cycle_dir: Path) -> UserSpend:
    """What a cycle's sends left unreported, its L4 inner cycles' too (`enclosing`)."""
    spent = NOTHING_PAID
    for span in enclosing(CYCLE).spans(cycle_dir):
        spent = spent.plus(unreported_under(charges, span.path))
    return spent.total


def campaign_spend(campaigns: CampaignStore, charges: ChargeBook, campaign_id: str) -> PaidSpend:
    """What the campaign cost, its cycles' L4 inner runs included (`enclosing`); the account's
    bill is :func:`account_bill`'s."""
    bank = workspace_bank(campaigns.workspace)
    root = campaigns.campaign_root_dir(campaign_id)
    spent = bank.spent.get(campaign_id, NOTHING_PAID).plus(unreported_under(charges, root))
    if bank.deleted(campaign_id):
        return spent
    spans = enclosing(CAMPAIGN).spans(root)
    for span in spans:
        if span.enclosed:
            spent = spent.plus(unreported_under(charges, span.path))
    return billed_spend(span.path for span in spans).plus(spent)


def workspace_bank(workspace: WorkspaceDir) -> WorkspaceBank:
    workspace_ledger = CycleEventLog.workspace_path(workspace)
    return LedgerIndex.of(workspace_ledger).view(_Tombstones)


def account_usage(charges: ChargeBook, *, since: float, until: float) -> list[Bill]:
    return LedgerIndex.of(charges.path).view(_Bills).window(since, until)


def account_bill(charges: ChargeBook, *, since: str | None = None) -> PaidSpend:
    """Every send the account made, a deleted campaign's and an L4 sandbox's included: no delete
    reaches a charge. *since* (a coupon's `CouponGrant.granted_at`) windows it: each bill at or
    after it, and each unreported send held at or after it."""
    money = money_on(charges.path)
    if since is None:
        return money.spent.plus(money.unreported)
    start = datetime.fromisoformat(since)

    def after(line: Hold) -> bool:
        return datetime.fromisoformat(line.timestamp) >= start

    billed = LedgerIndex.of(charges.path).view(_Bills).billed_since(since)
    whole, unpriced = money.unreported_holds()
    return billed.plus(still_held(filter(after, whole), filter(after, unpriced)))


def bank_spend(
    *,
    workspace: WorkspaceDir,
    ledgers: list[Path],
    subject: Subject,
    metered: dict[CeilingMeter, Budget],
) -> PaidSpend:
    """Destroyers only (archiving keeps the rows); a tombstone already standing is a crashed delete's, kept for the retry."""
    spent = billed_spend(ledgers)
    # Written at nothing spent too: the tombstone is the delete's commit (`WorkspaceBank.deleted`).
    log = CycleEventLog.open_workspace(workspace)
    if subject in LedgerIndex.of(log.path).view(_Tombstones).subjects:
        return spent
    by_payer: dict[KeySource, UserSpend] = {"host": spent.host, "user": spent.user}
    log.append(
        SpendTombstoneRecord(
            campaign_id=subject.campaign_id,
            cycle_id=subject.cycle_id,
            inside=subject.inside,
            spent={
                paid_by: BankedSpend.of(part)
                for paid_by, part in by_payer.items()
                if part != ZERO_SPEND
            },
            metered=metered,
        )
    )
    return spent
