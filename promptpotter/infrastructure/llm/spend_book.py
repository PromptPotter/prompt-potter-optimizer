"""The spend book — where a paid send is ADMITTED, before it leaves, and what a run has spent.

Three facts, one record each, and none is ever written as another:

- **A bill** (``TokenUsageRecord``) is what a provider REPORTED one response cost, written at the
  send that incurred it. It is the only thing any surface calls spent.
- **A hold** (``SpendHoldRecord``) is a send admitted, written before it leaves and naming the
  most it may cost. Its bill closes it.
- **An unreported send** is a hold no bill closed: the request left and nobody learned its price —
  cancelled, timed out, killed. It writes NOTHING. It binds the ceiling at its bound, like a send
  still out, and no reader sums it as spent.

Four rules, each the shape of an overshoot or a fiction it replaced:

- **A send is admitted or refused, never queued** (:meth:`SpendBook.hold`). A wait inside a cell
  spends the cell's wall clock, and a halt would come to depend on budget pressure.
- **The book counts every bill its cycle ledger carries** — it subscribes to it — whoever wrote
  it. A bill that reached another ledger, or none, it counts itself.
- **A ceiling reads spent + unreported + held**, and a resumed run starts holding every send its
  ledger left open (:func:`unreported_on`). **There are two, and a send out holds a different
  amount against each.** The run's CEILING is where it stops: a send holds what the dearest such
  send has billed (:meth:`SpendBook.held_at`), so the ceiling bounds money and never how many run
  at once, and bills may pass it by what the sends then out bill over that. The account's RESERVE
  is what those bills may never pass: a send holds the most it may cost. It is ``None`` where no
  account binds, and equal to the ceiling it makes the ceiling a concurrency limit — a cell bills
  far under its bound, so a cap a few bounds wide walks one call at a time with money left.
- **A send the provider provably never billed is released** — closed with a bill of nothing: a
  429, a connection never made, a request refused before any generation.

A cell whose sends are each admitted where they are made RESERVES the run it DECLARES instead
(:func:`reserved`): it starts only where that fits, and every send inside draws its hold from the
reservation rather than from the room left — so the episode it declared cannot be refused halfway
through. **Not its worst case.** A reservation covering every retry that could ever fire, all at
once, prices one agent cell at three orders of magnitude over what such cells measure, and a
ceiling then affords exactly one of them at a time: the run goes serial with every other reading
saying it should not. What the reservation does not cover is admitted against the ceiling like any
other send, so the thing that stops a cell mid-way is the money running out, never the estimate
being wrong.
"""

from __future__ import annotations

import logging
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import httpx

from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.spend import (
    NESTED_NODE_PREFIX,
    SEARCH_KINDS,
    CeilingMeter,
    TokenAccount,
    TokenUsageKind,
)
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    bill_usd,
    emit_spend_hold,
    emit_token_usage,
    filed_kind,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.read_model import HOLD_TRAIL, iter_jsonl, open_holds
from promptpotter.shared.errors import ErrorCategory, SendRefusedError

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = [
    "FRAMING_TOKENS",
    "Admission",
    "Billed",
    "CallLabel",
    "SendBound",
    "SpendBook",
    "Unreported",
    "admitted",
    "bind_spend_book",
    "bound_spend_book",
    "connection_broke",
    "filed",
    "may_have_billed",
    "never_sent",
    "reservation_left",
    "reserved",
    "reset_spend_book",
    "spending_under",
    "unbounded_spend_book",
    "unreported_on",
]


@dataclass(frozen=True)
class CallLabel:
    """Whose call a send is: the node its usage record names, and the bucket it bills."""

    node: str
    kind: TokenUsageKind


@dataclass(frozen=True)
class SendBound:
    """The most one send can cost. ``output_tokens`` is ``None`` where nothing caps the reply, and
    ``usd`` where no rate prices the call — either refuses the send under the ceiling it leaves
    unbounded, and neither refuses it where no ceiling is set."""

    input_tokens: int
    output_tokens: int | None
    usd: float | None

    @property
    def tokens(self) -> int | None:
        return None if self.output_tokens is None else self.input_tokens + self.output_tokens


# Tokens a provider may add around a request's own text — chat-template markers, a schema it
# renders into the prompt — beyond what the request's bytes already count.
FRAMING_TOKENS = 512


def never_sent(exc: BaseException) -> bool:
    """Whether a failed request provably never left — a connection never made."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def connection_broke(exc: BaseException) -> bool:
    """Whether a request that left lost its connection before any reply — a reset, a TLS record
    fault, a protocol break. Never a timeout: there the provider may still be generating."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, httpx.TimeoutException | TimeoutError):
            return False
        if isinstance(
            seen,
            httpx.ReadError
            | httpx.WriteError
            | httpx.RemoteProtocolError
            | ssl.SSLError
            | ConnectionError,
        ):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def may_have_billed(status: int | None, exc: BaseException | None = None) -> bool:
    """Whether a failed send may have cost anything. One refused before generation — any 4xx but a
    timeout — and one that never left did not; a timeout, a 5xx, or a connection lost once the
    request was out may have."""
    if status is not None:
        return status == 408 or status >= 500
    return exc is None or not never_sent(exc)


def _times(left: float, each: float | None) -> int:
    """How many sends costing ``each`` fit in ``left`` — none that nothing bounds."""
    if each is None or left < 0:
        return 0
    if each <= 0:
        return sys.maxsize
    return int(left / each)


class SpendBook(Projection):
    """One run's bills, its unreported sends, and what its sends out hold against its limits —
    each re-read on every question, so one moved mid-run binds the next send."""

    def __init__(
        self,
        *,
        usd_cap: Callable[[], float | None],
        tokens_cap: Callable[[], int | None],
        usd_reserve: Callable[[], float | None],
        tokens_reserve: Callable[[], int | None],
        meters: CeilingMeter,
        usd_spent: float = 0.0,
        tokens_spent: int = 0,
    ) -> None:
        self.usd_cap = usd_cap
        self.tokens_cap = tokens_cap
        self.usd_reserve = usd_reserve
        self.tokens_reserve = tokens_reserve
        self.meters = meters
        # The dearest bill each node's sends have closed with, in each unit — by node, since the
        # bucket a send files under changes who pays for it and not what it costs.
        self._billed_most: dict[str, tuple[float, int]] = {}
        self.usd_spent = usd_spent
        self.tokens_spent = tokens_spent
        # Sends that ended with no bill, at the bound each was admitted on — see the module header.
        self.usd_unreported = 0.0
        self.tokens_unreported = 0
        # What the sends out and the cells reserved hold against the ceilings, and the most they
        # may cost, by who holds it — a scheduler counts its own cells itself.
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._out_usd: dict[TokenUsageKind, float] = {}
        self._out_tokens: dict[TokenUsageKind, int] = {}
        # What the run keeps back for the bench's pass after the search, so the search stops short
        # of the ceiling by that much and the pass still fits under it.
        self.set_aside_usd = 0.0
        self.set_aside_tokens = 0
        self._seen: TokenUsageRecord | None = None
        # The run's own ledger and round, where a run armed this book. A nested run spends under
        # its ROOT's book — every level of the recursion against one ceiling — and its bills are
        # carried onto this ledger as they land (:meth:`mirror`), holds included.
        self.ledger: CycleEventLog | None = None
        self.round_now: Callable[[], int | None] = lambda: None

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self._seen = record
        self.count(record)

    def binds(self, kind: TokenUsageKind) -> bool:
        """Whether a send of ``kind`` spends against these ceilings; a bench pass under a
        ``search_incurred`` book is metered beside them."""
        return self.meters == "bill" or kind in SEARCH_KINDS

    def count(self, record: TokenUsageRecord) -> None:
        # A replay is priced into the USD arm only where the ceiling is the search's incurred cost.
        replay_usd = self.meters == "search_incurred"
        self._spend(
            record.kind,
            record.cost_usd if not record.cached or replay_usd else None,
            0 if record.cached else record.input_tokens + record.output_tokens,
        )

    def _spend(self, kind: TokenUsageKind, usd: float | None, tokens: int) -> None:
        if not self.binds(kind):
            return
        self.usd_spent += usd or 0.0
        self.tokens_spent += tokens

    def set_aside(self, usd: float, tokens: int) -> None:
        self.set_aside_usd = usd
        self.set_aside_tokens = tokens

    def saw(self, record: TokenUsageRecord) -> bool:
        """Whether ``record`` reached this book through its ledger — asked right after the append."""
        return record is self._seen

    def foreign(self, ledger: CycleEventLog | None) -> bool:
        """Whether a call recorded on ``ledger`` is a nested run's, to be carried onto this one."""
        return self.ledger is not None and ledger is not self.ledger

    def mirror(self, record: TokenUsageRecord, *, hold_id: str) -> bool:
        """Carry a nested run's bill onto this book's ledger — the backend spend of the run that
        measured with it, in that run's round — and say whether it landed there."""
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
        """The ceiling already reached, if any — by bills, by sends whose bill never came, or by
        what is set aside."""
        usd_used = self.usd_spent + self.usd_unreported + self.set_aside_usd
        if (cap := self.usd_cap()) is not None and usd_used >= cap:
            return ErrorCategory.SPEND_CEILING
        tokens_used = self.tokens_spent + self.tokens_unreported + self.set_aside_tokens
        cap_t = self.tokens_cap()
        if cap_t is not None and tokens_used >= cap_t:
            return ErrorCategory.TOKEN_CEILING
        return None

    def _room(
        self, held: SendBound, bound: SendBound, beside: TokenUsageKind | None
    ) -> Iterator[tuple[ErrorCategory, bool, int]]:
        """How many more such sends each limit admits, a reserve's flagged: ``held`` each against
        a ceiling, ``bound`` each against a reserve."""
        used = self.usd_spent + self.usd_unreported + self.set_aside_usd
        for reserve, limit, out, each in (
            (False, self.usd_cap(), self._held_usd, held.usd),
            (True, self.usd_reserve(), self._out_usd, bound.usd),
        ):
            if limit is not None:
                left = limit - used - sum(v for k, v in out.items() if k != beside)
                yield ErrorCategory.SPEND_CEILING, reserve, _times(left, each)
        used_t = self.tokens_spent + self.tokens_unreported + self.set_aside_tokens
        for reserve, limit_t, out_t, each_t in (
            (False, self.tokens_cap(), self._held_tokens, held.tokens),
            (True, self.tokens_reserve(), self._out_tokens, bound.tokens),
        ):
            if limit_t is not None:
                left_t = limit_t - used_t - sum(v for k, v in out_t.items() if k != beside)
                yield ErrorCategory.TOKEN_CEILING, reserve, _times(left_t, each_t)

    def held_at(self, label: CallLabel, bound: SendBound) -> SendBound:
        """What a send of ``label`` holds against a CEILING: the dearest such send has billed, and
        ``bound`` until one has. An unpriced or uncapped send keeps its bound, and its refusal."""
        most = self._billed_most.get(label.node)
        if most is None or bound.usd is None or bound.tokens is None:
            return bound
        return SendBound(
            input_tokens=0,
            output_tokens=min(bound.tokens, most[1]),
            usd=min(bound.usd, most[0]),
        )

    def learn(self, label: CallLabel, usd: float, tokens: int) -> None:
        """One send of ``label`` closed with this bill. A bill of nothing teaches nothing: it is
        a send the provider never ran."""
        if usd <= 0.0 and tokens <= 0:
            return
        was = self._billed_most.get(label.node, (0.0, 0))
        self._billed_most[label.node] = (max(was[0], usd), max(was[1], tokens))

    def fits(
        self, held: SendBound, bound: SendBound, *, beside: TokenUsageKind | None = None
    ) -> int:
        """How many more such sends the ceilings and the reserves admit beside everything out —
        with ``beside``, beside all but that bucket's, for one that counts its own calls out."""
        return min((n for _, _, n in self._room(held, bound, beside)), default=sys.maxsize)

    def binding(
        self, held: SendBound, bound: SendBound, *, beside: TokenUsageKind | None = None
    ) -> SendBound:
        """What one such send holds against the limit that admits fewest of them."""
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
        """Hold a send out of the room left, refusing one that does not fit — or out of
        ``drawn_from``, its cell's reservation, whose shortfall is refused like any other send."""
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
        """The ONE refusal — ``held`` against every ceiling, ``bound`` against every reserve — so a
        send out of the room left and one past its cell's reservation cannot come to disagree."""
        for category, reserve, n in self._room(held, bound, None):
            if n < 1:
                raise SendRefusedError(
                    self._refusal(category, reserve, bound if reserve else held, what),
                    category=category,
                )

    def _refusal(self, category: ErrorCategory, reserve: bool, each: SendBound, what: str) -> str:
        limit = "account reserve" if reserve else "ceiling"
        if category is ErrorCategory.SPEND_CEILING:
            if each.usd is None:
                return f"{what}: no rate bounds what it may cost, so it cannot run under a ceiling"
            unreported = (
                f", ${self.usd_unreported:.4f} unreported (sends whose bill never came)"
                if self.usd_unreported
                else ""
            )
            cap = self.usd_reserve() if reserve else self.usd_cap()
            out = self._out_usd if reserve else self._held_usd
            return (
                f"{what} holds ${each.usd:.4f} while it is out, and the ${cap:.4f} {limit} "
                f"holds ${self.usd_spent:.4f} spent{unreported}, "
                f"${sum(out.values()):.4f} for calls out and "
                f"${self.set_aside_usd:.4f} set aside for the bench"
            )
        if each.tokens is None:
            return f"{what}: nothing caps its reply, so it cannot run under a token ceiling"
        cap_t = self.tokens_reserve() if reserve else self.tokens_cap()
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

    def unreported(self, held: SendBound, bound: SendBound, kind: TokenUsageKind) -> None:
        """A send out ended with no bill: its ``bound`` stays held, against the ceiling and the
        reserve alike, as one whose price nobody learned — no bill will ever say less."""
        if not self.binds(kind):
            return
        self.release(held, bound, kind)
        self.usd_unreported += bound.usd or 0.0
        self.tokens_unreported += bound.tokens or 0


def unbounded_spend_book() -> SpendBook:
    """A book for a call path no ceiling binds yet — it refuses nothing and still counts every
    bill and every unreported send."""
    return SpendBook(
        usd_cap=lambda: None,
        tokens_cap=lambda: None,
        usd_reserve=lambda: None,
        tokens_reserve=lambda: None,
        meters="bill",
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
    """Admit every send made inside the block against ``book``."""
    token = _BOOK.set(book)
    try:
        yield book
    finally:
        _BOOK.reset(token)


def filed(label: CallLabel) -> CallLabel:
    """``label`` under the kind its block files it as (``telemetry.filed_as``) — so a send is
    admitted in the bucket its bill will land in, and a bench pass is never held as search."""
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
    """A cell's worst case, held in memory while the cell runs — no ledger record, since it is no
    send. Each send inside draws its ceiling hold from ``held_*``, its bound from the rest."""

    def __init__(self, book: SpendBook, kind: TokenUsageKind, bound: SendBound) -> None:
        self.book = book
        self.kind = kind
        self.usd = self.held_usd = bound.usd or 0.0
        self.tokens = self.held_tokens = bound.tokens or 0

    def draw(self, held: SendBound, bound: SendBound, what: str = "a send") -> None:
        """Take what the reservation covers; admit the REST against the ceiling like any other
        send. A cell that outruns its estimate is stopped by the ceiling, never by the estimate —
        which is what lets a reservation be the episode as DECLARED rather than every retry it
        could ever need multiplied together."""
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
    """What the open reservation has left, as one send's bound — for a send inside a cell that
    nothing in this process can see into, whose worst case is therefore the cell's."""
    reservation = _RESERVATION.get()
    if reservation is None:
        raise RuntimeError("no cell reservation is open to bound this send")
    return _amount(reservation.usd, reservation.tokens)


@contextmanager
def reserved(label: CallLabel, bound: SendBound) -> Iterator[None]:
    """Reserve what one cell DECLARES it will run, or refuse the cell with
    :class:`SendRefusedError`. Every send admitted inside the block, in this task or one it starts,
    draws from the reservation; what is left of it is released when the block ends, however it
    ends, and what it cannot cover is admitted against the ceiling (:meth:`_Reservation.draw`)."""
    label = filed(label)
    book = _bound_book(label.node)
    book.hold(bound, bound, label.kind, what=label.node)
    reservation = _Reservation(book, label.kind, bound)
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


class Billed(NamedTuple):
    """What a reply says one billed part of its send used — the bill that closes it. A call billed
    in parts (one node of a cell each) names each part's own node and provider."""

    usage: TokenAccount
    cost_usd: float | None
    served_by: str | None = None
    model: str | None = None
    node: str | None = None
    provider: str | None = None
    duration_s: float | None = None


# Every hold a send of THIS process has out — so a scan of the ledger never reads one still
# waiting on its bill as one that ended without it.
_OUT: set[str] = set()


class Admission:
    """One admitted send, its hold already on the ledger. :meth:`settle` closes it with its bill;
    :meth:`release` when the provider provably billed nothing. Left open, :func:`admitted` closes
    it :meth:`unreported`."""

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
        self._model = model
        self._provider = provider
        self._started = time.monotonic()
        self._hold_id = uuid.uuid4().hex
        self._foreign = book.foreign(active_cycle_ledger())
        self._unseen: list[TokenUsageRecord] = []
        self._unlanded: list[tuple[float | None, int]] = []
        self.closed = False
        _OUT.add(self._hold_id)
        emit_spend_hold(
            hold_id=self._hold_id,
            node=label.node,
            kind=label.kind,
            input_tokens=bound.input_tokens,
            output_tokens=bound.output_tokens or 0,
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
            hold_id=self._hold_id,
            mirrored=self._foreign,
        )
        if record is None:
            # The bill is in hand; only its line is missing. It is counted here, at its price —
            # and said, since money on no ledger is money no account or campaign ever shows.
            usd = bill_usd(part.usage, model=model, provider=provider, cost_usd=part.cost_usd)
            self._unlanded.append((usd, part.usage.input + part.usage.output))
            self._billed_usd += usd or 0.0
            self._billed_tokens += part.usage.input + part.usage.output
            logger.warning(
                "%s: a bill of $%s reached no ledger; only this run's book counts it",
                part.node or self._label.node,
                "?" if usd is None else f"{usd:.6f}",
            )
            return
        self._billed_usd += record.cost_usd or 0.0
        self._billed_tokens += record.input_tokens + record.output_tokens
        if self._foreign:
            if not self._book.mirror(record, hold_id=self._hold_id):
                self._unseen.append(record)
        elif not self._book.saw(record):
            self._unseen.append(record)

    def settle(self, *parts: Billed) -> None:
        """Close with what each billed part of the call used; a call that reports no part billed
        nothing, and says so on the bill that closes its hold."""
        for part in parts or (Billed(TokenAccount(), 0.0),):
            self._emit(part)
        self._finish()
        self._book.release(self._held, self._bound, self._label.kind)
        self._book.learn(self._label, self._billed_usd, self._billed_tokens)
        for record in self._unseen:
            self._book.count(record)
        for usd, tokens in self._unlanded:
            self._book._spend(self._label.kind, usd, tokens)

    def release(self) -> None:
        # A bill of nothing, so no later scan reads the hold as a send that ended unreported.
        self.settle()

    def unreported(self) -> None:
        """Close with no bill — the send left and nothing reported what it cost. Nothing is
        written: its hold stays open on the ledger, and the book keeps holding its bound."""
        self._finish()
        self._book.unreported(self._held, self._bound, self._label.kind)

    def _finish(self) -> None:
        self.closed = True
        _OUT.discard(self._hold_id)


@contextmanager
def admitted(
    label: CallLabel, bound: SendBound, *, model: str | None, provider: str | None
) -> Iterator[Admission]:
    """Hold one send — ``held_at`` against the ceiling, ``bound`` against the reserve — drawn from
    its cell's reservation, else refused. An admission the block leaves open is unreported."""
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


class Unreported(NamedTuple):
    """Sends a ledger holds open that no send of this process still has out, at their bounds."""

    usd: float
    tokens: int
    sends: int


def unreported_on(ledger: CycleEventLog) -> Unreported:
    """What the sends this ledger left open may have cost — a run killed with its calls out, or
    one that ended them unreported. Its own lines only: an inherited prefix is its owner's.

    The LIVENESS half is this scope's own: a hold whose send this process still has out is not
    unreported, it is in flight. An account reading the same ledgers from outside cannot see
    ``_OUT`` and asks the run's phase instead (``store/account_spend.py``); the fold under both is
    one (:func:`~promptpotter.infrastructure.store.read_model.open_holds`)."""
    usd, tokens, sends = 0.0, 0, 0
    for hold_id, rec in open_holds(iter_jsonl(ledger.path, record_types=HOLD_TRAIL)).items():
        if hold_id in _OUT:
            continue
        usd += float(rec.get("cost_usd") or 0.0)
        tokens += int(rec.get("input_tokens", 0)) + int(rec.get("output_tokens", 0))
        sends += 1
    return Unreported(usd, tokens, sends)
