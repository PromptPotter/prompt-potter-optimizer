"""A campaign's spend is summed from its ledgers, never `dashboard.json`, whose spend is
cumulative-from-seed and double-counts a fork; the account's is summed from its charges."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import (
    BankedSpend,
    BankedSubject,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)
from promptpotter.domain.spend import (
    NOTHING_METERED,
    NOTHING_PAID,
    ZERO_SPEND,
    Budget,
    CeilingMeter,
    KeySource,
    PaidSpend,
    TokenUsageKind,
    UserSpend,
    bill_or_rate_usd,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    HOLD_TRAIL,
    HeldSends,
    HoldTrailRecord,
    LedgerIndex,
    RecordClasses,
    held_tokens,
)
from promptpotter.infrastructure.store.wallet_store import BilledCharge, ChargeBook, HeldCharge

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def _billed(cost_usd: float | None, rate_priced_usd: float | None, tokens: int) -> UserSpend:
    if cost_usd is None and rate_priced_usd is None:
        return UserSpend(0.0, tokens, tokens)
    return UserSpend(cost_usd or 0.0, tokens, 0, rate_priced_usd=rate_priced_usd or 0.0)


def _billed_of(usage: TokenUsageRecord) -> UserSpend:
    tokens = usage.input_tokens + usage.output_tokens
    if usage.cached:
        return _billed(0.0, None, tokens)
    return _billed(usage.cost_usd, usage.rate_priced_usd, tokens)


type _Hold = SpendHoldRecord | HeldCharge


def _unreported_of(hold: _Hold) -> PaidSpend:
    """Unpriced lands in the token residue: `$0.00` would report an unpriceable send as a free one."""
    tokens = held_tokens(hold)
    if hold.cost_usd is None:
        return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, tokens, 0.0, tokens))
    return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, hold.cost_usd, tokens))


def _still_held(open_holds: Iterable[_Hold], unpriced: Iterable[_Hold]) -> PaidSpend:
    total = NOTHING_PAID
    for hold in open_holds:
        total = total.plus(_unreported_of(hold))
    # Its bill already filed the tokens, as residue (:func:`_billed`); only the money is kept.
    for hold in unpriced:
        if hold.cost_usd is not None:
            money = UserSpend(0.0, 0, 0, hold.cost_usd, 0)
            total = total.plus(PaidSpend.of(hold.key_source, money))
    return total


class _Cost:
    """What a ledger's subject COST: every answered send, a nested run's included."""

    records: ClassVar[RecordClasses] = HOLD_TRAIL
    # A nested run's send, mirrored onto its root's ledger, is part of what this subject cost.
    counts_mirrored: ClassVar[bool] = True

    def __init__(self) -> None:
        self._total = NOTHING_PAID
        self._held: HeldSends[SpendHoldRecord] = HeldSends()

    def feed(self, record: HoldTrailRecord) -> None:
        # A replay names no payer (`key_source` is `None` exactly on one): it cost nothing.
        if (
            isinstance(record, TokenUsageRecord)
            and (paid_by := record.key_source) is not None
            and (self.counts_mirrored or not record.mirrored)
        ):
            self._total = self._total.plus(PaidSpend.of(paid_by, _billed_of(record)))
        self._held.track(record)

    def value(self) -> tuple[PaidSpend, PaidSpend]:
        return self._total, _still_held(self._held.open.values(), self._held.unpriced.values())


class _Billed(_Cost):
    """What a ledger's own sends cost: its cost less the sends a root ledger already carries."""

    counts_mirrored: ClassVar[bool] = False


class _Tombstones:
    records: ClassVar[RecordClasses] = (SpendTombstoneRecord,)

    def __init__(self) -> None:
        self._by_campaign: dict[str, PaidSpend] = {}
        self._metered: dict[tuple[str, CeilingMeter], Budget] = {}
        self._subjects: set[BankedSubject] = set()

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

    def value(self) -> _Banked:
        return _Banked(dict(self._by_campaign), dict(self._metered), frozenset(self._subjects))


class _Banked(NamedTuple):
    spent: dict[str, PaidSpend]
    metered: dict[tuple[str, CeilingMeter], Budget]
    subjects: frozenset[BankedSubject]


_SPEND_FOLDS = (_Cost, _Billed, _Tombstones)


def _unreported_once_stopped(ledger: Path, held: PaidSpend) -> PaidSpend:
    """While a producer is attached the account wallet already reserves the run's ceiling: counting a hold too reads it twice."""
    if held == NOTHING_PAID:
        return held
    run = derive_run_state(CycleLayout.of_ledger(ledger).cycle_dir)
    return NOTHING_PAID if run.producer.attached else held


def _summed(ledgers: Iterable[Path], fold: type[_Cost]) -> PaidSpend:
    total = NOTHING_PAID
    for ledger in ledgers:
        spent, held = LedgerIndex.of(ledger, _SPEND_FOLDS).view(fold)
        # Whether what is held is unreported moves with the run, not the file: asked per read.
        total = total.plus(spent).plus(_unreported_once_stopped(ledger, held))
    return total


def billed_spend(ledgers: Iterable[Path]) -> PaidSpend:
    return _summed(ledgers, _Billed)


def ledger_cost(ledgers: Iterable[Path]) -> PaidSpend:
    return _summed(ledgers, _Cost)


def campaign_spend(campaigns: CampaignStore, campaign_id: str) -> PaidSpend:
    """What the campaign cost, an L4 inner one too; the account's bill is :func:`billed_spend`'s."""
    own = ledger_cost(campaigns.campaign_cycle_ledgers(campaign_id))
    banked = _banked(campaigns.workspace).spent
    return own.plus(banked.get(campaign_id, NOTHING_PAID))


def banked_metered(workspace: WorkspaceDir, campaign_id: str, meter: CeilingMeter) -> Budget:
    """What the campaign's deleted cycles had metered against its ceiling."""
    return _banked(workspace).metered.get((campaign_id, meter), NOTHING_METERED)


def _banked(workspace: WorkspaceDir) -> _Banked:
    workspace_ledger = CycleEventLog.workspace_path(workspace)
    return LedgerIndex.of(workspace_ledger, _SPEND_FOLDS).view(_Tombstones)


def sandbox_cycle_dirs(sandbox: Path) -> list[Path]:
    """The sandbox tree is a sibling of the tenant tree, so no account-wide walk reaches it."""
    return sorted(sandbox.glob("*/campaigns/*/cycles/*")) if sandbox.is_dir() else []


class _Charges:
    records: ClassVar[RecordClasses] = (HeldCharge, BilledCharge)

    def __init__(self) -> None:
        self._billed = NOTHING_PAID
        self._held: HeldSends[HeldCharge] = HeldSends()

    def feed(self, charge: HeldCharge | BilledCharge) -> None:
        match charge:
            case HeldCharge():
                self._held.hold(charge.hold_id, charge)
            case BilledCharge():
                spent = _billed(charge.cost_usd, charge.rate_priced_usd, charge.tokens)
                self._billed = self._billed.plus(PaidSpend.of(charge.key_source, spent))
                priced = bill_or_rate_usd(charge.cost_usd, charge.rate_priced_usd) is not None
                self._held.close(charge.hold_id, priced=priced)

    def value(self) -> tuple[PaidSpend, tuple[HeldCharge, ...], tuple[HeldCharge, ...]]:
        return self._billed, tuple(self._held.open.values()), tuple(self._held.unpriced.values())


class UsageRow(NamedTuple):
    ts: float
    billed_usd: float | None
    rate_priced_usd: float | None
    tokens: int
    model: str | None
    provider: str | None
    kind: TokenUsageKind
    key_source: KeySource


def _interned(value: str | None) -> str | None:
    return None if value is None else sys.intern(value)


class _Usage:
    records: ClassVar[RecordClasses] = (BilledCharge,)

    def __init__(self) -> None:
        self._rows: list[UsageRow] = []
        self._held: tuple[UsageRow, ...] | None = None

    def feed(self, charge: BilledCharge) -> None:
        self._rows.append(
            UsageRow(
                ts=datetime.fromisoformat(charge.timestamp).timestamp(),
                billed_usd=charge.cost_usd,
                rate_priced_usd=charge.rate_priced_usd,
                tokens=charge.tokens,
                model=_interned(charge.model),
                provider=_interned(charge.provider),
                kind=charge.kind,
                key_source=charge.key_source,
            )
        )
        self._held = None

    def value(self) -> tuple[UsageRow, ...]:
        if self._held is None:
            self._held = tuple(self._rows)
        return self._held


_CHARGE_FOLDS = (_Charges, _Usage)


def account_usage(charges: ChargeBook, *, since: float, until: float) -> list[UsageRow]:
    rows = LedgerIndex.of(charges.path, _CHARGE_FOLDS).view(_Usage)
    return [row for row in rows if since <= row.ts < until]


def account_bill(charges: ChargeBook) -> PaidSpend:
    """Every send the account made, a deleted campaign's and an L4 sandbox's included: no delete reaches a charge."""
    billed, open_holds, unpriced = LedgerIndex.of(charges.path, _CHARGE_FOLDS).view(_Charges)
    attached: dict[str, bool] = {}

    def stopped(hold: HeldCharge) -> bool:
        # Whether what is held is unreported moves with the run, not the file: asked per read.
        if hold.ledger not in attached:
            cycle_dir = CycleLayout.of_ledger(charges.ledger_of(hold)).cycle_dir
            attached[hold.ledger] = derive_run_state(cycle_dir).producer.attached
        return not attached[hold.ledger]

    held = _still_held(
        (hold for hold in open_holds if stopped(hold)), (hold for hold in unpriced if stopped(hold))
    )
    return billed.plus(held)


def bank_spend(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    subject: BankedSubject,
    metered: dict[CeilingMeter, Budget],
) -> PaidSpend:
    """Destroyers only (archiving keeps the rows); a tombstone already standing is a crashed delete's, kept for the retry."""
    spent = billed_spend(CycleLayout(d).ledger for d in cycle_dirs)
    # A replayed cell bills nothing and still meters, on a search-incurred ceiling.
    if spent == NOTHING_PAID and all(m == NOTHING_METERED for m in metered.values()):
        return spent
    log = CycleEventLog.open_workspace(workspace)
    if subject in LedgerIndex.of(log.path, _SPEND_FOLDS).view(_Tombstones).subjects:
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
