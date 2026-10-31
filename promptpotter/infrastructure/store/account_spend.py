"""Summed from the ledgers, never `dashboard.json`: its spend is cumulative-from-seed and double-counts a fork."""

from __future__ import annotations

import sys
from collections.abc import Iterable
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
    UserSpend,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import CycleLayout, draft_dirs
from promptpotter.infrastructure.store.read_model import (
    HOLD_TRAIL,
    HeldSends,
    HoldTrailRecord,
    LedgerIndex,
    RecordClasses,
    held_tokens,
)
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def account_ledgers(campaigns: CampaignStore) -> list[Path]:
    """Resolved, never opened, so a read mints no dir; a draft's calls are the account's."""
    return [
        *campaigns.iter_cycle_ledgers(),
        *(
            ledger
            for draft_dir in draft_dirs(campaigns.workspace)
            if (ledger := CycleLayout(draft_dir).ledger).is_file()
        ),
        CycleEventLog.workspace_path(campaigns.workspace),
    ]


class UsageRow(NamedTuple):
    ts: float
    billed_usd: float | None
    rate_priced_usd: float | None
    """:func:`row_figures`' pair: both ``None`` is unpriced, and a replay is a bill of ``0.0``."""
    tokens: int
    model: str | None
    provider: str | None
    kind: str
    # ``None`` on a replay, which no key paid.
    key_source: KeySource | None


def iter_user_token_usage(*, ledgers: Iterable[Path], since: float, until: float) -> list[UsageRow]:
    return [
        row
        for ledger in ledgers
        for row in LedgerIndex.of(ledger, _SPEND_FOLDS).view(_Usage)
        if since <= row.ts < until
    ]


def row_figures(usage: TokenUsageRecord) -> tuple[float | None, float | None]:
    return (0.0, None) if usage.cached else (usage.cost_usd, usage.rate_priced_usd)


def _billed_of(usage: TokenUsageRecord) -> UserSpend:
    tokens = usage.input_tokens + usage.output_tokens
    billed, rate_priced = row_figures(usage)
    if billed is None and rate_priced is None:
        return UserSpend(0.0, tokens, tokens)
    return UserSpend(billed or 0.0, tokens, 0, rate_priced_usd=rate_priced or 0.0)


def _unreported_of(hold: SpendHoldRecord) -> PaidSpend:
    """Unpriced lands in the token residue: `$0.00` would report an unpriceable send as a free one."""
    tokens = held_tokens(hold)
    if hold.cost_usd is None:
        return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, tokens, 0.0, tokens))
    return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, hold.cost_usd, tokens))


class _Cost:
    """What a ledger's subject COST: every answered send, a nested run's included."""

    records: ClassVar[RecordClasses] = HOLD_TRAIL
    # A nested run's send, mirrored onto its root's ledger, is part of what this subject cost.
    counts_mirrored: ClassVar[bool] = True

    def __init__(self) -> None:
        self._total = NOTHING_PAID
        self._held = HeldSends()

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
        held = NOTHING_PAID
        for hold in self._held.open.values():
            held = held.plus(_unreported_of(hold))
        # Its bill already filed the tokens, as residue (:func:`_billed_of`); only the money is kept.
        for hold in self._held.unpriced.values():
            if hold.cost_usd is not None:
                money = UserSpend(0.0, 0, 0, hold.cost_usd, 0)
                held = held.plus(PaidSpend.of(hold.key_source, money))
        return self._total, held


class _Billed(_Cost):
    """What a ledger bills the ACCOUNT: its cost less the sends a root ledger already carries."""

    counts_mirrored: ClassVar[bool] = False


class _Tombstones:
    records: ClassVar[RecordClasses] = (SpendTombstoneRecord,)

    def __init__(self) -> None:
        self._by_campaign: dict[str, PaidSpend] = {}
        self._drafts = NOTHING_PAID
        self._metered: dict[tuple[str, CeilingMeter], Budget] = {}
        self._subjects: set[BankedSubject] = set()

    def feed(self, banked: SpendTombstoneRecord) -> None:
        self._subjects.add(banked.subject)
        spent = NOTHING_PAID
        for paid_by, part in banked.spent.items():
            spent = spent.plus(PaidSpend.of(paid_by, part.spend))
        # A draft's check-in calls are the account's alone: no campaign's ceiling counted them.
        if banked.draft_id:
            self._drafts = self._drafts.plus(spent)
            return
        for meter, counted in banked.metered.items():
            key = (banked.campaign_id, meter)
            self._metered[key] = self._metered.get(key, NOTHING_METERED).plus(counted)
        held = self._by_campaign.get(banked.campaign_id, NOTHING_PAID)
        self._by_campaign[banked.campaign_id] = held.plus(spent)

    def value(self) -> _Banked:
        return _Banked(
            dict(self._by_campaign), self._drafts, dict(self._metered), frozenset(self._subjects)
        )


class _Banked(NamedTuple):
    spent: dict[str, PaidSpend]
    drafts: PaidSpend
    metered: dict[tuple[str, CeilingMeter], Budget]
    subjects: frozenset[BankedSubject]


def _interned(value: object) -> str | None:
    return sys.intern(value) if isinstance(value, str) else None


class _Usage:
    records: ClassVar[RecordClasses] = (TokenUsageRecord,)

    def __init__(self) -> None:
        self._rows: list[UsageRow] = []
        self._held: tuple[UsageRow, ...] | None = None

    def feed(self, usage: TokenUsageRecord) -> None:
        ts = epoch_seconds(usage.timestamp)
        if ts is None:
            return
        billed, rate_priced = row_figures(usage)
        self._rows.append(
            UsageRow(
                ts=ts,
                billed_usd=billed,
                rate_priced_usd=rate_priced,
                tokens=usage.input_tokens + usage.output_tokens,
                model=_interned(usage.model),
                provider=_interned(usage.provider),
                kind=sys.intern(usage.kind),
                key_source=usage.key_source,
            )
        )
        self._held = None

    def value(self) -> tuple[UsageRow, ...]:
        if self._held is None:
            self._held = tuple(self._rows)
        return self._held


_SPEND_FOLDS = (_Cost, _Billed, _Tombstones, _Usage)


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


def sum_user_spend(*, ledgers: Iterable[Path]) -> PaidSpend:
    paths = list(ledgers)
    total = billed_spend(paths)
    for ledger in paths:
        banked = LedgerIndex.of(ledger, _SPEND_FOLDS).view(_Tombstones)
        total = total.plus(banked.drafts)
        for spend in banked.spent.values():
            total = total.plus(spend)
    return total


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
            **subject._asdict(),
            spent={
                paid_by: BankedSpend.of(part)
                for paid_by, part in by_payer.items()
                if part != ZERO_SPEND
            },
            metered=metered,
        )
    )
    return spent
