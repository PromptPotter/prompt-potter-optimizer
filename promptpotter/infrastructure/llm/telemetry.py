from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import ErrorRecord, StopReason
from promptpotter.domain.run_records import (
    BackendWarningRecord,
    Bill,
    CallPrice,
    CycleRecord,
    PricedKeyRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RoundWarningKind,
    RoundWarningRecord,
    RoundWarningSeverity,
    SampleScoredRecord,
    SampleStartedRecord,
    TokenUsageRecord,
)
from promptpotter.domain.scoring import GradedCell, WalkedCell
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.pricing import compute_usd
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
from promptpotter.infrastructure.store.io import write_lock
from promptpotter.infrastructure.store.layout import CampaignLayout, cycle_ledgers
from promptpotter.shared.measurement_context import MeasurementRole, measured_pass
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


def append_record(record: CycleRecord, ledger: CycleEventLog | None = None) -> int | None:
    """``None``: the bound ledger is muted. A failed append raises to the call site."""
    if ledger is None:
        ledger = bound_ledger(type(record).__name__)
    return None if ledger is None else ledger.append(record)


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
        append_record(record, ledger)
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


def emit_backend_warning(
    *,
    kind: str,
    attempt: int,
    max_attempts: int,
    wait_s: float,
    query: str,
    detail: str = "",
    error_class: str | None = None,
    status_code: int | None = None,
    final: bool = False,
) -> None:
    append_record(
        BackendWarningRecord(
            kind=kind,
            attempt=attempt,
            max_attempts=max_attempts,
            wait_s=float(wait_s),
            error_class=error_class,
            status_code=status_code,
            final=final,
            detail=detail[:400],
            query=query[:80],
        )
    )


def emit_provider_hold(
    *,
    ledger: CycleEventLog,
    sender: str,
    scope: str,
    strikes: int,
    gives_up_at: float,
    reading: BackpressureReading,
) -> None:
    append_record(
        ProviderHoldRecord(
            sender=sender, scope=scope, strikes=strikes, gives_up_at=gives_up_at, reading=reading
        ),
        ledger,
    )


def emit_provider_eased(*, ledger: CycleEventLog, sender: str, held_s: float) -> None:
    append_record(ProviderEasedRecord(sender=sender, held_s=held_s), ledger)


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


def emit_error_record(
    exc: BaseException, *, stop_reason: StopReason, traceback: str | None
) -> ErrorRecord:
    record = ErrorRecord.raised(
        exc,
        kind=type(exc).__name__,
        stop_reason=stop_reason,
        traceback=traceback,
        round=current_round(),
    )
    append_record(record)
    return record


def emit_round_warning(
    *,
    kind: RoundWarningKind,
    message: str,
    severity: RoundWarningSeverity = "warning",
    detail: dict[str, Any] | None = None,
) -> None:
    append_record(
        RoundWarningRecord(
            kind=kind,
            severity=severity,
            message=message,
            round=current_round(),
            detail=dict(detail or {}),
        )
    )


# Capped at the writer, never sliced off a full query by a reader.
QUERY_PREVIEW_CHARS = 120


def emit_sample_started(
    *,
    individual_id: str,
    role: MeasurementRole,
    sample_idx: int,
    sample_total: int,
    position: int,
    query: str,
    sample_lookahead: int,
) -> None:
    append_record(
        SampleStartedRecord(
            round=bound_round(),
            individual_id=individual_id,
            role=role,
            sample_idx=sample_idx,
            sample_total=sample_total,
            position=int(position),
            query_preview=query[:QUERY_PREVIEW_CHARS],
            sample_lookahead=int(sample_lookahead),
        )
    )


def emit_sample_scored(
    *,
    individual_id: str,
    role: MeasurementRole,
    sample_idx: int,
    sample_total: int,
    position: int,
    cell: GradedCell,
    running: ScoreSummary | None,
) -> None:
    append_record(
        SampleScoredRecord(
            round=bound_round(),
            individual_id=individual_id,
            role=role,
            sample_idx=sample_idx,
            sample_total=sample_total,
            position=int(position),
            result=cell,
            running=running,
        )
    )


def emit_race_catch_up(
    *,
    member: str,
    arm: ArmKey,
    sample_key: str,
    position: int,
    cells: dict[ArmKey, WalkedCell],
) -> None:
    append_record(
        RaceCatchUpRecord(
            round=bound_round(),
            arm=arm,
            member=member,
            sample_key=sample_key,
            position=int(position),
            cells=cells,
        )
    )
