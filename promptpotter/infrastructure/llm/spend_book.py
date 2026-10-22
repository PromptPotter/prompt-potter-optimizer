from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from promptpotter.domain.run_records import SpendHoldRecord, TokenUsageRecord
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    METER_KINDS,
    METER_PRICES_REPLAYS,
    MONEY_AXES,
    NESTED_NODE_PREFIX,
    NOTHING_METERED,
    Budget,
    CeilingMeter,
    TokenAccount,
    TokenUsageKind,
    bill_or_rate_usd,
    metered,
)
from promptpotter.infrastructure.llm.telemetry import (
    billing_ledger,
    campaign_priced,
    current_round,
    emit_spend_hold,
    emit_token_usage,
    filed_kind,
    rate_priced_usd,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.runtime_flags import standing_budget
from promptpotter.infrastructure.store.io import read_json
from promptpotter.infrastructure.store.read_model import (
    HOLD_TRAIL,
    HeldSends,
    HoldTrailRecord,
    LedgerIndex,
    RecordClasses,
    held_tokens,
)
from promptpotter.shared.errors import (
    ErrorCategory,
    SendRefusedError,
    is_provider_credit_refusal,
)
from promptpotter.shared.measurement_context import instrument_mode

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


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


def _admits(left: float, each: float | None, reserve: bool) -> int:
    """The ceiling admits while any of it is left, so a run stops AT its cap; the reserve is what
    bounds the calls out when it is reached."""
    n = _times(left, each)
    return n if reserve or each is None or left <= 0 else max(n, 1)


class Hold(NamedTuple):
    # The most the book's cycle may have metered of its campaign's ceiling, in all.
    allotment: Budget
    # What the account holds back for it, on the axes an account bounds.
    reserve: Budget


class JobHold:
    """A registry job's hold, re-read whenever its file changes: a reshare moves it mid-run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stamp: tuple[int, int, int] | None = None
        self._hold = Hold(Budget(), Budget())

    def read(self) -> Hold:
        stat = self.path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp != self._stamp:
            job = read_json(self.path)
            self._hold = Hold(
                Budget.model_validate(job["allotment"]), Budget.model_validate(job["reserve"])
            )
            self._stamp = stamp
        return self._hold


# What an enclosing book files a nested run's sends as, as its ledger's copy of them does.
_ENCLOSED: TokenUsageKind = "backend"


class SpendBook(Projection):
    def __init__(
        self,
        *,
        declared: Budget,
        hold: Hold | JobHold,
        meters: CeilingMeter,
        opened: float,
        cycle_dir: Path | None = None,
        metered: Budget = NOTHING_METERED,
    ) -> None:
        # The launch's own axis (`wall_clock_s`); money binds on the hold.
        self.declared = declared.only(CYCLE_AXES)
        self.hold_of = hold
        self.cycle_dir = cycle_dir
        self.meters = meters
        # The launch's ``time.monotonic()``: a book re-armed mid-launch keeps its clock.
        self.opened = opened
        # Keyed by node, not kind: the bucket a send files under changes who pays, not its cost.
        self._billed_most: dict[str, tuple[float, int]] = {}
        # The cycle's OWN, in the meter's units: never a figure to print as spent.
        self.usd_metered = metered.usd_amount
        self.tokens_spent = metered.token_amount
        self.usd_unreported = 0.0
        self.tokens_unreported = 0
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._out_usd: dict[TokenUsageKind, float] = {}
        self._out_tokens: dict[TokenUsageKind, int] = {}
        self.set_aside_usd = 0.0
        self.set_aside_tokens = 0
        self._seen: TokenUsageRecord | None = None
        self.ledger: CycleEventLog | None = None
        # A nested run's book: each send is admitted against it AND every book enclosing it.
        self.enclosing: SpendBook | None = None

    # Each read live: a running change moves the cycle's standing record and the job's hold.
    @property
    def budget(self) -> Budget:
        if self.cycle_dir is None:
            return self.declared
        return self.declared.over(standing_budget(self.cycle_dir).budget)

    def _hold(self) -> Hold:
        return self.hold_of if isinstance(self.hold_of, Hold) else self.hold_of.read()

    @property
    def allotment(self) -> Budget:
        return self._hold().allotment.only(MONEY_AXES)

    @property
    def reserve(self) -> Budget:
        return self._hold().reserve

    def root(self) -> SpendBook:
        return self if self.enclosing is None else self.enclosing.root()

    def clock_left(self) -> float | None:
        """Seconds this launch may still run; ``None`` where no wall clock bounds it."""
        limit = self.budget.wall_clock_s
        return None if limit is None else limit - (time.monotonic() - self.opened)

    def _usd_used(self) -> float:
        return self.usd_metered + self.usd_unreported + self.set_aside_usd

    def _tokens_used(self) -> int:
        return self.tokens_spent + self.tokens_unreported + self.set_aside_tokens

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self._seen = record
        # A nested run's copy is the ledger's: its book already counted it into this one.
        if not record.node.startswith(NESTED_NODE_PREFIX):
            self.count(record)

    def binds(self, kind: TokenUsageKind) -> bool:
        return kind in METER_KINDS[self.meters]

    @property
    def prices_replays(self) -> bool:
        return METER_PRICES_REPLAYS[self.meters]

    def replays_free(self, cell_key: str) -> bool:
        """A bill meter prices no replay; the other prices one the first time its campaign reads it."""
        return not self.prices_replays or campaign_priced(cell_key)

    def count(self, record: TokenUsageRecord) -> None:
        usd, tokens = metered(record, self.meters)
        if usd is not None:
            self.usd_metered += usd
        self.tokens_spent += tokens
        if self.enclosing is not None:
            self.enclosing.count(record.model_copy(update={"kind": _ENCLOSED}))

    def _spend(self, kind: TokenUsageKind, usd: float | None, tokens: int) -> None:
        """``usd`` is ``None`` where neither a bill nor a rate prices the call."""
        for book, _ in self._levels(kind):
            if usd is not None:
                book.usd_metered += usd
            book.tokens_spent += tokens

    def set_aside(self, usd: float, tokens: int) -> None:
        self.set_aside_usd = usd
        self.set_aside_tokens = tokens

    def saw(self, record: TokenUsageRecord) -> bool:
        """Valid only right after the append: it compares against the LAST record seen."""
        return record is self._seen

    def mirror(self, record: TokenUsageRecord, *, hold_id: str) -> None:
        """The root ledger's copy of a nested run's call; its books counted it already."""
        if self.ledger is None:
            return
        mode = instrument_mode()
        copy = record.model_copy(
            update={
                "kind": "backend",
                "node": f"{NESTED_NODE_PREFIX}{record.node}",
                # The ROOT's round, whose ledger this lands on; the nested run binds its own.
                "round": current_round() if mode is None else mode.root_round,
                "hold_id": hold_id,
                "mirrored": False,
            }
        )
        try:
            self.ledger.append(copy)
        except Exception:
            logger.exception("could not carry a nested run's call onto the root ledger")

    def exhausted(self) -> ErrorCategory | None:
        allotment = self.allotment
        if allotment.usd is not None and self._usd_used() >= allotment.usd:
            return ErrorCategory.SPEND_CEILING
        if allotment.tokens is not None and self._tokens_used() >= allotment.tokens:
            return ErrorCategory.TOKEN_CEILING
        left = self.clock_left()
        if left is not None and left <= 0.0:
            return ErrorCategory.WALL_CLOCK
        return None

    def _room(
        self, held: SendBound, bound: SendBound, beside: TokenUsageKind | None
    ) -> Iterator[tuple[ErrorCategory, bool, int]]:
        budget, reserved = self.allotment, self.reserve
        used = self._usd_used()
        for reserve, limit, out, each in (
            (False, budget.usd, self._held_usd, held.usd),
            (True, reserved.usd, self._out_usd, bound.usd),
        ):
            if limit is not None:
                left = limit - used - sum(v for k, v in out.items() if k != beside)
                yield ErrorCategory.SPEND_CEILING, reserve, _admits(left, each, reserve)
        used_t = self._tokens_used()
        for reserve, limit_t, out_t, each_t in (
            (False, budget.tokens, self._held_tokens, held.tokens),
            (True, reserved.tokens, self._out_tokens, bound.tokens),
        ):
            if limit_t is not None:
                left_t = limit_t - used_t - sum(v for k, v in out_t.items() if k != beside)
                yield ErrorCategory.TOKEN_CEILING, reserve, _admits(left_t, each_t, reserve)

    def held_at(self, label: CallLabel, bound: SendBound) -> SendBound:
        most = self._billed_most.get(label.node)
        if most is None or bound.usd is None or bound.tokens is None:
            return bound
        return SendBound(
            input_tokens=0,
            output_tokens=min(bound.tokens, most[1]),
            usd=min(bound.usd, most[0]),
        )

    def learn(self, label: CallLabel, bill: Budget) -> None:
        usd, tokens = bill.usd_amount, bill.token_amount
        # A bill of nothing is a send the provider never ran: it teaches nothing.
        if usd <= 0.0 and tokens <= 0:
            return
        was = self._billed_most.get(label.node, (0.0, 0))
        self._billed_most[label.node] = (max(was[0], usd), max(was[1], tokens))

    def take_up(self, ledger: CycleEventLog) -> Unreported:
        left = unreported_on(ledger)
        self.usd_unreported, self.tokens_unreported = left.usd, left.tokens
        for label, usd, tokens in left.dearest:
            self.learn(label, Budget.amount(usd, tokens))
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
        reservation: _Reservation | None,
    ) -> bool:
        """True where an open cell reservation in this book's chain paid part of it."""
        levels = list(self._levels(kind))
        at = next(
            (
                i
                for i, (book, filed_as) in enumerate(levels)
                if reservation is not None
                and reservation.book is book
                and reservation.kind == filed_as
            ),
            None,
        )
        for book, _ in levels if at is None else levels[:at]:
            book.refuse_unless_room(held, bound, what)
        if at is not None and reservation is not None:
            reservation.draw(held, bound, what)
        for book, filed_as in levels:
            book._move(filed_as, held, bound, 1)
        return at is not None

    def _levels(self, kind: TokenUsageKind) -> Iterator[tuple[SpendBook, TokenUsageKind]]:
        """This book and every book enclosing it, each with the kind it files *kind* as, where it binds."""
        book: SpendBook | None = self
        while book is not None:
            if book.binds(kind):
                yield book, kind
            book, kind = book.enclosing, _ENCLOSED

    def _move(self, kind: TokenUsageKind, held: SendBound, bound: SendBound, sign: int) -> None:
        self._held_usd[kind] = self._held_usd.get(kind, 0.0) + sign * (held.usd or 0.0)
        self._held_tokens[kind] = self._held_tokens.get(kind, 0) + sign * (held.tokens or 0)
        self._out_usd[kind] = self._out_usd.get(kind, 0.0) + sign * (bound.usd or 0.0)
        self._out_tokens[kind] = self._out_tokens.get(kind, 0) + sign * (bound.tokens or 0)

    def refuse_in_chain(
        self, held: SendBound, bound: SendBound, kind: TokenUsageKind, what: str
    ) -> None:
        for book, _ in self._levels(kind):
            book.refuse_unless_room(held, bound, what)

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
            "tokens (`budget: {usd: null, tokens: N}`); then `resume`.",
            category=ErrorCategory.NO_RATE,
        )

    def _refusal(self, category: ErrorCategory, reserve: bool, each: SendBound, what: str) -> str:
        limit = "account reserve" if reserve else "cycle's share of the campaign ceiling"
        bounds = self.reserve if reserve else self.allotment
        if category is ErrorCategory.SPEND_CEILING:
            # Only a bounded axis refuses, so the cap it names is set.
            cap = bounds.usd_amount
            out = sum((self._out_usd if reserve else self._held_usd).values())
            owed = [
                (self.usd_metered, CEILING_METER_LABELS[self.meters]),
                (self.usd_unreported, "unreported"),
                (out, "in calls still out"),
                (self.set_aside_usd, "set aside for the bench"),
            ]
            left = cap - sum(usd for usd, _ in owed)
            said = ", ".join(f"${usd:.4f} {name}" for usd, name in owed if usd)
            if left <= 0 or each.usd is None:
                return f"{what} was not sent: the ${cap:.2f} {limit} is used up ({said})"
            return (
                f"{what} was not sent: it may cost up to ${each.usd:.4f} and the "
                f"${cap:.2f} {limit} has ${left:.4f} left ({said})"
            )
        if each.tokens is None:
            return f"{what}: nothing caps its reply, so it cannot run under a token ceiling"
        out_t = self._out_tokens if reserve else self._held_tokens
        return (
            f"{what} holds {each.tokens:,} tokens while it is out, and the "
            f"{bounds.token_amount:,}-token {limit} holds {self.tokens_spent:,} spent, "
            f"{self.tokens_unreported:,} unreported, {sum(out_t.values()):,} for calls out and "
            f"{self.set_aside_tokens:,} set aside for the bench"
        )

    def release(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        for book, filed_as in self._levels(kind):
            book._move(filed_as, held, bound, -1)

    def unpriced(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        self.release(held, bound, kind)
        for book, _ in self._levels(kind):
            book.usd_unreported += bound.usd or 0.0

    def unreported(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        self.release(held, bound, kind)
        self.keep_held(bound, kind)

    def keep_held(self, bound: SendBound, kind: TokenUsageKind) -> None:
        for book, _ in self._levels(kind):
            book.usd_unreported += bound.usd or 0.0
            book.tokens_unreported += bound.tokens or 0

    def prices_sends(self) -> bool:
        return self.allotment.usd is not None or self.reserve.usd is not None

    def money_left(self) -> Budget:
        """What the allotment still admits past spend and holds: an enclosed run's whole ceiling."""
        allotment = self.allotment
        return Budget(
            usd=None
            if allotment.usd is None
            else max(0.0, allotment.usd - self._usd_used() - sum(self._held_usd.values())),
            tokens=None
            if allotment.tokens is None
            else max(0, allotment.tokens - self._tokens_used() - sum(self._held_tokens.values())),
        )


def unbounded_spend_book() -> SpendBook:
    return SpendBook(
        declared=Budget(),
        hold=Hold(Budget(), Budget()),
        meters="bill",
        opened=time.monotonic(),
    )


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
    billing_ledger(what)
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
        self.billed: Budget | None = NOTHING_METERED

    def closed(self, bill: Budget | None) -> None:
        self.billed = None if bill is None or self.billed is None else self.billed.plus(bill)

    def draw(self, held: SendBound, bound: SendBound, what: str = "a send") -> None:
        """The rest is admitted against the ceiling: it stops a cell, never the estimate."""
        held_usd = min(self.held_usd, held.usd or 0.0)
        held_tokens = min(self.held_tokens, held.tokens or 0)
        usd, tokens = min(self.usd, bound.usd or 0.0), min(self.tokens, bound.tokens or 0)
        short_held = _amount((held.usd or 0.0) - held_usd, (held.tokens or 0) - held_tokens)
        short = _amount((bound.usd or 0.0) - usd, (bound.tokens or 0) - tokens)
        if short_held.usd or short_held.tokens or short.usd or short.tokens:
            self.book.refuse_in_chain(
                short_held, short, self.kind, f"{what}, past its cell's reservation"
            )
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
    book.hold(held, bound, label.kind, what=label.node, reservation=_RESERVATION.get())
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
            book.learn(label, reservation.billed)


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
        # A nested run's holds and bills land on its ROOT's ledger, which the account banks.
        self._root = book.root()
        self._unseen: list[TokenUsageRecord] = []
        self._unlanded: list[tuple[float | None, int]] = []
        self.closed = False
        # What the send closed at, once every part of it was priced; ``None`` until then.
        self.bill: Budget | None = None
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
            ledger=self._root.ledger,
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
            mirrored=self._root is not self._book,
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
        if self._root is not self._book:
            self._root.mirror(record, hold_id=self._hold_id)
        if not self._book.saw(record):
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
                ledger=self._root.ledger,
            )
            self._book.keep_held(unknown, self._label.kind)
        self._finish()
        if self._priced:
            self._book.release(self._held, self._bound, self._label.kind)
            bill = Budget.amount(self._billed_usd, self._billed_tokens)
            self._book.learn(self._label, bill)
            if unknown is None:
                self.bill = bill
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
    what = f"{label.node} on {model}" if model else label.node
    held = book.held_at(label, bound)
    drew = book.hold(held, bound, label.kind, what=what, reservation=reservation)
    admission = Admission(book, label, bound, held, model=model, provider=provider)
    try:
        yield admission
    finally:
        if not admission.closed:
            admission.unreported()
        if drew and reservation is not None:
            reservation.closed(admission.bill)


class Unreported(NamedTuple):
    usd: float
    tokens: int
    sends: int
    dearest: tuple[tuple[CallLabel, float, int], ...] = ()


class _Trail(NamedTuple):
    open: dict[str, SpendHoldRecord]
    unpriced: tuple[float | None, ...]
    dearest: tuple[tuple[CallLabel, float, int], ...]


class _HoldTrail:
    records: ClassVar[RecordClasses] = HOLD_TRAIL

    def __init__(self) -> None:
        self._held = HeldSends()
        self._labels: dict[str, CallLabel] = {}
        self._bills: dict[str, tuple[float, int]] = {}

    def feed(self, offset: int, record: HoldTrailRecord) -> None:
        self._held.track(record)
        if isinstance(record, SpendHoldRecord):
            self._labels[record.hold_id] = CallLabel(record.node, record.kind)
            return
        hold_id, usd = record.hold_id, record.bill_or_rate_usd
        if hold_id is not None and hold_id in self._labels and usd is not None:
            usd_was, tokens_was = self._bills.get(hold_id, (0.0, 0))
            billed = record.input_tokens + record.output_tokens
            self._bills[hold_id] = (usd_was + usd, tokens_was + billed)

    def value(self) -> _Trail:
        unpriced = self._held.unpriced
        return _Trail(
            dict(self._held.open),
            tuple(unpriced.values()),
            tuple(
                (self._labels[hold_id], usd, tokens)
                for hold_id, (usd, tokens) in self._bills.items()
                if hold_id not in unpriced
            ),
        )


_TRAIL_FOLDS = (_HoldTrail,)


def unreported_on(ledger: CycleEventLog) -> Unreported:
    """``store/account_spend.py`` cannot see ``_OUT``; it asks the run's phase for liveness."""
    # The ledger's OWN lines only: walking a fork chain holds the parent's open sends twice.
    trail = LedgerIndex.of(ledger.path, _TRAIL_FOLDS).view(_HoldTrail)
    left = [hold for hold_id, hold in trail.open.items() if hold_id not in _OUT]
    tokens = sum(held_tokens(hold) for hold in left)
    # A hold with no bound was admitted where no ceiling stood, and names no money to keep.
    bounds = (*(hold.cost_usd for hold in left), *trail.unpriced)
    usd = sum(bound for bound in bounds if bound is not None)
    return Unreported(usd, tokens, len(left) + len(trail.unpriced), trail.dearest)
