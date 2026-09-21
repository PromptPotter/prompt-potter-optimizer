"""The spend book — where a paid send is ADMITTED, before it leaves, and what a run has spent.

Three facts, one record each, and none is ever written as another:

- **A bill** (``TokenUsageRecord``) is what a provider REPORTED one response cost, written at the
  send that incurred it. It is the only thing any surface calls spent.
- **A hold** (``SpendHoldRecord``) is a send admitted at the most it may cost, written before it
  leaves. Its bill closes it.
- **An unreported send** is a hold no bill closed: the request left and nobody learned its price —
  cancelled, timed out, killed. It writes NOTHING. It binds the ceiling at its bound, like a send
  still out, and no reader sums it as spent.

Four rules, each the shape of an overshoot or a fiction it replaced:

- **A send is admitted or refused, never queued** (:meth:`SpendBook.hold`). A wait inside a cell
  spends the cell's wall clock, and a halt would come to depend on budget pressure.
- **The book counts every bill its cycle ledger carries** — it subscribes to it — whoever wrote
  it. A bill that reached another ledger, or none, it counts itself.
- **A ceiling reads spent + unreported + held** — so recorded bills plus every send whose bill is
  still unknown never pass the cap, however much runs at once, and a resumed run starts holding
  every send its ledger left open (:func:`unreported_on`).
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
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    bill_usd,
    emit_spend_hold,
    emit_token_usage,
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
    """One run's bills, its unreported sends, and what its sends out hold against its ceilings.
    The caps are re-read on every question, so a ceiling moved mid-run binds the next send."""

    def __init__(
        self,
        *,
        usd_cap: Callable[[], float | None],
        tokens_cap: Callable[[], int | None],
        usd_spent: float = 0.0,
        tokens_spent: int = 0,
    ) -> None:
        self.usd_cap = usd_cap
        self.tokens_cap = tokens_cap
        self.usd_spent = usd_spent
        self.tokens_spent = tokens_spent
        # Sends that ended with no bill, at the bound each was admitted on — see the module header.
        self.usd_unreported = 0.0
        self.tokens_unreported = 0
        # What the sends out and the cells reserved hold, by who holds it — a scheduler counts its
        # own cells itself.
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._seen: TokenUsageRecord | None = None
        # The run's own ledger and round, where a run armed this book. A nested run spends under
        # its ROOT's book — every level of the recursion against one ceiling — and its bills are
        # carried onto this ledger as they land (:meth:`mirror`), holds included.
        self.ledger: CycleEventLog | None = None
        self.round_now: Callable[[], int | None] = lambda: None

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self._seen = record
        self.count(record)

    def count(self, record: TokenUsageRecord) -> None:
        if not record.cached:
            self._spend(record.cost_usd, record.input_tokens + record.output_tokens)

    def _spend(self, usd: float | None, tokens: int) -> None:
        self.usd_spent += usd or 0.0
        self.tokens_spent += tokens

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
                "node": f"inner:{record.node}",
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
        """The ceiling already reached, if any — by bills, or by sends whose bill never came."""
        if (cap := self.usd_cap()) is not None and self.usd_spent + self.usd_unreported >= cap:
            return ErrorCategory.SPEND_CEILING
        cap_t = self.tokens_cap()
        if cap_t is not None and self.tokens_spent + self.tokens_unreported >= cap_t:
            return ErrorCategory.TOKEN_CEILING
        return None

    def _room(
        self, bound: SendBound, beside: TokenUsageKind | None
    ) -> Iterator[tuple[ErrorCategory, int]]:
        if (cap := self.usd_cap()) is not None:
            held = sum(v for k, v in self._held_usd.items() if k != beside)
            left = cap - self.usd_spent - self.usd_unreported - held
            yield ErrorCategory.SPEND_CEILING, _times(left, bound.usd)
        if (cap_t := self.tokens_cap()) is not None:
            held_t = sum(v for k, v in self._held_tokens.items() if k != beside)
            left_t = cap_t - self.tokens_spent - self.tokens_unreported - held_t
            yield ErrorCategory.TOKEN_CEILING, _times(left_t, bound.tokens)

    def fits(self, bound: SendBound, *, beside: TokenUsageKind | None = None) -> int:
        """How many more sends of ``bound`` the ceilings admit beside everything already held —
        or, with ``beside``, beside everything but that bucket's holds, for a caller that counts
        its own calls out itself."""
        return min((n for _, n in self._room(bound, beside)), default=sys.maxsize)

    def hold(
        self,
        bound: SendBound,
        kind: TokenUsageKind,
        *,
        what: str,
        drawn_from: _Reservation | None = None,
    ) -> None:
        """Hold ``bound`` — out of the room left, refusing a send that does not fit, or out of
        ``drawn_from``, the reservation of the cell the send is part of, which already fit. What
        that reservation cannot cover is refused here like any other send."""
        if drawn_from is None:
            self.refuse_unless_room(bound, what)
        else:
            drawn_from.draw(bound, what)
        self._held_usd[kind] = self._held_usd.get(kind, 0.0) + (bound.usd or 0.0)
        self._held_tokens[kind] = self._held_tokens.get(kind, 0) + (bound.tokens or 0)

    def refuse_unless_room(self, bound: SendBound, what: str) -> None:
        """Raise unless every ceiling admits ``bound`` beside everything already held. The ONE
        refusal, so a send out of the room left and a send past its cell's reservation cannot come
        to disagree about what the ceiling permits."""
        for category, n in self._room(bound, None):
            if n < 1:
                raise SendRefusedError(self._refusal(category, bound, what), category=category)

    def _refusal(self, category: ErrorCategory, bound: SendBound, what: str) -> str:
        if category is ErrorCategory.SPEND_CEILING:
            if bound.usd is None:
                return f"{what}: no rate bounds what it may cost, so it cannot run under a ceiling"
            unreported = (
                f", ${self.usd_unreported:.4f} unreported (sends whose bill never came)"
                if self.usd_unreported
                else ""
            )
            return (
                f"{what} may cost up to ${bound.usd:.4f}, and the ${self.usd_cap():.4f} ceiling "
                f"holds ${self.usd_spent:.4f} spent{unreported} and "
                f"${sum(self._held_usd.values()):.4f} for calls out"
            )
        if bound.tokens is None:
            return f"{what}: nothing caps its reply, so it cannot run under a token ceiling"
        return (
            f"{what} may use up to {bound.tokens:,} tokens, and the {self.tokens_cap():,}-token "
            f"ceiling holds {self.tokens_spent:,} spent, {self.tokens_unreported:,} unreported and "
            f"{sum(self._held_tokens.values()):,} for calls out"
        )

    def release(self, bound: SendBound, kind: TokenUsageKind) -> None:
        self._held_usd[kind] -= bound.usd or 0.0
        self._held_tokens[kind] -= bound.tokens or 0

    def unreported(self, bound: SendBound, kind: TokenUsageKind) -> None:
        """A send out ended with no bill: its hold stops being a send out and stays held as one
        whose price nobody learned."""
        self.release(bound, kind)
        self.usd_unreported += bound.usd or 0.0
        self.tokens_unreported += bound.tokens or 0


def unbounded_spend_book() -> SpendBook:
    """A book for a call path no ceiling binds yet — it refuses nothing and still counts every
    bill and every unreported send."""
    return SpendBook(usd_cap=lambda: None, tokens_cap=lambda: None)


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


def _bound_book(what: str) -> SpendBook:
    book = _BOOK.get()
    if book is None:
        raise RuntimeError(
            f"{what}: no spend book is bound, and a paid call is never sent unadmitted"
        )
    return book


class _Reservation:
    """A cell's worst case, held in memory for as long as the cell runs — no ledger record, since
    the cell is not a send. Each send inside draws its hold from what is left of it."""

    def __init__(self, book: SpendBook, kind: TokenUsageKind, bound: SendBound) -> None:
        self.book = book
        self.kind = kind
        self.usd = bound.usd or 0.0
        self.tokens = bound.tokens or 0

    def draw(self, bound: SendBound, what: str = "a send") -> None:
        """Take what the reservation covers; admit the REST against the ceiling like any other
        send. A cell that outruns its estimate is stopped by the ceiling, never by the estimate —
        which is what lets a reservation be the episode as DECLARED rather than every retry it
        could ever need multiplied together."""
        usd, tokens = min(self.usd, bound.usd or 0.0), min(self.tokens, bound.tokens or 0)
        short = SendBound(
            input_tokens=0,
            output_tokens=(bound.tokens or 0) - tokens,
            usd=(bound.usd or 0.0) - usd,
        )
        if short.usd or short.tokens:
            self.book.refuse_unless_room(short, f"{what}, past its cell's reservation")
        self.usd -= usd
        self.tokens -= tokens
        self.book.release(SendBound(input_tokens=0, output_tokens=tokens, usd=usd), self.kind)


_RESERVATION: ContextVar[_Reservation | None] = ContextVar("spend_reservation", default=None)


def reservation_left() -> SendBound:
    """What the open reservation has left, as one send's bound — for a send inside a cell that
    nothing in this process can see into, whose worst case is therefore the cell's."""
    reservation = _RESERVATION.get()
    if reservation is None:
        raise RuntimeError("no cell reservation is open to bound this send")
    return SendBound(input_tokens=0, output_tokens=reservation.tokens, usd=reservation.usd)


@contextmanager
def reserved(label: CallLabel, bound: SendBound) -> Iterator[None]:
    """Reserve what one cell DECLARES it will run, or refuse the cell with
    :class:`SendRefusedError`. Every send admitted inside the block, in this task or one it starts,
    draws from the reservation; what is left of it is released when the block ends, however it
    ends, and what it cannot cover is admitted against the ceiling (:meth:`_Reservation.draw`)."""
    book = _bound_book(label.node)
    book.hold(bound, label.kind, what=label.node)
    reservation = _Reservation(book, label.kind, bound)
    token = _RESERVATION.set(reservation)
    try:
        yield
    finally:
        _RESERVATION.reset(token)
        book.release(
            SendBound(input_tokens=0, output_tokens=reservation.tokens, usd=reservation.usd),
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
        *,
        model: str | None,
        provider: str | None,
    ) -> None:
        self._book = book
        self._label = label
        self._bound = bound
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
            logger.warning(
                "%s: a bill of $%s reached no ledger; only this run's book counts it",
                part.node or self._label.node,
                "?" if usd is None else f"{usd:.6f}",
            )
        elif self._foreign:
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
        self._book.release(self._bound, self._label.kind)
        for record in self._unseen:
            self._book.count(record)
        for usd, tokens in self._unlanded:
            self._book._spend(usd, tokens)

    def release(self) -> None:
        # A bill of nothing, so no later scan reads the hold as a send that ended unreported.
        self.settle()

    def unreported(self) -> None:
        """Close with no bill — the send left and nothing reported what it cost. Nothing is
        written: its hold stays open on the ledger, and the book keeps holding its bound."""
        self._finish()
        self._book.unreported(self._bound, self._label.kind)

    def _finish(self) -> None:
        self.closed = True
        _OUT.discard(self._hold_id)


@contextmanager
def admitted(
    label: CallLabel, bound: SendBound, *, model: str | None, provider: str | None
) -> Iterator[Admission]:
    """Hold ``bound`` for one send — drawn from the reservation of the cell it is part of, else
    refused with :class:`SendRefusedError` where it does not fit. The block closes the admission;
    one it leaves open — the send raised or was cancelled before its bill came back — is
    unreported."""
    book = _bound_book(label.node)
    reservation = _RESERVATION.get()
    drawn_from = (
        reservation
        if reservation is not None and reservation.book is book and reservation.kind == label.kind
        else None
    )
    what = f"{label.node} on {model}" if model else label.node
    book.hold(bound, label.kind, what=what, drawn_from=drawn_from)
    admission = Admission(book, label, bound, model=model, provider=provider)
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
