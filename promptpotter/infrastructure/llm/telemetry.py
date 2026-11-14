from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.run_records import (
    Bill,
    CallPrice,
    PricedKeyRecord,
    TokenUsageRecord,
)
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.pricing import compute_usd
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
from promptpotter.infrastructure.store.io import write_lock
from promptpotter.infrastructure.store.layout import CampaignLayout, cycle_ledgers
from promptpotter.shared.measurement_context import measured_pass
from promptpotter.shared.run_scope import UNBOUND, run_scope

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

_CURRENT_ROUND: ContextVar[int | None] = ContextVar("current_round", default=None)


def active_cycle_ledger() -> CycleEventLog | None:
    """Ask before OPENING one: a second handle is a second appender, and ``append`` is not atomic."""
    ledger = run_scope().ledger
    return None if ledger is UNBOUND else ledger


def bound_ledger(what: str) -> CycleEventLog | None:
    """The run scope's ledger; ``None`` where a probe render mutes it."""
    if (ledger := run_scope().ledger) is UNBOUND:
        raise RuntimeError(
            f"{what}: no cycle ledger is bound, so this record would reach no file. Bind one "
            "where the entry opens its run scope (`run_optimization`, `verb_ledger`)."
        )
    return ledger


def billing_ledger(what: str) -> CycleEventLog:
    ledger = bound_ledger(what)
    if ledger is None:
        raise RuntimeError(f"{what}: the ledger is muted here, and a muted pass sends nothing")
    return ledger


_FILED_AS: ContextVar[TokenUsageKind | None] = ContextVar("filed_as", default=None)


@contextmanager
def filed_as(kind: TokenUsageKind | None) -> Iterator[None]:
    if kind is None:
        yield
        return
    token = _FILED_AS.set(kind)
    try:
        yield
    finally:
        _FILED_AS.reset(token)


def filed_kind(kind: TokenUsageKind) -> TokenUsageKind:
    return _FILED_AS.get() or kind


@contextmanager
def at_round(round_num: int) -> Iterator[None]:
    """The ONE binder of the round: a run binds 0 for its whole span, each round its own inside it."""
    token = _CURRENT_ROUND.set(round_num)
    try:
        yield
    finally:
        _CURRENT_ROUND.reset(token)


def current_round() -> int | None:
    """``None`` outside any round, which only a record whose ``round`` is optional may carry."""
    return _CURRENT_ROUND.get()


def bound_round() -> int:
    round_num = _CURRENT_ROUND.get()
    if round_num is None:
        raise RuntimeError(
            "a record names the round it was measured in and none is bound: "
            "the entry driving this walk binds one first (`at_round`)"
        )
    return round_num


def append_record(*records: CycleRecord, ledger: CycleEventLog | None = None) -> int | None:
    """``None``: the bound ledger is muted. A failed append raises to the call site."""
    if ledger is None:
        ledger = bound_ledger(type(records[0]).__name__)
    return None if ledger is None else ledger.append(*records)


def rate_priced_usd(
    usage: TokenAccount,
    *,
    model: str | None,
    provider: str | None,
    cost_usd: float | None,
    recorded: float | None = None,
) -> float | None:
    if cost_usd is not None:
        return None
    if recorded is not None:
        return recorded
    return compute_usd(
        model,
        usage.input,
        usage.output,
        provider=provider,
        cache_read_tokens=usage.cache_read or 0,
        cache_write_tokens=usage.cache_write,
    )


def call_price(
    kind: TokenUsageKind,
    usage: TokenAccount,
    *,
    model: str | None,
    provider: str | None,
    cost_usd: float | None,
    recorded: float | None,
) -> CallPrice:
    """Priced once, here: readers sum the stamp, so a rate refresh rewrites nothing."""
    return CallPrice(
        kind=filed_kind(kind),
        model=model,
        provider=provider,
        cost_usd=cost_usd,
        rate_priced_usd=rate_priced_usd(
            usage, model=model, provider=provider, cost_usd=cost_usd, recorded=recorded
        ),
    )


def emit_replay_usage(
    *,
    node: str,
    usage: TokenAccount,
    duration_s: float,
    price: CallPrice,
    served_by: str | None,
) -> None:
    """Through the run's book, which meters a nested run's replay on its root's ledger too; a
    muted ledger files nothing."""
    if (ledger := bound_ledger(node)) is None:
        return
    book = run_scope().book
    record = token_usage_record(
        node=node,
        usage=usage,
        duration_s=duration_s,
        money=price,
        served_by=served_by,
        mirrored=False,
    )
    if book is None:
        append_record(record, ledger=ledger)
    else:
        book.file(record, ledger)


def token_usage_record(
    *,
    node: str,
    usage: TokenAccount,
    duration_s: float,
    money: Bill | CallPrice,
    served_by: str | None,
    mirrored: bool,
) -> TokenUsageRecord:
    return TokenUsageRecord(
        node=node,
        served_by=served_by,
        # Flat on purpose: every lifetime-spend read sums these counts off raw JSON.
        input_tokens=usage.input,
        output_tokens=usage.output,
        reasoning_tokens=usage.reasoning,
        cache_read_tokens=usage.cache_read or 0,
        cache_write_tokens=usage.cache_write,
        duration_s=float(duration_s),
        money=money,
        mirrored=mirrored,
        round=current_round(),
        role=cand.role if (cand := measured_pass()) else None,
    )


# The campaign dir whose cycles' ledgers name the cells (`ReplayFeed.cell_key`) and calls
# (`call:{reuse key}`) it priced: per CAMPAIGN, never per spend book or process.
_PRICED: ContextVar[Path | None] = ContextVar("priced", default=None)


def bind_priced(campaign_dir: Path) -> None:
    """The run's campaign: a cell or call any of its cycles priced replays free for it ever after."""
    _PRICED.set(campaign_dir)


def campaign_priced(key: str) -> bool:
    campaign = _PRICED.get()
    return campaign is not None and key in scan_ledger_priced_keys(cycle_ledgers(campaign))


def claim_campaign_price(key: str) -> bool:
    """Whether this reading of *key* is its campaign's first, which this call claims under the
    campaign's write lock, so two processes holding its cycles price it once; outside a campaign's
    run every reading is."""
    campaign = _PRICED.get()
    if campaign is None:
        return True
    with write_lock(CampaignLayout(campaign).manifest):
        if key in scan_ledger_priced_keys(cycle_ledgers(campaign)):
            return False
        append_record(PricedKeyRecord(priced_key=key))
    return True


def claim_call_price(reuse_key: str) -> bool:
    return claim_campaign_price(f"call:{reuse_key}")
