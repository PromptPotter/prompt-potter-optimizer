"""The spend book — where a paid request is ADMITTED, before it is sent.

A ceiling compared against spend already recorded is a guess about the calls still out: every call
in flight, every retry, and every call whose record a cancel lost lands past it. The book instead
HOLDS each send's worst case from admission until its usage record lands, and refuses a send whose
worst case does not fit beside everything already held — so recorded spend plus every call still
out never passes the cap, however much runs at once.

Four rules, each the shape of an overshoot it replaced:

- **A send is admitted or refused, never queued** (:meth:`SpendBook.hold`). A wait inside a cell
  spends the cell's wall clock, and a halt would come to depend on budget pressure.
- **The book counts every usage record its cycle ledger carries** — it subscribes to it — whoever
  wrote the record. One that reached another ledger, or none, it counts itself.
- **A send that ended without reporting is charged its whole bound** (:func:`admitted`), as an
  ``unsettled`` record on the ledger: cancelled, timed out, failed after the request left. Whatever
  sums the ledger, a resumed run included, never under-reads what the provider may have billed.
- **A send the provider provably never billed is released** — a 429, a connection never made, a
  request refused before any generation.
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
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx

from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.spend import TokenAccount, TokenUsageKind
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    emit_spend_hold,
    emit_token_usage,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.errors import ErrorCategory, WalletExhaustedError

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
    "admitted",
    "bind_spend_book",
    "bound_spend_book",
    "charge_open_holds",
    "may_have_billed",
    "never_sent",
    "reset_spend_book",
    "spending_under",
    "unbounded_spend_book",
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
    """One run's spend and what its calls out hold against its ceilings. The caps are re-read on
    every question, so a ceiling moved mid-run binds the next send."""

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
        # What the calls out hold, by who holds it — a scheduler counts its own cells itself.
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._seen: TokenUsageRecord | None = None
        # The run's own ledger and round, where a run armed this book. A nested run spends under
        # its ROOT's book — every level of the recursion against one ceiling — and its charges are
        # carried onto this ledger as they settle (:meth:`mirror`), holds included.
        self.ledger: CycleEventLog | None = None
        self.round_now: Callable[[], int | None] = lambda: None

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self._seen = record
        self.count(record)

    def count(self, record: TokenUsageRecord) -> None:
        if not record.cached:
            self.usd_spent += record.cost_usd or 0.0
            self.tokens_spent += record.input_tokens + record.output_tokens

    def saw(self, record: TokenUsageRecord) -> bool:
        """Whether ``record`` reached this book through its ledger — asked right after the append."""
        return record is self._seen

    def foreign(self, ledger: CycleEventLog | None) -> bool:
        """Whether a call recorded on ``ledger`` is a nested run's, to be carried onto this one."""
        return self.ledger is not None and ledger is not self.ledger

    def mirror(self, record: TokenUsageRecord, *, hold_id: str) -> bool:
        """Carry a nested run's settled call onto this book's ledger — the backend spend of the run
        that measured with it, in that run's round — and say whether it landed there."""
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
        """The ceiling already reached, if any."""
        if (cap := self.usd_cap()) is not None and self.usd_spent >= cap:
            return ErrorCategory.SPEND_CEILING
        if (cap_t := self.tokens_cap()) is not None and self.tokens_spent >= cap_t:
            return ErrorCategory.TOKEN_CEILING
        return None

    def _room(
        self, bound: SendBound, beside: TokenUsageKind | None
    ) -> Iterator[tuple[ErrorCategory, int]]:
        if (cap := self.usd_cap()) is not None:
            held = sum(v for k, v in self._held_usd.items() if k != beside)
            yield ErrorCategory.SPEND_CEILING, _times(cap - self.usd_spent - held, bound.usd)
        if (cap_t := self.tokens_cap()) is not None:
            held_t = sum(v for k, v in self._held_tokens.items() if k != beside)
            yield (
                ErrorCategory.TOKEN_CEILING,
                _times(cap_t - self.tokens_spent - held_t, bound.tokens),
            )

    def fits(self, bound: SendBound, *, beside: TokenUsageKind | None = None) -> int:
        """How many more sends of ``bound`` the ceilings admit beside everything already held —
        or, with ``beside``, beside everything but that bucket's holds, for a caller that counts
        its own calls out itself."""
        return min((n for _, n in self._room(bound, beside)), default=sys.maxsize)

    def hold(self, bound: SendBound, kind: TokenUsageKind, *, what: str) -> None:
        for category, n in self._room(bound, None):
            if n < 1:
                raise WalletExhaustedError(self._refusal(category, bound, what), category=category)
        self._held_usd[kind] = self._held_usd.get(kind, 0.0) + (bound.usd or 0.0)
        self._held_tokens[kind] = self._held_tokens.get(kind, 0) + (bound.tokens or 0)

    def _refusal(self, category: ErrorCategory, bound: SendBound, what: str) -> str:
        if category is ErrorCategory.SPEND_CEILING:
            if bound.usd is None:
                return f"{what}: no rate bounds what it may cost, so it cannot run under a ceiling"
            return (
                f"{what} may cost up to ${bound.usd:.4f}, and the ${self.usd_cap():.4f} ceiling "
                f"holds ${self.usd_spent:.4f} spent and ${sum(self._held_usd.values()):.4f} for "
                "calls out"
            )
        if bound.tokens is None:
            return f"{what}: nothing caps its reply, so it cannot run under a token ceiling"
        return (
            f"{what} may use up to {bound.tokens:,} tokens, and the {self.tokens_cap():,}-token "
            f"ceiling holds {self.tokens_spent:,} spent and {sum(self._held_tokens.values()):,} "
            "for calls out"
        )

    def release(self, bound: SendBound, kind: TokenUsageKind) -> None:
        self._held_usd[kind] -= bound.usd or 0.0
        self._held_tokens[kind] -= bound.tokens or 0

    def charge(self, bound: SendBound) -> None:
        """Count a whole bound as spent — the call's usage reached no ledger this book reads."""
        self.usd_spent += bound.usd or 0.0
        self.tokens_spent += bound.tokens or 0


def unbounded_spend_book() -> SpendBook:
    """A book for a call path no ceiling binds yet — it refuses nothing and still charges a send
    that never reported."""
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


class Billed(NamedTuple):
    """What a reply says one billed part of its send used — the usage record that settles it. A
    call billed in parts (one node of a cell each) names each part's own node and provider."""

    usage: TokenAccount
    cost_usd: float | None
    served_by: str | None = None
    model: str | None = None
    node: str | None = None
    provider: str | None = None
    duration_s: float | None = None


class Admission:
    """One admitted send, its hold already on the ledger. :meth:`settle` closes it with what the
    call used; :meth:`release` when the provider provably billed nothing. Left open,
    :func:`admitted` charges it in full."""

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
        self._missing = False
        self._unseen: list[TokenUsageRecord] = []
        self.closed = False
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

    def _emit(self, part: Billed, *, unsettled: bool = False) -> None:
        record = emit_token_usage(
            node=part.node or self._label.node,
            kind=self._label.kind,
            usage=part.usage,
            duration_s=(
                time.monotonic() - self._started if part.duration_s is None else part.duration_s
            ),
            model=part.model or self._model,
            provider=part.provider or self._provider,
            served_by=part.served_by,
            cost_usd=part.cost_usd,
            unsettled=unsettled,
            hold_id=self._hold_id,
            mirrored=self._foreign,
        )
        if record is None:
            self._missing = True
        elif self._foreign:
            if not self._book.mirror(record, hold_id=self._hold_id):
                self._unseen.append(record)
        elif not self._book.saw(record):
            self._unseen.append(record)

    def settle(self, *parts: Billed) -> None:
        """Close with what each billed part of the call used; a call that reports no part billed
        nothing, and says so on the record that settles its hold."""
        for part in parts or (Billed(TokenAccount(), 0.0),):
            self._emit(part)
        self._close()

    def release(self) -> None:
        # Settled at nothing, so a resumed run does not charge the hold as one a kill left open.
        self._emit(Billed(TokenAccount(), 0.0))
        self.closed = True
        self._book.release(self._bound, self._label.kind)

    def charge_in_full(self) -> None:
        bound = self._bound
        usage = TokenAccount(input=bound.input_tokens, output=bound.output_tokens or 0)
        self._emit(Billed(usage, bound.usd), unsettled=True)
        self._close()

    def _close(self) -> None:
        self.closed = True
        self._book.release(self._bound, self._label.kind)
        if self._missing:
            # A part reached no ledger, so its cost is known nowhere: the whole bound is charged.
            self._book.charge(self._bound)
        else:
            for record in self._unseen:
                self._book.count(record)


def charge_open_holds(ledger: CycleEventLog) -> int:
    """Charge, in full, every hold this ledger opened and nothing settled — a run killed with its
    calls out — and say how many. Its own lines only: an inherited prefix is its owner's."""
    open_holds: dict[str, dict[str, Any]] = {}
    for rec in iter_jsonl(ledger.path, record_types=_HOLD_TRAIL):
        if rec.get("record_type") == "spend_hold":
            open_holds[str(rec.get("hold_id"))] = rec
        elif (hold_id := rec.get("hold_id")) is not None:
            open_holds.pop(str(hold_id), None)
    for hold_id, rec in open_holds.items():
        ledger.append(
            TokenUsageRecord(
                kind=rec["kind"],
                node=rec["node"],
                model=rec.get("model"),
                provider=rec.get("provider"),
                input_tokens=int(rec.get("input_tokens", 0)),
                output_tokens=int(rec.get("output_tokens", 0)),
                cost_usd=rec.get("cost_usd"),
                unsettled=True,
                hold_id=hold_id,
                round=rec.get("round"),
            )
        )
    return len(open_holds)


_HOLD_TRAIL = frozenset({"spend_hold", "token_usage"})


@contextmanager
def admitted(
    label: CallLabel, bound: SendBound, *, model: str | None, provider: str | None
) -> Iterator[Admission]:
    """Hold ``bound`` for one send, or refuse it with :class:`WalletExhaustedError`. The block
    closes the admission; one it leaves open — the send raised or was cancelled — is charged in
    full."""
    book = _BOOK.get()
    if book is None:
        raise RuntimeError(
            f"{label.node}: no spend book is bound, and a paid call is never sent unadmitted"
        )
    book.hold(bound, label.kind, what=f"{label.node} on {model}" if model else label.node)
    admission = Admission(book, label, bound, model=model, provider=provider)
    try:
        yield admission
    finally:
        if not admission.closed:
            admission.charge_in_full()
