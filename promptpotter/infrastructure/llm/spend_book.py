from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    METER_KINDS,
    METER_PRICES_REPLAYS,
    NESTED_NODE_PREFIX,
    CeilingMeter,
    SpendCeilings,
    TokenAccount,
    TokenUsageKind,
    bill_or_rate_usd,
    declare_ceiling,
)
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    emit_spend_hold,
    emit_token_usage,
    filed_kind,
    rate_priced_usd,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.runtime_flags import standing_run_limits
from promptpotter.infrastructure.store.read_model import (
    HOLD_TRAIL,
    HeldSends,
    held_tokens,
    iter_jsonl,
    usage_row_usd,
)
from promptpotter.shared.errors import (
    ErrorCategory,
    SendRefusedError,
    is_provider_credit_refusal,
)

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = [
    "FRAMING_TOKENS",
    "Admission",
    "Billed",
    "CallLabel",
    "Reported",
    "SendBound",
    "SendOutcome",
    "SpendBook",
    "Unreported",
    "admitted",
    "answered",
    "bind_spend_book",
    "bound_spend_book",
    "filed",
    "replied",
    "reservation_left",
    "reserved",
    "reset_spend_book",
    "spending_under",
    "unbounded_spend_book",
    "unreported_on",
]


@dataclass(frozen=True)
class CallLabel:
    node: str
    kind: TokenUsageKind


@dataclass(frozen=True)
class SendBound:
    """``output_tokens`` / ``usd`` are ``None`` where nothing caps the reply / no rate prices it."""

    input_tokens: int
    output_tokens: int | None
    usd: float | None
    unpriced: tuple[str, ...] = ()

    @property
    def tokens(self) -> int | None:
        return None if self.output_tokens is None else self.input_tokens + self.output_tokens


# Tokens a provider may add around a request's own text: chat-template markers, a rendered schema.
FRAMING_TOKENS = 512


class Reported(NamedTuple):
    """``unknown`` is the bound of attempts inside the send that reported nothing; it stays held."""

    parts: tuple[Billed, ...]
    unknown: SendBound | None = None


class SendOutcome(NamedTuple):
    sent: bool
    may_have_billed: bool
    reported: Reported | None = None
    failure: ErrorCategory | None = None
    # Never a timeout: the provider may still be generating, and a resend is a second bill.
    resendable: bool = False
    detail: str = ""
    headers: object | None = None


def _status_failure(
    status: int, said: str, credit_statuses: frozenset[int] | None
) -> ErrorCategory | None:
    if 200 <= status < 300:
        return None
    if status == 429:
        return ErrorCategory.PROVIDER_THROTTLED
    if 400 <= status < 500 and status != 408:
        credit = credit_statuses is None or status in credit_statuses
        if credit and is_provider_credit_refusal(said):
            return ErrorCategory.PROVIDER_CREDIT
        return ErrorCategory.CLIENT
    return ErrorCategory.SERVER


def answered(bill: Billed | None) -> SendOutcome:
    return SendOutcome(
        sent=True, may_have_billed=True, reported=None if bill is None else Reported((bill,))
    )


def replied(
    status: int,
    *,
    headers: object | None,
    said: str,
    reported: Reported | None = None,
    credit_statuses: frozenset[int] | None = None,
    answered_on: frozenset[int] = frozenset(),
) -> SendOutcome:
    failure = _status_failure(status, said, credit_statuses)
    # ``answered_on``: the statuses a relay answers a model that DID generate with, so may bill.
    refused = 400 <= status < 500 and status != 408 and status not in answered_on
    return SendOutcome(
        sent=True,
        may_have_billed=not refused,
        reported=reported,
        failure=failure,
        resendable=status >= 500,
        detail=said,
        headers=headers,
    )


def _times(left: float, each: float | None) -> int:
    if each is None or left < 0:
        return 0
    if each <= 0:
        return sys.maxsize
    return int(left / each)


class SpendBook(Projection):
    def __init__(
        self,
        *,
        declared: SpendCeilings,
        reserved: SpendCeilings,
        meters: CeilingMeter,
        cycle_dir: Path | None = None,
        usd_metered: float = 0.0,
        tokens_spent: int = 0,
    ) -> None:
        self.declared = declared
        self.reserved = reserved
        self.cycle_dir = cycle_dir
        self.meters = meters
        # Keyed by node, not kind: the bucket a send files under changes who pays, not its cost.
        self._billed_most: dict[str, tuple[float, int]] = {}
        # In the meter's units (``MeteredSpend.metered_usd``): never a figure to print as spent.
        self.usd_metered = usd_metered
        self.tokens_spent = tokens_spent
        self.usd_unreported = 0.0
        self.tokens_unreported = 0
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._out_usd: dict[TokenUsageKind, float] = {}
        self._out_tokens: dict[TokenUsageKind, int] = {}
        self.set_aside_usd = 0.0
        self.set_aside_tokens = 0
        self._seen: TokenUsageRecord | None = None
        # A nested run spends under its ROOT's book; `mirror` carries its bills onto this ledger.
        self.ledger: CycleEventLog | None = None
        self.round_now: Callable[[], int | None] = lambda: None

    @property
    def ceiling(self) -> SpendCeilings:
        if self.cycle_dir is None:
            return self.declared
        return declare_ceiling(self.declared, standing_run_limits(self.cycle_dir).ceiling)

    @property
    def reserve(self) -> SpendCeilings:
        if self.cycle_dir is None:
            return self.reserved
        return declare_ceiling(self.reserved, standing_run_limits(self.cycle_dir).reserve)

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self._seen = record
        self.count(record)

    def binds(self, kind: TokenUsageKind) -> bool:
        return kind in METER_KINDS[self.meters]

    def count(self, record: TokenUsageRecord) -> None:
        # Elsewhere a replay spent no money: a price of nothing (0.0), not a missing one (None).
        replay_usd = METER_PRICES_REPLAYS[self.meters]
        self._spend(
            record.kind,
            record.bill_or_rate_usd if not record.cached or replay_usd else 0.0,
            0 if record.cached else record.input_tokens + record.output_tokens,
        )

    def _spend(self, kind: TokenUsageKind, usd: float | None, tokens: int) -> None:
        """``usd`` is ``None`` where neither a bill nor a rate prices the call."""
        if not self.binds(kind):
            return
        if usd is not None:
            self.usd_metered += usd
        self.tokens_spent += tokens

    def set_aside(self, usd: float, tokens: int) -> None:
        self.set_aside_usd = usd
        self.set_aside_tokens = tokens

    def saw(self, record: TokenUsageRecord) -> bool:
        """Valid only right after the append: it compares against the LAST record seen."""
        return record is self._seen

    def foreign(self, ledger: CycleEventLog | None) -> bool:
        return self.ledger is not None and ledger is not self.ledger

    def mirror(self, record: TokenUsageRecord, *, hold_id: str) -> bool:
        if self.ledger is None:
            return False
        copy = record.model_copy(
            update={
                "kind": "backend",
                "node": f"{NESTED_NODE_PREFIX}{record.node}",
                "round": self.round_now(),
                "hold_id": hold_id,
                "mirrored": False,
            }
        )
        try:
            self.ledger.append(copy)
        except Exception:
            logger.exception("could not carry a nested run's call onto the root ledger")
            return False
        return self.saw(copy)

    def exhausted(self) -> ErrorCategory | None:
        ceiling = self.ceiling
        usd_used = self.usd_metered + self.usd_unreported + self.set_aside_usd
        if ceiling.usd is not None and usd_used >= ceiling.usd:
            return ErrorCategory.SPEND_CEILING
        tokens_used = self.tokens_spent + self.tokens_unreported + self.set_aside_tokens
        if ceiling.tokens is not None and tokens_used >= ceiling.tokens:
            return ErrorCategory.TOKEN_CEILING
        return None

    def _room(
        self, held: SendBound, bound: SendBound, beside: TokenUsageKind | None
    ) -> Iterator[tuple[ErrorCategory, bool, int]]:
        ceiling, reserved = self.ceiling, self.reserve
        used = self.usd_metered + self.usd_unreported + self.set_aside_usd
        for reserve, limit, out, each in (
            (False, ceiling.usd, self._held_usd, held.usd),
            (True, reserved.usd, self._out_usd, bound.usd),
        ):
            if limit is not None:
                left = limit - used - sum(v for k, v in out.items() if k != beside)
                yield ErrorCategory.SPEND_CEILING, reserve, _times(left, each)
        used_t = self.tokens_spent + self.tokens_unreported + self.set_aside_tokens
        for reserve, limit_t, out_t, each_t in (
            (False, ceiling.tokens, self._held_tokens, held.tokens),
            (True, reserved.tokens, self._out_tokens, bound.tokens),
        ):
            if limit_t is not None:
                left_t = limit_t - used_t - sum(v for k, v in out_t.items() if k != beside)
                yield ErrorCategory.TOKEN_CEILING, reserve, _times(left_t, each_t)

    def held_at(self, label: CallLabel, bound: SendBound) -> SendBound:
        most = self._billed_most.get(label.node)
        if most is None or bound.usd is None or bound.tokens is None:
            return bound
        return SendBound(
            input_tokens=0,
            output_tokens=min(bound.tokens, most[1]),
            usd=min(bound.usd, most[0]),
        )

    def learn(self, label: CallLabel, usd: float, tokens: int) -> None:
        # A bill of nothing is a send the provider never ran: it teaches nothing.
        if usd <= 0.0 and tokens <= 0:
            return
        was = self._billed_most.get(label.node, (0.0, 0))
        self._billed_most[label.node] = (max(was[0], usd), max(was[1], tokens))

    def take_up(self, ledger: CycleEventLog) -> Unreported:
        left = unreported_on(ledger)
        self.usd_unreported, self.tokens_unreported = left.usd, left.tokens
        for label, usd, tokens in left.dearest:
            self.learn(label, usd, tokens)
        return left

    def fits(
        self, held: SendBound, bound: SendBound, *, beside: TokenUsageKind | None = None
    ) -> int:
        return min((n for _, _, n in self._room(held, bound, beside)), default=sys.maxsize)

    def binding(
        self, held: SendBound, bound: SendBound, *, beside: TokenUsageKind | None = None
    ) -> SendBound:
        sides = sorted(self._room(held, bound, beside), key=lambda side: side[2])
        return bound if sides and sides[0][1] else held

    def hold(
        self,
        held: SendBound,
        bound: SendBound,
        kind: TokenUsageKind,
        *,
        what: str,
        drawn_from: _Reservation | None = None,
    ) -> None:
        if not self.binds(kind):
            return
        if drawn_from is None:
            self.refuse_unless_room(held, bound, what)
        else:
            drawn_from.draw(held, bound, what)
        self._held_usd[kind] = self._held_usd.get(kind, 0.0) + (held.usd or 0.0)
        self._held_tokens[kind] = self._held_tokens.get(kind, 0) + (held.tokens or 0)
        self._out_usd[kind] = self._out_usd.get(kind, 0.0) + (bound.usd or 0.0)
        self._out_tokens[kind] = self._out_tokens.get(kind, 0) + (bound.tokens or 0)

    def refuse_unless_room(self, held: SendBound, bound: SendBound, what: str) -> None:
        self.refuse_unpriced(bound, what)
        for category, reserve, n in self._room(held, bound, None):
            if n < 1:
                raise SendRefusedError(
                    self._refusal(category, reserve, bound if reserve else held, what),
                    category=category,
                )

    def refuse_unpriced(self, bound: SendBound, what: str) -> None:
        if bound.usd is not None or not self.prices_sends():
            return
        on = f" on {', '.join(bound.unpriced)}" if bound.unpriced else ""
        raise SendRefusedError(
            f"{what}: no rate bounds what it may cost{on}, so the dollar ceiling cannot be "
            "enforced for it and nothing was sent. Run it on a (provider, model) pair the rate "
            "table prices (`nodes.{node}.config`), or drop the dollar cap and bound the run in "
            "tokens (`ceiling: {usd: null, tokens: N}`); then `resume`.",
            category=ErrorCategory.NO_RATE,
        )

    def _refusal(self, category: ErrorCategory, reserve: bool, each: SendBound, what: str) -> str:
        limit = "account reserve" if reserve else "ceiling"
        if category is ErrorCategory.SPEND_CEILING:
            unreported = (
                f", ${self.usd_unreported:.4f} unreported (sends no bill priced)"
                if self.usd_unreported
                else ""
            )
            cap = (self.reserve if reserve else self.ceiling).usd
            out = self._out_usd if reserve else self._held_usd
            return (
                f"{what} holds ${each.usd:.4f} while it is out, and the ${cap:.4f} {limit} "
                f"holds ${self.usd_metered:.4f} {CEILING_METER_LABELS[self.meters]}{unreported}, "
                f"${sum(out.values()):.4f} for calls out and "
                f"${self.set_aside_usd:.4f} set aside for the bench"
            )
        if each.tokens is None:
            return f"{what}: nothing caps its reply, so it cannot run under a token ceiling"
        cap_t = (self.reserve if reserve else self.ceiling).tokens
        out_t = self._out_tokens if reserve else self._held_tokens
        return (
            f"{what} holds {each.tokens:,} tokens while it is out, and the {cap_t:,}-token "
            f"{limit} holds {self.tokens_spent:,} spent, {self.tokens_unreported:,} unreported, "
            f"{sum(out_t.values()):,} for calls out and "
            f"{self.set_aside_tokens:,} set aside for the bench"
        )

    def release(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        if not self.binds(kind):
            return
        self._held_usd[kind] -= held.usd or 0.0
        self._held_tokens[kind] -= held.tokens or 0
        self._out_usd[kind] -= bound.usd or 0.0
        self._out_tokens[kind] -= bound.tokens or 0

    def unpriced(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        if not self.binds(kind):
            return
        self.release(held, bound, kind)
        self.usd_unreported += bound.usd or 0.0

    def unreported(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        if not self.binds(kind):
            return
        self.release(held, bound, kind)
        self.keep_held(bound, kind)

    def keep_held(self, bound: SendBound, kind: TokenUsageKind) -> None:
        if not self.binds(kind):
            return
        self.usd_unreported += bound.usd or 0.0
        self.tokens_unreported += bound.tokens or 0

    def prices_sends(self) -> bool:
        return self.ceiling.usd is not None or self.reserve.usd is not None


def unbounded_spend_book() -> SpendBook:
    return SpendBook(declared=SpendCeilings(), reserved=SpendCeilings(), meters="bill")


_BOOK: ContextVar[SpendBook | None] = ContextVar("spend_book", default=None)


def bind_spend_book(book: SpendBook) -> Token[SpendBook | None]:
    return _BOOK.set(book)


def reset_spend_book(token: Token[SpendBook | None]) -> None:
    _BOOK.reset(token)


def bound_spend_book() -> SpendBook | None:
    return _BOOK.get()


@contextmanager
def spending_under(book: SpendBook) -> Iterator[SpendBook]:
    token = _BOOK.set(book)
    try:
        yield book
    finally:
        _BOOK.reset(token)


def filed(label: CallLabel) -> CallLabel:
    """Admits a send in the bucket its bill lands in, so a bench pass is never held as search."""
    return CallLabel(label.node, filed_kind(label.kind))


def _bound_book(what: str) -> SpendBook:
    book = _BOOK.get()
    if book is None:
        raise RuntimeError(
            f"{what}: no spend book is bound, and a paid call is never sent unadmitted"
        )
    return book


def _amount(usd: float, tokens: int) -> SendBound:
    return SendBound(input_tokens=0, output_tokens=tokens, usd=usd)


class _Reservation:
    """In memory only: a reservation is no send, so it writes no ledger record."""

    def __init__(
        self, book: SpendBook, kind: TokenUsageKind, held: SendBound, bound: SendBound
    ) -> None:
        self.book = book
        self.kind = kind
        self.held_usd = held.usd or 0.0
        self.held_tokens = held.tokens or 0
        self.usd = bound.usd or 0.0
        self.tokens = bound.tokens or 0
        # ``None`` once a send inside closed without a price: nobody knows what the cell cost.
        self.billed: tuple[float, int] | None = (0.0, 0)

    def closed(self, bill: tuple[float, int] | None) -> None:
        if bill is None or self.billed is None:
            self.billed = None
        else:
            self.billed = (self.billed[0] + bill[0], self.billed[1] + bill[1])

    def draw(self, held: SendBound, bound: SendBound, what: str = "a send") -> None:
        """The rest is admitted against the ceiling: it stops a cell, never the estimate."""
        held_usd = min(self.held_usd, held.usd or 0.0)
        held_tokens = min(self.held_tokens, held.tokens or 0)
        usd, tokens = min(self.usd, bound.usd or 0.0), min(self.tokens, bound.tokens or 0)
        short_held = _amount((held.usd or 0.0) - held_usd, (held.tokens or 0) - held_tokens)
        short = _amount((bound.usd or 0.0) - usd, (bound.tokens or 0) - tokens)
        if (short_held.usd or short_held.tokens or short.usd or short.tokens) and self.book.binds(
            self.kind
        ):
            self.book.refuse_unless_room(short_held, short, f"{what}, past its cell's reservation")
        self.held_usd -= held_usd
        self.held_tokens -= held_tokens
        self.usd -= usd
        self.tokens -= tokens
        self.book.release(_amount(held_usd, held_tokens), _amount(usd, tokens), self.kind)


_RESERVATION: ContextVar[_Reservation | None] = ContextVar("spend_reservation", default=None)


def reservation_left() -> SendBound:
    reservation = _RESERVATION.get()
    if reservation is None:
        raise RuntimeError("no cell reservation is open to bound this send")
    return _amount(reservation.usd, reservation.tokens)


@contextmanager
def reserved(label: CallLabel, bound: SendBound) -> Iterator[None]:
    """``bound`` is the run a cell DECLARES, never its worst case, which fits one cell at a time."""
    label = filed(label)
    book = _bound_book(label.node)
    held = book.held_at(label, bound)
    book.hold(held, bound, label.kind, what=label.node)
    reservation = _Reservation(book, label.kind, held, bound)
    token = _RESERVATION.set(reservation)
    try:
        yield
    finally:
        _RESERVATION.reset(token)
        book.release(
            _amount(reservation.held_usd, reservation.held_tokens),
            _amount(reservation.usd, reservation.tokens),
            label.kind,
        )
        if reservation.billed is not None:
            book.learn(label, *reservation.billed)


class Billed(NamedTuple):
    usage: TokenAccount
    # Only what the provider REPORTED; ``None`` is priced at our rate by ``emit_token_usage``.
    cost_usd: float | None
    served_by: str | None = None
    model: str | None = None
    node: str | None = None
    provider: str | None = None
    duration_s: float | None = None
    rate_priced_usd: float | None = None


# Holds THIS process has out, so a ledger scan never reads one awaiting its bill as unreported.
_OUT: set[str] = set()


class Admission:
    def __init__(
        self,
        book: SpendBook,
        label: CallLabel,
        bound: SendBound,
        held: SendBound,
        *,
        model: str | None,
        provider: str | None,
    ) -> None:
        self._book = book
        self._label = label
        self._bound = bound
        self._held = held
        self._billed_usd = 0.0
        self._billed_tokens = 0
        self._priced = True
        self._model = model
        self._provider = provider
        self._started = time.monotonic()
        self._hold_id = uuid.uuid4().hex
        self._foreign = book.foreign(active_cycle_ledger())
        self._unseen: list[TokenUsageRecord] = []
        self._unlanded: list[tuple[float | None, int]] = []
        self.closed = False
        # What the send closed at, once every part of it was priced; ``None`` until then.
        self.bill: tuple[float, int] | None = None
        _OUT.add(self._hold_id)
        emit_spend_hold(
            hold_id=self._hold_id,
            node=label.node,
            kind=label.kind,
            input_tokens=bound.input_tokens,
            output_tokens=bound.output_tokens,
            cost_usd=bound.usd,
            model=model,
            provider=provider,
            ledger=book.ledger,
        )

    def _emit(self, part: Billed) -> None:
        model = part.model or self._model
        provider = part.provider or self._provider
        record = emit_token_usage(
            node=part.node or self._label.node,
            kind=self._label.kind,
            usage=part.usage,
            duration_s=(
                time.monotonic() - self._started if part.duration_s is None else part.duration_s
            ),
            model=model,
            provider=provider,
            served_by=part.served_by,
            cost_usd=part.cost_usd,
            recorded_rate_usd=part.rate_priced_usd,
            hold_id=self._hold_id,
            mirrored=self._foreign,
        )
        if record is None:
            usd = bill_or_rate_usd(
                part.cost_usd,
                rate_priced_usd(
                    part.usage,
                    model=model,
                    provider=provider,
                    cost_usd=part.cost_usd,
                    recorded=part.rate_priced_usd,
                ),
            )
            self._unlanded.append((usd, part.usage.input + part.usage.output))
            self._bill(usd, part.usage.input + part.usage.output)
            logger.warning(
                "%s: a call counted at $%s reached no ledger; only this run's book counts it",
                part.node or self._label.node,
                "?" if usd is None else f"{usd:.6f}",
            )
            return
        self._bill(record.bill_or_rate_usd, record.input_tokens + record.output_tokens)
        if self._foreign:
            if not self._book.mirror(record, hold_id=self._hold_id):
                self._unseen.append(record)
        elif not self._book.saw(record):
            self._unseen.append(record)

    def _bill(self, usd: float | None, tokens: int) -> None:
        if usd is None:
            self._priced = False
        else:
            self._billed_usd += usd
        self._billed_tokens += tokens

    def close(self, outcome: SendOutcome) -> None:
        if outcome.reported is not None:
            self.settle(*outcome.reported.parts, unknown=outcome.reported.unknown)
        elif outcome.may_have_billed:
            self.unreported()
        else:
            self.release()

    def settle(self, *parts: Billed, unknown: SendBound | None = None) -> None:
        for part in parts or (Billed(TokenAccount(), 0.0),):
            self._emit(part)
        if unknown is not None:
            emit_spend_hold(
                hold_id=uuid.uuid4().hex,
                node=self._label.node,
                kind=self._label.kind,
                input_tokens=unknown.input_tokens,
                output_tokens=unknown.output_tokens,
                cost_usd=unknown.usd,
                model=self._model,
                provider=self._provider,
                ledger=self._book.ledger,
            )
            self._book.keep_held(unknown, self._label.kind)
        self._finish()
        if self._priced:
            self._book.release(self._held, self._bound, self._label.kind)
            self._book.learn(self._label, self._billed_usd, self._billed_tokens)
            if unknown is None:
                self.bill = (self._billed_usd, self._billed_tokens)
        else:
            self._book.unpriced(self._held, self._bound, self._label.kind)
        for record in self._unseen:
            self._book.count(record)
        for usd, tokens in self._unlanded:
            self._book._spend(self._label.kind, usd, tokens)

    def release(self) -> None:
        # A bill of nothing, so no later scan reads the hold as a send that ended unreported.
        self.settle()

    def unreported(self) -> None:
        """Writes nothing: the hold stays open on the ledger, and the book keeps its bound held."""
        self._finish()
        self._book.unreported(self._held, self._bound, self._label.kind)

    def _finish(self) -> None:
        if self.closed:
            # A second close would release the hold twice and admit sends on room nobody has.
            raise RuntimeError(f"{self._label.node}: an admission is closed once")
        self.closed = True
        _OUT.discard(self._hold_id)


@contextmanager
def admitted(
    label: CallLabel, bound: SendBound, *, model: str | None, provider: str | None
) -> Iterator[Admission]:
    label = filed(label)
    book = _bound_book(label.node)
    reservation = _RESERVATION.get()
    drawn_from = (
        reservation
        if reservation is not None and reservation.book is book and reservation.kind == label.kind
        else None
    )
    what = f"{label.node} on {model}" if model else label.node
    held = book.held_at(label, bound)
    book.hold(held, bound, label.kind, what=what, drawn_from=drawn_from)
    admission = Admission(book, label, bound, held, model=model, provider=provider)
    try:
        yield admission
    finally:
        if not admission.closed:
            admission.unreported()
        if drawn_from is not None:
            drawn_from.closed(admission.bill)


class Unreported(NamedTuple):
    usd: float
    tokens: int
    sends: int
    dearest: tuple[tuple[CallLabel, float, int], ...] = ()


def unreported_on(ledger: CycleEventLog) -> Unreported:
    """``store/account_spend.py`` cannot see ``_OUT``; it asks the run's phase for liveness."""
    held = HeldSends()
    labels: dict[str, CallLabel] = {}
    bills: dict[str, tuple[float, int]] = {}
    # The ledger's OWN lines only: walking a fork chain holds the parent's open sends twice.
    for rec in iter_jsonl(ledger.path, record_types=HOLD_TRAIL):
        held.track(rec)
        hold_id = rec.get("hold_id")
        if rec.get("record_type") == "spend_hold":
            labels[str(hold_id)] = CallLabel(rec["node"], rec["kind"])
        elif hold_id in labels and (usd := usage_row_usd(rec)) is not None:
            usd_was, tokens_was = bills.get(hold_id, (0.0, 0))
            billed = int(rec.get("input_tokens", 0)) + int(rec.get("output_tokens", 0))
            bills[hold_id] = (usd_was + usd, tokens_was + billed)
    left = [rec for hold_id, rec in held.open.items() if hold_id not in _OUT]
    tokens = sum(held_tokens(h) for h in left)
    # A hold with no bound was admitted where no ceiling stood, and names no money to keep.
    bounds = (*(h.get("cost_usd") for h in left), *held.unpriced.values())
    usd = sum(float(bound) for bound in bounds if bound is not None)
    dearest = tuple(
        (labels[hold_id], billed_usd, billed_tokens)
        for hold_id, (billed_usd, billed_tokens) in bills.items()
        if hold_id not in held.unpriced
    )
    return Unreported(usd, tokens, len(left) + len(held.unpriced), dearest)
