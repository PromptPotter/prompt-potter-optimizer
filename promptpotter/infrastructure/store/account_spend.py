"""Summed from the ledgers, never `dashboard.json`: its spend is cumulative-from-seed and double-counts a fork."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from pydantic import Field

from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import (
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)
from promptpotter.domain.spend import bill_is_floor, calls_rate_priced
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    HeldSends,
    LedgerIndex,
    RecordClasses,
    held_tokens,
)
from promptpotter.shared.clock import epoch_seconds

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def account_ledgers(campaigns: CampaignStore) -> list[Path]:
    """The workspace path is resolved, not opened: a read must not mint the dir it reads."""
    return [*campaigns.iter_cycle_ledgers(), CycleEventLog.workspace_path(campaigns.workspace)]


class UsageRow(NamedTuple):
    ts: float
    billed_usd: float | None
    rate_priced_usd: float | None
    """:func:`row_figures`' pair: both ``None`` is unpriced, and a replay is a bill of ``0.0``."""
    tokens: int
    model: str | None
    provider: str | None
    kind: str


def iter_user_token_usage(*, ledgers: Iterable[Path], since: float, until: float) -> list[UsageRow]:
    return [
        row
        for ledger in ledgers
        for row in LedgerIndex.of(ledger, _SPEND_FOLDS).view(_Usage)
        if since <= row.ts < until
    ]


def row_figures(usage: TokenUsageRecord) -> tuple[float | None, float | None]:
    return (0.0, None) if usage.cached else (usage.cost_usd, usage.rate_priced_usd)


class UserSpend(NamedTuple):
    used_usd: float
    used_tokens: int
    unpriced_tokens: int
    unreported_usd: float = 0.0
    unreported_tokens: int = 0
    rate_priced_usd: float = 0.0

    @property
    def sent_usd(self) -> float:
        return self.used_usd + self.rate_priced_usd

    @property
    def at_most_usd(self) -> float:
        return self.sent_usd + self.unreported_usd

    @property
    def at_most_tokens(self) -> int:
        return self.used_tokens + self.unreported_tokens

    @property
    def sends_unreported(self) -> bool:
        return self.unreported_usd > 0

    def plus(self, other: UserSpend) -> UserSpend:
        return UserSpend(
            self.used_usd + other.used_usd,
            self.used_tokens + other.used_tokens,
            self.unpriced_tokens + other.unpriced_tokens,
            self.unreported_usd + other.unreported_usd,
            self.unreported_tokens + other.unreported_tokens,
            self.rate_priced_usd + other.rate_priced_usd,
        )


ZERO_SPEND = UserSpend(0.0, 0, 0)


class LifetimeSpend(StrictModel):
    """What an account, or one campaign's share of it, was billed over its whole life."""

    billed_usd: float = Field(
        description="What the providers REPORTED they billed, over every cycle, fork and "
        "forwarded inner run, plus what a deleted cycle banked. Never an estimate: a call priced "
        "off our rate table is `rate_priced_usd`, a send whose bill never came `unreported_usd`."
    )
    rate_priced_usd: float = Field(
        description="What our rate table prices the calls no provider reported a cost for. Not "
        "spent — an estimate, shown beside `billed_usd` and never added into it. It binds the "
        "ceiling beside `billed_usd`."
    )
    calls_rate_priced: bool = Field(
        description="Some call was priced at our rate, so there is a `rate_priced_usd` to show "
        "beside the bill."
    )
    bill_is_floor: bool = Field(
        description="`billed_usd` and `rate_priced_usd` understate, by what `unpriced_tokens` "
        "cost; the token ceiling is then the binding one."
    )
    unpriced_tokens: int = Field(
        description="Tokens no provider billed and no rate on file prices."
    )
    sends_unreported: bool = Field(
        description="Some send ended with no bill, so up to `unreported_usd` more may have left "
        "the account than `billed_usd` says."
    )
    unreported_usd: float = Field(
        description="The most that sends which ended with no bill (cancelled, timed out, killed "
        "with a run) may have cost, at the bounds they were admitted on. Not spent — unknown. It "
        "binds the ceiling beside `billed_usd`."
    )

    @classmethod
    def of(cls, spent: UserSpend) -> LifetimeSpend:
        return cls(
            billed_usd=round(spent.used_usd, 6),
            rate_priced_usd=round(spent.rate_priced_usd, 6),
            calls_rate_priced=calls_rate_priced(spent.rate_priced_usd),
            bill_is_floor=bill_is_floor(spent.unpriced_tokens),
            unpriced_tokens=spent.unpriced_tokens,
            sends_unreported=spent.sends_unreported,
            unreported_usd=round(spent.unreported_usd, 6),
        )


def _billed_of(usage: TokenUsageRecord) -> UserSpend:
    tokens = usage.input_tokens + usage.output_tokens
    billed, rate_priced = row_figures(usage)
    if billed is None and rate_priced is None:
        return UserSpend(0.0, tokens, tokens)
    return UserSpend(billed or 0.0, tokens, 0, rate_priced_usd=rate_priced or 0.0)


def _unreported_of(hold: dict[str, Any]) -> UserSpend:
    """Unpriced lands in the token residue: `$0.00` would report an unpriceable send as a free one."""
    tokens = held_tokens(hold)
    usd = hold.get("cost_usd")
    if not isinstance(usd, int | float):
        return UserSpend(0.0, 0, tokens, 0.0, tokens)
    return UserSpend(0.0, 0, 0, float(usd), tokens)


class _Billed:
    records: ClassVar[RecordClasses] = (SpendHoldRecord, TokenUsageRecord)

    def __init__(self) -> None:
        self._total = ZERO_SPEND
        self._held = HeldSends()

    def feed(self, offset: int, record: SpendHoldRecord | TokenUsageRecord) -> None:
        if isinstance(record, TokenUsageRecord) and not (record.cached or record.mirrored):
            self._total = self._total.plus(_billed_of(record))
        # `HeldSends` is the spend book's too, which folds the lines as written.
        self._held.track(record.model_dump(mode="json"))

    def value(self) -> tuple[UserSpend, UserSpend]:
        held = ZERO_SPEND
        for rec in self._held.open.values():
            held = held.plus(_unreported_of(rec))
        # Its bill already filed the tokens, as residue (:func:`_billed_of`); only the money is kept.
        for bound in self._held.unpriced.values():
            if isinstance(bound, int | float):
                held = held.plus(UserSpend(0.0, 0, 0, float(bound), 0))
        return self._total, held


class _Tombstones:
    records: ClassVar[RecordClasses] = (SpendTombstoneRecord,)

    def __init__(self) -> None:
        self._by_campaign: dict[str, UserSpend] = {}
        self._subjects: set[tuple[str, str]] = set()

    def feed(self, offset: int, banked: SpendTombstoneRecord) -> None:
        held = self._by_campaign.get(banked.campaign_id, ZERO_SPEND)
        self._by_campaign[banked.campaign_id] = held.plus(
            UserSpend(
                banked.used_usd,
                banked.used_tokens,
                banked.unpriced_tokens,
                banked.unreported_usd,
                banked.unreported_tokens,
                banked.rate_priced_usd,
            )
        )
        self._subjects.add((banked.campaign_id, banked.cycle_id))

    def value(self) -> tuple[dict[str, UserSpend], frozenset[tuple[str, str]]]:
        return dict(self._by_campaign), frozenset(self._subjects)


def _interned(value: object) -> str | None:
    return sys.intern(value) if isinstance(value, str) else None


class _Usage:
    records: ClassVar[RecordClasses] = (TokenUsageRecord,)

    def __init__(self) -> None:
        self._rows: list[UsageRow] = []
        self._held: tuple[UsageRow, ...] | None = None

    def feed(self, offset: int, usage: TokenUsageRecord) -> None:
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
            )
        )
        self._held = None

    def value(self) -> tuple[UsageRow, ...]:
        if self._held is None:
            self._held = tuple(self._rows)
        return self._held


_SPEND_FOLDS = (_Billed, _Tombstones, _Usage)


def _unreported_once_stopped(ledger: Path, held: UserSpend) -> UserSpend:
    """While a producer is attached the account wallet already reserves the run's ceiling: counting a hold too reads it twice."""
    if held == ZERO_SPEND:
        return held
    run = derive_run_state(CycleLayout.of_ledger(ledger).cycle_dir)
    return ZERO_SPEND if run.producer.attached else held


def billed_spend(ledgers: Iterable[Path]) -> UserSpend:
    total = ZERO_SPEND
    for ledger in ledgers:
        billed, held = LedgerIndex.of(ledger, _SPEND_FOLDS).view(_Billed)
        # Whether what is held is unreported moves with the run, not the file: asked per read.
        total = total.plus(billed).plus(_unreported_once_stopped(ledger, held))
    return total


def history_spend(cycle_dir: CycleDir) -> UserSpend:
    """A hold in the prefix is its owner's: a bill past the cut may close it."""
    *prefix, own = ledger_chain(cycle_dir)
    total = billed_spend([own.path])
    for span in prefix:
        billed, _ = LedgerIndex.of(span.path, _SPEND_FOLDS).view(_Billed, span.until)
        total = total.plus(billed)
    return total


def campaign_spend(campaigns: CampaignStore, campaign_id: str) -> UserSpend:
    own = billed_spend(campaigns.campaign_cycle_ledgers(campaign_id))
    workspace_ledger = CycleEventLog.workspace_path(campaigns.workspace)
    banked, _ = LedgerIndex.of(workspace_ledger, _SPEND_FOLDS).view(_Tombstones)
    return own.plus(banked.get(campaign_id, ZERO_SPEND))


def sandbox_cycle_dirs(sandbox: Path) -> list[Path]:
    """The sandbox tree is a sibling of the tenant tree, so no account-wide walk reaches it."""
    return sorted(sandbox.glob("*/campaigns/*/cycles/*")) if sandbox.is_dir() else []


def sum_user_spend(*, ledgers: Iterable[Path]) -> UserSpend:
    paths = list(ledgers)
    total = billed_spend(paths)
    for ledger in paths:
        banked, _ = LedgerIndex.of(ledger, _SPEND_FOLDS).view(_Tombstones)
        for spend in banked.values():
            total = total.plus(spend)
    return total


def bank_spend(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    campaign_id: str,
    cycle_id: str = "",
) -> UserSpend:
    """Destroyers only (archiving keeps the rows); a tombstone already standing is a crashed delete's, kept for the retry."""
    spent = billed_spend(CycleLayout(d).ledger for d in cycle_dirs)
    if spent == ZERO_SPEND:
        return spent
    log = CycleEventLog.open_workspace(workspace)
    if _already_banked(log.path, campaign_id=campaign_id, cycle_id=cycle_id):
        return spent
    log.append(
        SpendTombstoneRecord(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            used_usd=spent.used_usd,
            rate_priced_usd=spent.rate_priced_usd,
            used_tokens=spent.used_tokens,
            unpriced_tokens=spent.unpriced_tokens,
            unreported_usd=spent.unreported_usd,
            unreported_tokens=spent.unreported_tokens,
        )
    )
    return spent


def _already_banked(workspace_ledger: Path, *, campaign_id: str, cycle_id: str) -> bool:
    _, subjects = LedgerIndex.of(workspace_ledger, _SPEND_FOLDS).view(_Tombstones)
    return (campaign_id, cycle_id) in subjects


__all__ = [
    "ZERO_SPEND",
    "LifetimeSpend",
    "UsageRow",
    "UserSpend",
    "account_ledgers",
    "bank_spend",
    "billed_spend",
    "campaign_spend",
    "history_spend",
    "iter_user_token_usage",
    "row_figures",
    "sandbox_cycle_dirs",
    "sum_user_spend",
]
