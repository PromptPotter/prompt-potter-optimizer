"""A campaign's spend is summed from its ledgers, never `dashboard.json`, whose spend is
cumulative-from-seed and double-counts a fork; the account's is summed from its charges."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.cycle_paths import Subject, WorkspaceDir
from promptpotter.domain.run_records import (
    BankedSpend,
    Bill,
    Hold,
    SpendTombstoneRecord,
)
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
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    RecordClasses,
    Spent,
    money_on,
    still_held,
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
        self._subjects.add(banked.subject)
        spent = NOTHING_PAID
        for paid_by, part in banked.spent.items():
            spent = spent.plus(PaidSpend.of(paid_by, part.spend))
        for meter, counted in banked.metered.items():
            key = (banked.campaign_id, meter)
            self._metered[key] = self._metered.get(key, NOTHING_METERED).plus(counted)
        held = self._by_campaign.get(banked.campaign_id, NOTHING_PAID)
        self._by_campaign[banked.campaign_id] = held.plus(spent)

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


LedgerIndex.readers(_Tombstones)


def _summed(ledgers: Iterable[Path]) -> Spent:
    total = Spent(NOTHING_PAID, NOTHING_PAID)
    for ledger in ledgers:
        total = total.plus(money_on(ledger).spent)
    return total


def billed_spend(ledgers: Iterable[Path]) -> PaidSpend:
    return _summed(ledgers).billed


def ledger_cost(ledgers: Iterable[Path]) -> PaidSpend:
    return _summed(ledgers).cost


def unreported_under(charges: ChargeBook, root: Path) -> PaidSpend:
    """What the account's sends that landed under *root* (a ledger, or a campaign's dir) left
    unreported (`Money.unreported_holds`): a deleted ledger's too."""
    under = PurePosixPath(charges.held_on(root))

    def on(hold: Hold) -> bool:
        return hold.ledger is not None and PurePosixPath(hold.ledger).is_relative_to(under)

    whole, unpriced = money_on(charges.path).unreported_holds()
    return still_held(filter(on, whole), filter(on, unpriced))


def campaign_spend(campaigns: CampaignStore, charges: ChargeBook, campaign_id: str) -> PaidSpend:
    """What the campaign cost, an L4 inner one too; the account's bill is :func:`account_bill`'s."""
    bank = workspace_bank(campaigns.workspace)
    root = campaigns.campaign_root_dir(campaign_id)
    spent = bank.spent.get(campaign_id, NOTHING_PAID).plus(unreported_under(charges, root))
    if bank.deleted(campaign_id):
        return spent
    return ledger_cost(campaigns.campaign_cycle_ledgers(campaign_id)).plus(spent)


def workspace_bank(workspace: WorkspaceDir) -> WorkspaceBank:
    workspace_ledger = CycleEventLog.workspace_path(workspace)
    return LedgerIndex.of(workspace_ledger).view(_Tombstones)


def sandbox_cycle_dirs(sandbox: Path) -> list[Path]:
    """The sandbox tree is a sibling of the tenant tree, so no account-wide walk reaches it."""
    return sorted(sandbox.glob("*/campaigns/*/cycles/*")) if sandbox.is_dir() else []


def account_usage(charges: ChargeBook, *, since: float, until: float) -> list[Bill]:
    return [
        bill
        for bill in money_on(charges.path).bills
        if since <= datetime.fromisoformat(bill.timestamp).timestamp() < until
    ]


def account_bill(charges: ChargeBook) -> PaidSpend:
    """Every send the account made, a deleted campaign's and an L4 sandbox's included: no delete reaches a charge."""
    money = money_on(charges.path)
    return money.spent.billed.plus(money.unreported)


def bank_spend(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    subject: Subject,
    metered: dict[CeilingMeter, Budget],
) -> PaidSpend:
    """Destroyers only (archiving keeps the rows); a tombstone already standing is a crashed delete's, kept for the retry."""
    spent = billed_spend(CycleLayout(d).ledger for d in cycle_dirs)
    # A replayed cell bills nothing and still meters, on a search-incurred ceiling.
    if spent == NOTHING_PAID and all(m == NOTHING_METERED for m in metered.values()):
        return spent
    log = CycleEventLog.open_workspace(workspace)
    if subject in LedgerIndex.of(log.path).view(_Tombstones).subjects:
        return spent
    by_payer: dict[KeySource, UserSpend] = {"host": spent.host, "user": spent.user}
    log.append(
        SpendTombstoneRecord(
            campaign_id=subject.campaign_id,
            cycle_id=subject.cycle_id,
            spent={
                paid_by: BankedSpend.of(part)
                for paid_by, part in by_payer.items()
                if part != ZERO_SPEND
            },
            metered=metered,
        )
    )
    return spent
