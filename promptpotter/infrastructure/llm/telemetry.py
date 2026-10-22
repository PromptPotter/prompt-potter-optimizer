from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import ScoreSummary
from promptpotter.domain.run_records import (
    BackendWarningRecord,
    CommandAckRecord,
    CommandAckStatus,
    CommandRecord,
    CycleRecord,
    ErrorRecord,
    PricedKeyRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RoundWarningKind,
    RoundWarningRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    SpendHoldRecord,
    TokenUsageRecord,
)
from promptpotter.domain.scoring import GradedCell, WalkedCell
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.pricing import compute_usd
from promptpotter.shared.identity import Principal
from promptpotter.shared.measurement_context import MeasurementRole, measured_pass

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


# No default: unbound is "never bound", and a bound ``None`` is the declared mute of a probe render.
_CYCLE_LEDGER: ContextVar[CycleEventLog | None] = ContextVar("cycle_ledger")
_CURRENT_ROUND: ContextVar[int | None] = ContextVar("current_round", default=None)


def set_cycle_ledger(ledger: CycleEventLog | None) -> Token[CycleEventLog | None]:
    return _CYCLE_LEDGER.set(ledger)


def reset_cycle_ledger(token: Token[CycleEventLog | None]) -> None:
    _CYCLE_LEDGER.reset(token)


def active_cycle_ledger() -> CycleEventLog | None:
    """Ask before OPENING one: a second handle is a second appender, and ``append`` is not atomic."""
    return _CYCLE_LEDGER.get(None)


def bound_ledger(what: str) -> CycleEventLog | None:
    try:
        return _CYCLE_LEDGER.get()
    except LookupError:
        raise RuntimeError(
            f"{what}: no cycle ledger is bound, so this record would reach no file. Bind one "
            "where the entry binds its spend book (`build_run_observers`, `verb_ledger`)."
        ) from None


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


def _append_record(record: CycleRecord, ledger: CycleEventLog | None = None) -> int | None:
    if ledger is None:
        ledger = bound_ledger(type(record).__name__)
    if ledger is None:
        return None
    try:
        return ledger.append(record)
    except Exception:
        # Telemetry must not break its call site.
        logger.exception("ledger append failed for %s", type(record).__name__)
        return None


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


def emit_token_usage(
    *,
    node: str,
    kind: TokenUsageKind,
    usage: TokenAccount,
    duration_s: float,
    model: str | None = None,
    provider: str | None = None,
    served_by: str | None = None,
    cost_usd: float | None = None,
    recorded_rate_usd: float | None = None,
    cached: bool = False,
    hold_id: str | None = None,
    mirrored: bool = False,
) -> TokenUsageRecord | None:
    record = TokenUsageRecord(
        kind=filed_kind(kind),
        node=node,
        model=model,
        provider=provider,
        served_by=served_by,
        # Flat on purpose: every lifetime-spend read sums these counts off raw JSON.
        input_tokens=usage.input,
        output_tokens=usage.output,
        reasoning_tokens=usage.reasoning,
        cache_read_tokens=usage.cache_read or 0,
        cache_write_tokens=usage.cache_write,
        duration_s=float(duration_s),
        cost_usd=cost_usd,
        # Priced once, here: readers sum the stamp, so a rate refresh rewrites nothing.
        rate_priced_usd=rate_priced_usd(
            usage, model=model, provider=provider, cost_usd=cost_usd, recorded=recorded_rate_usd
        ),
        hold_id=hold_id,
        mirrored=mirrored,
        cached=cached,
        round=current_round(),
        role=cand.role if (cand := measured_pass()) else None,
    )
    return record if _append_record(record) is not None else None


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
    _append_record(
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
    _append_record(
        ProviderHoldRecord(
            sender=sender, scope=scope, strikes=strikes, gives_up_at=gives_up_at, reading=reading
        ),
        ledger,
    )


def emit_provider_eased(*, ledger: CycleEventLog, sender: str, held_s: float) -> None:
    _append_record(ProviderEasedRecord(sender=sender, held_s=held_s), ledger)


# Cells (`ReplayFeed.cell_key`) and calls (`call:{reuse key}`), per CAMPAIGN and never per spend
# book: a nested run's book is enclosed by its root's.
_PRICED: ContextVar[set[str] | None] = ContextVar("priced", default=None)


def bind_priced(priced: set[str]) -> None:
    """The run's campaign: a cell or call it priced once replays free for it ever after."""
    _PRICED.set(priced)


def campaign_priced(key: str) -> bool:
    priced = _PRICED.get()
    return priced is not None and key in priced


def claim_campaign_price(key: str) -> bool:
    """Whether this reading of *key* is its campaign's first, which this call claims; outside a
    campaign's run every reading is."""
    priced = _PRICED.get()
    if priced is None:
        return True
    if key in priced:
        return False
    priced.add(key)
    _append_record(PricedKeyRecord(priced_key=key))
    return True


def claim_call_price(reuse_key: str) -> bool:
    return claim_campaign_price(f"call:{reuse_key}")


def emit_spend_hold(
    *,
    hold_id: str,
    node: str,
    kind: TokenUsageKind,
    input_tokens: int,
    output_tokens: int | None,
    cost_usd: float | None,
    model: str | None,
    provider: str | None,
    ledger: CycleEventLog | None,
) -> None:
    """``kind`` arrives already filed (``spend_book.py::filed``)."""
    _append_record(
        SpendHoldRecord(
            hold_id=hold_id,
            kind=kind,
            node=node,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            round=current_round(),
        ),
        ledger,
    )


def emit_command(
    *,
    command_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    issued_by: Principal,
) -> int | None:
    return _append_record(
        CommandRecord(
            command_id=command_id,
            kind=kind,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            issued_by=issued_by,
        )
    )


def emit_command_ack(
    *,
    command_id: str,
    status: CommandAckStatus,
    detail: str = "",
    effect: dict[str, Any] | None = None,
) -> None:
    _append_record(
        CommandAckRecord(command_id=command_id, status=status, detail=detail, effect=effect or {})
    )


def emit_error_record(
    *,
    kind: str,
    message: str,
    stop_reason: StopReason,
    traceback: str | None = None,
) -> ErrorRecord:
    record = ErrorRecord(
        kind=kind,
        message=message,
        traceback=traceback,
        stop_reason=stop_reason,
        round=current_round(),
    )
    _append_record(record)
    return record


def emit_round_warning(
    *,
    kind: RoundWarningKind,
    message: str,
    severity: Literal["warning", "error"] = "warning",
    detail: dict[str, Any] | None = None,
) -> None:
    _append_record(
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
    _append_record(
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
    _append_record(
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
    _append_record(
        RaceCatchUpRecord(
            round=bound_round(),
            arm=arm,
            member=member,
            sample_key=sample_key,
            position=int(position),
            cells=cells,
        )
    )


__all__ = [
    "active_cycle_ledger",
    "at_round",
    "billing_ledger",
    "bind_priced",
    "bound_ledger",
    "bound_round",
    "campaign_priced",
    "claim_call_price",
    "claim_campaign_price",
    "current_round",
    "emit_command",
    "emit_command_ack",
    "emit_error_record",
    "emit_provider_eased",
    "emit_provider_hold",
    "emit_race_catch_up",
    "emit_round_warning",
    "emit_sample_scored",
    "emit_sample_started",
    "emit_token_usage",
    "filed_as",
    "filed_kind",
    "rate_priced_usd",
    "reset_cycle_ledger",
    "set_cycle_ledger",
]
