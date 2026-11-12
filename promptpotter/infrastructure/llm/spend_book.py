from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.domain.run_records import (
    Bill,
    CallPrice,
    CycleShareRecord,
    Hold,
    Release,
    TokenUsageRecord,
)
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    CYCLE_AXES,
    METER_KINDS,
    METER_PRICES_REPLAYS,
    MONEY_AXES,
    NESTED_NODE_PREFIX,
    Budget,
    CeilingMeter,
    KeySource,
    TokenAccount,
    TokenUsageKind,
    draws_coupon,
    metered,
    usd_text,
)
from promptpotter.infrastructure.llm.telemetry import (
    append_record,
    billing_ledger,
    call_price,
    campaign_priced,
    current_round,
    emit_spend_hold,
    filed_kind,
    token_usage_record,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.read_model import money_on
from promptpotter.shared.errors import (
    ErrorCategory,
    HostAllowanceExhaustedError,
    LaunchRefusalReason,
    SendRefusedError,
    is_provider_credit_refusal,
)
from promptpotter.shared.measurement_context import instrument_mode
from promptpotter.shared.run_scope import run_scope, scoped

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.infrastructure.store.wallet_store import ChargeBook

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


class LiveCycle(NamedTuple):
    """The cycle a live book re-reads at each send, through its store."""

    campaigns: CampaignStore
    hop: CycleHop

    def standing(self) -> Budget:
        return self.campaigns.read_standing_budget(self.hop).budget

    def share(self) -> Budget:
        """The most the cycle may have metered of its campaign's ceiling, which a reshare moves."""
        share = self.campaigns.last(self.hop, CycleShareRecord)
        if share is None:
            raise RuntimeError(f"no share landed on {self.hop} before its book opened")
        return share.allotment


class RunHold(NamedTuple):
    """A run's hold on its account's charges, which a set-limits restates: what the host's coupon
    holds back for it binds the sends on the box's key alone."""

    charges: ChargeBook
    hold_id: str

    def bound(self) -> Budget:
        return money_on(self.charges.path).holds[self.hold_id].bound


# What an enclosing book files a nested run's sends as, as its ledger's copy of them does.
_ENCLOSED: TokenUsageKind = "backend"


class SpendBook(Projection):
    def __init__(
        self,
        *,
        declared: Budget,
        share: Budget | LiveCycle,
        reserve: Budget | RunHold,
        hold_id: str | None,
        meters: CeilingMeter,
        opened: float,
        cycle: LiveCycle | None = None,
    ) -> None:
        # The launch's own axis (`wall_clock_s`); money binds on the share and the reserve.
        self.declared = declared.only(CYCLE_AXES)
        self.share = share
        self.reserve_of = reserve
        # The job's hold its calls draw on; ``None`` on a book no job holds.
        self.hold_id = hold_id
        self.cycle = cycle
        self.meters = meters
        # The launch's ``time.monotonic()``: a book re-armed mid-launch keeps its clock.
        self.opened = opened
        # The cycle's OWN, in the meter's units: never a figure to print as spent.
        self.usd_metered = 0.0
        self.tokens_spent = 0
        self.usd_unreported = 0.0
        self.tokens_unreported = 0
        # What this cycle metered that no host key paid (the account's own keys, replays): the
        # coupon's reserve never counts it.
        self.off_coupon_usd = 0.0
        self.off_coupon_tokens = 0
        # What the box's key paid for kinds the meter does not count (a Compare arm's bench): the
        # reserve binds every send on that key, so the root book counts it beside the meter.
        self.beside_usd = 0.0
        self.beside_tokens = 0
        self._held_usd: dict[TokenUsageKind, float] = {}
        self._held_tokens: dict[TokenUsageKind, int] = {}
        self._out_usd: dict[TokenUsageKind, float] = {}
        self._out_tokens: dict[TokenUsageKind, int] = {}
        self.set_aside_usd = 0.0
        self.set_aside_tokens = 0
        self._seen: TokenUsageRecord | None = None
        # A bill its fold could not count: the meter is short of it, so no send is admitted after.
        self.unmetered: Exception | None = None
        self.ledger: CycleEventLog | None = None
        # A nested run's book: each send is admitted against it AND every book enclosing it.
        self.enclosing: SpendBook | None = None

    # Each read live: a running change moves the cycle's standing record and the job's hold.
    @property
    def budget(self) -> Budget:
        if self.cycle is None:
            return self.declared
        return self.declared.over(self.cycle.standing())

    @property
    def allotment(self) -> Budget:
        share = self.share if isinstance(self.share, Budget) else self.share.share()
        return share.only(MONEY_AXES)

    @property
    def reserve(self) -> Budget:
        held = self.reserve_of
        return held if isinstance(held, Budget) else held.bound()

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

    def _coupon_usd_used(self) -> float:
        return self._usd_used() - self.off_coupon_usd + self.beside_usd

    def _coupon_tokens_used(self) -> int:
        return self._tokens_used() - self.off_coupon_tokens + self.beside_tokens

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
        for book, filed_as in self._levels(record.price.kind):
            try:
                book._counted(record.filed_as(filed_as))
            except Exception as fault:
                # Its guard reports it; no send in its chain is admitted against the short meter.
                book.unmetered = fault
                raise

    def _counted(self, record: TokenUsageRecord) -> None:
        paid_by = None if record.bill is None else record.bill.key_source
        if self.binds(record.price.kind):
            self._meter(*metered(record, self.meters), paid_by)
        else:
            self._beside(*metered(record, "bill"), paid_by)

    def _meter(self, usd: float | None, tokens: int, paid_by: KeySource | None) -> None:
        if usd is not None:
            self.usd_metered += usd
        self.tokens_spent += tokens
        if not draws_coupon(paid_by):
            self.off_coupon_usd += usd or 0.0
            self.off_coupon_tokens += tokens

    def _beside(self, usd: float | None, tokens: int, paid_by: KeySource | None) -> None:
        if draws_coupon(paid_by):
            self.beside_usd += usd or 0.0
            self.beside_tokens += tokens

    def _spend(
        self, kind: TokenUsageKind, usd: float | None, tokens: int, paid_by: KeySource
    ) -> None:
        """``usd`` is ``None`` where neither a bill nor a rate prices the call."""
        for book, filed_as in self._levels(kind):
            (book._meter if book.binds(filed_as) else book._beside)(usd, tokens, paid_by)

    def set_aside(self, usd: float, tokens: int) -> None:
        self.set_aside_usd = usd
        self.set_aside_tokens = tokens

    def saw(self, record: TokenUsageRecord) -> bool:
        """Valid only right after the append: it compares against the LAST record seen."""
        return record is self._seen

    def mirror(self, record: TokenUsageRecord) -> None:
        """The root ledger's copy of a nested run's call; its books counted it already."""
        if self.ledger is None:
            return
        mode = instrument_mode()
        copy = record.filed_as(_ENCLOSED).model_copy(
            update={
                "node": f"{NESTED_NODE_PREFIX}{record.node}",
                # The ROOT's round, whose ledger this lands on; the nested run binds its own.
                "round": current_round() if mode is None else mode.root_round,
                "mirrored": False,
            }
        )
        self.ledger.append(copy)

    def exhausted(self) -> ErrorCategory | None:
        allotment = self.allotment
        if allotment.usd is not None and self._usd_used() >= allotment.usd:
            return ErrorCategory.SPEND_CEILING
        if allotment.tokens is not None and self._tokens_used() >= allotment.tokens:
            return ErrorCategory.TOKEN_CEILING
        # The reserve is not asked: a run on the account's own keys outlives its coupon, and a
        # send on the box's key past it is refused where it is held (`refuse_unless_room`).
        left = self.clock_left()
        if left is not None and left <= 0.0:
            return ErrorCategory.WALL_CLOCK
        return None

    def _room(
        self, bound: SendBound, beside: TokenUsageKind | None, paid_by: KeySource
    ) -> Iterator[tuple[ErrorCategory, bool, int]]:
        """The campaign's share binds every send it meters; the coupon's reserve every one on the
        box's key; each holds the send's whole bound."""
        budget = self.allotment
        reserved = self.reserve if draws_coupon(paid_by) else Budget()
        for reserve, limit, used, out in (
            (False, budget.usd, self._usd_used(), self._held_usd),
            (True, reserved.usd, self._coupon_usd_used(), self._out_usd),
        ):
            if limit is not None:
                left = limit - used - sum(v for k, v in out.items() if k != beside)
                yield ErrorCategory.SPEND_CEILING, reserve, _admits(left, bound.usd, reserve)
        for reserve, limit_t, used_t, out_t in (
            (False, budget.tokens, self._tokens_used(), self._held_tokens),
            (True, reserved.tokens, self._coupon_tokens_used(), self._out_tokens),
        ):
            if limit_t is not None:
                left_t = limit_t - used_t - sum(v for k, v in out_t.items() if k != beside)
                yield ErrorCategory.TOKEN_CEILING, reserve, _admits(left_t, bound.tokens, reserve)

    def take_up(self, ledger: Path) -> Unreported:
        """The cycle's own prior, once at open: each bill counted as its send's was, a nested
        run's copy included, then what stayed held."""
        for record in money_on(ledger).usage:
            self._counted(record)
        left = unreported_on(ledger)
        self.usd_unreported, self.tokens_unreported = left.usd, left.tokens
        self.off_coupon_usd += left.off_coupon_usd
        self.off_coupon_tokens += left.off_coupon_tokens
        return left

    # These two plan cells alone, on what the cell's connector declares pays it.
    def fits(
        self, bound: SendBound, *, paid_by: KeySource, kind: TokenUsageKind | None = None
    ) -> int:
        """*kind*'s own holds out are the caller's to count; the share binds only a kind it meters,
        as `refuse_unless_room` refuses. ``None`` counts every side and every hold."""
        return min(
            (
                n
                for _, reserve, n in self._room(bound, kind, paid_by)
                if reserve or kind is None or self.binds(kind)
            ),
            default=sys.maxsize,
        )

    def hold(
        self,
        bound: SendBound,
        kind: TokenUsageKind,
        *,
        what: str,
        reservation: CellReservation | None,
        paid_by: KeySource,
    ) -> None:
        levels = list(self._levels(kind))
        if (fault := next((b.unmetered for b, _ in levels if b.unmetered), None)) is not None:
            raise fault
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
        for book, filed_as in levels if at is None else levels[:at]:
            book.refuse_unless_room(bound, filed_as, what, paid_by)
        if at is not None and reservation is not None:
            reservation.draw(bound, what, paid_by)
        for book, filed_as in levels:
            book._move(filed_as, bound, 1, paid_by)

    def _levels(self, kind: TokenUsageKind) -> Iterator[tuple[SpendBook, TokenUsageKind]]:
        """This book and every book enclosing it, each with the kind it files *kind* as, where it
        binds; the root always, whose reserve binds every send on the box's key."""
        book: SpendBook | None = self
        while book is not None:
            if book.binds(kind) or book.enclosing is None:
                yield book, kind
            book, kind = book.enclosing, _ENCLOSED

    def _move(self, kind: TokenUsageKind, bound: SendBound, sign: int, paid_by: KeySource) -> None:
        if self.binds(kind):
            self._held_usd[kind] = self._held_usd.get(kind, 0.0) + sign * (bound.usd or 0.0)
            self._held_tokens[kind] = self._held_tokens.get(kind, 0) + sign * (bound.tokens or 0)
        # What is out against the reserve: a send on the account's own key holds none of it.
        if draws_coupon(paid_by):
            self._out_usd[kind] = self._out_usd.get(kind, 0.0) + sign * (bound.usd or 0.0)
            self._out_tokens[kind] = self._out_tokens.get(kind, 0) + sign * (bound.tokens or 0)

    def refuse_in_chain(
        self, bound: SendBound, kind: TokenUsageKind, what: str, paid_by: KeySource
    ) -> None:
        for book, filed_as in self._levels(kind):
            book.refuse_unless_room(bound, filed_as, what, paid_by)

    def refuse_unless_room(
        self, bound: SendBound, kind: TokenUsageKind, what: str, paid_by: KeySource
    ) -> None:
        """*kind* as this book files it: the share refuses only a send it meters."""
        self.refuse_unpriced(bound, kind, what, paid_by)
        for category, reserve, n in self._room(bound, None, paid_by):
            if n < 1 and (reserve or self.binds(kind)):
                refusal = self._refusal(category, reserve, bound, what)
                if reserve:
                    # Whether a key of the account's own would pay this send is not the book's to know.
                    raise HostAllowanceExhaustedError(
                        f"{refusal}.", reason=LaunchRefusalReason.USED_UP, provider=None
                    )
                raise SendRefusedError(refusal, category=category)

    def refuse_unpriced(
        self, bound: SendBound, kind: TokenUsageKind, what: str, paid_by: KeySource
    ) -> None:
        priced = (self.binds(kind) and self.allotment.usd is not None) or (
            draws_coupon(paid_by) and self.reserve.usd is not None
        )
        if bound.usd is not None or not priced:
            return
        on = f" on {', '.join(bound.unpriced)}" if bound.unpriced else ""
        raise SendRefusedError(
            f"{what}: no rate bounds what it may cost{on}, so the dollar ceiling cannot be "
            "enforced for it and nothing was sent. Run it on a (provider, model) pair the rate "
            "table prices (`nodes.{node}.config`), or drop the dollar cap and bound the run in "
            "tokens (`budget: {usd: null, tokens: N}`); then a `start-run`.",
            category=ErrorCategory.NO_RATE,
        )

    def _refusal(self, category: ErrorCategory, reserve: bool, each: SendBound, what: str) -> str:
        limit = (
            "this run's reserve of the host's allowance, beside sends on the account's own keys,"
            if reserve
            else "cycle's share of the campaign ceiling"
        )
        bounds = self.reserve if reserve else self.allotment
        if category is ErrorCategory.SPEND_CEILING:
            # Only a bounded axis refuses, so the cap it names is set.
            cap = bounds.usd_amount
            out = sum((self._out_usd if reserve else self._held_usd).values())
            owed = [
                (
                    self.usd_metered - (self.off_coupon_usd if reserve else 0.0),
                    CEILING_METER_LABELS[self.meters],
                ),
                (self.beside_usd if reserve else 0.0, "on sends the meter does not count"),
                (self.usd_unreported, "unreported"),
                (out, "in calls still out"),
                (self.set_aside_usd, "set aside for the bench"),
            ]
            left = cap - sum(usd for usd, _ in owed)
            said = ", ".join(f"{usd_text(usd)} {name}" for usd, name in owed if usd)
            if left <= 0 or each.usd is None:
                return f"{what} was not sent: the {usd_text(cap)} {limit} is used up ({said})"
            return (
                f"{what} was not sent: it may cost up to {usd_text(each.usd)} and the "
                f"{usd_text(cap)} {limit} has {usd_text(left)} left ({said})"
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

    def release(self, bound: SendBound, kind: TokenUsageKind, paid_by: KeySource) -> None:
        for book, filed_as in self._levels(kind):
            book._move(filed_as, bound, -1, paid_by)

    def unpriced(self, bound: SendBound, kind: TokenUsageKind, paid_by: KeySource) -> None:
        self.release(bound, kind, paid_by)
        self.keep_held(_amount(bound.usd or 0.0, 0), kind, paid_by)

    def unreported(self, bound: SendBound, kind: TokenUsageKind, paid_by: KeySource) -> None:
        self.release(bound, kind, paid_by)
        self.keep_held(bound, kind, paid_by)

    def keep_held(self, bound: SendBound, kind: TokenUsageKind, paid_by: KeySource) -> None:
        for book, filed_as in self._levels(kind):
            if not book.binds(filed_as):
                book._beside(bound.usd, bound.tokens or 0, paid_by)
                continue
            book.usd_unreported += bound.usd or 0.0
            book.tokens_unreported += bound.tokens or 0
            if not draws_coupon(paid_by):
                book.off_coupon_usd += bound.usd or 0.0
                book.off_coupon_tokens += bound.tokens or 0

    def prices_sends(self) -> bool:
        """Whether ANY send is held in dollars here: a price list is then needed whoever pays."""
        return self.allotment.usd is not None or self.reserve.usd is not None

    def coupon_left(self) -> Budget:
        """What the reserve still admits on the box's key, past what it counted and has out."""
        reserve = self.reserve
        return Budget(
            usd=None
            if reserve.usd is None
            else max(0.0, reserve.usd - self._coupon_usd_used() - sum(self._out_usd.values())),
            tokens=None
            if reserve.tokens is None
            else max(
                0, reserve.tokens - self._coupon_tokens_used() - sum(self._out_tokens.values())
            ),
        )

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
        share=Budget(),
        reserve=Budget(),
        hold_id=None,
        meters="bill",
        opened=time.monotonic(),
    )


def require_spend_book(what: str) -> SpendBook:
    if (book := run_scope().book) is None:
        raise RuntimeError(f"{what}: no spend book is bound, so no admitted ceiling bounds it")
    return book


def bound_account(what: str) -> Stores:
    if (account := run_scope().account) is None:
        raise RuntimeError(
            f"{what}: no account is bound, so no wallet would draw this send. Every entry that "
            "sends lays its `RunScope.account` where it lays its spend book."
        )
    return account


def filed(label: CallLabel) -> CallLabel:
    """Admits a send in the bucket its bill lands in, so a bench pass is never held as search."""
    return CallLabel(label.node, filed_kind(label.kind))


def _bound_book(what: str) -> SpendBook:
    book = require_spend_book(what)
    billing_ledger(what)
    return book


def _amount(usd: float, tokens: int) -> SendBound:
    return SendBound(input_tokens=0, output_tokens=tokens, usd=usd)


class CellReservation:
    """In memory only: a reservation is no send, so it writes no ledger record."""

    def __init__(
        self, book: SpendBook, kind: TokenUsageKind, bound: SendBound, paid_by: KeySource
    ) -> None:
        self.book = book
        self.kind = kind
        self.paid_by = paid_by
        self.usd = bound.usd or 0.0
        self.tokens = bound.tokens or 0

    def draw(self, bound: SendBound, what: str, paid_by: KeySource) -> None:
        """The rest is admitted against the ceiling: it stops a cell, never the estimate."""
        usd, tokens = min(self.usd, bound.usd or 0.0), min(self.tokens, bound.tokens or 0)
        short = _amount((bound.usd or 0.0) - usd, (bound.tokens or 0) - tokens)
        if short.usd or short.tokens:
            self.book.refuse_in_chain(
                short, self.kind, f"{what}, past its cell's reservation", paid_by
            )
        self.usd -= usd
        self.tokens -= tokens
        # What the cell reserved, on its connector's payer, now held by the send that drew it.
        self.book.release(_amount(usd, tokens), self.kind, self.paid_by)


def reservation_left() -> SendBound:
    if (reservation := run_scope().reservation) is None:
        raise RuntimeError("no cell reservation is open to bound this send")
    return _amount(reservation.usd, reservation.tokens)


@contextmanager
def reserved(label: CallLabel, bound: SendBound, *, paid_by: KeySource) -> Iterator[None]:
    """``bound`` is the run a cell DECLARES, never its worst case, which fits one cell at a time."""
    label = filed(label)
    book = _bound_book(label.node)
    book.hold(
        bound, label.kind, what=label.node, reservation=run_scope().reservation, paid_by=paid_by
    )
    reservation = CellReservation(book, label.kind, bound, paid_by)
    try:
        with scoped(reservation=reservation):
            yield
    finally:
        book.release(_amount(reservation.usd, reservation.tokens), label.kind, paid_by)


class Billed(NamedTuple):
    usage: TokenAccount
    # Only what the provider REPORTED; ``None`` is priced at our rate by ``call_price``.
    cost_usd: float | None
    served_by: str | None = None
    model: str | None = None
    node: str | None = None
    provider: str | None = None
    duration_s: float | None = None
    rate_priced_usd: float | None = None


class _Part(NamedTuple):
    part: Billed
    price: CallPrice
    tokens: int


# Holds THIS process has out, so a ledger scan never reads one awaiting its bill as unreported.
_OUT: set[str] = set()


class Admission:
    def __init__(
        self,
        book: SpendBook,
        label: CallLabel,
        bound: SendBound,
        *,
        reservation: CellReservation | None,
        model: str | None,
        provider: str | None,
        paid_by: KeySource,
    ) -> None:
        self._book = book
        self._label = label
        self._paid_by = paid_by
        self._bound = bound
        self._billed_usd = 0.0
        self._billed_tokens = 0
        self._priced = True
        self._model = model
        self._provider = provider
        self._started = time.monotonic()
        self._hold_id = uuid.uuid4().hex
        # A nested run's holds and bills land on its ROOT's ledger; the account's charges take each once.
        self._root = book.root()
        self._held_on = self._root.ledger or billing_ledger(label.node)
        self._charges = bound_account(label.node).charges
        self._ledger = self._charges.held_on(self._held_on.path)
        self._parent = book.hold_id
        self._unseen: list[TokenUsageRecord] = []
        # Priced parts no ledger record carries yet, metered straight into the book at its close.
        self._unlanded: list[_Part] = []
        self.closed = False
        what = f"{label.node} on {model}" if model else label.node
        book.hold(bound, label.kind, what=what, reservation=reservation, paid_by=paid_by)
        _OUT.add(self._hold_id)
        account_held = False
        try:
            held = self._hold(self._hold_id, bound)
            account_held = True
            self._emit(held)
        except BaseException:
            self._finish()
            book.release(bound, label.kind, paid_by)
            # Nothing was sent: the account's hold closes, or the account holds its bound forever.
            if account_held:
                self._charges.append(Release(hold_id=self._hold_id))
            raise

    def _price(self, part: Billed) -> _Part:
        price = call_price(
            self._label.kind,
            part.usage,
            model=part.model or self._model,
            provider=part.provider or self._provider,
            cost_usd=part.cost_usd,
            recorded=part.rate_priced_usd,
        )
        one = _Part(part, price, part.usage.input + part.usage.output)
        self._bill(price.bill_or_rate_usd, one.tokens)
        self._unlanded.append(one)
        return one

    def _write(self, one: _Part, bill: Bill) -> None:
        part = one.part
        record = token_usage_record(
            node=part.node or self._label.node,
            usage=part.usage,
            duration_s=(
                time.monotonic() - self._started if part.duration_s is None else part.duration_s
            ),
            money=bill,
            served_by=part.served_by,
            mirrored=self._root is not self._book,
        )
        # The root's copy is the one money is summed off: it lands before the nested run's own.
        if self._root is not self._book:
            self._root.mirror(record)
        if append_record(record) is None:
            usd = one.price.bill_or_rate_usd
            logger.warning(
                "%s: a call counted at $%s reached a muted ledger; only this run's book counts it",
                part.node or self._label.node,
                "?" if usd is None else f"{usd:.6f}",
            )
            return
        self._unlanded.remove(one)
        if not self._book.saw(record):
            self._unseen.append(record)

    def _hold(self, hold_id: str, bound: SendBound) -> Hold:
        hold = Hold(
            hold_id=hold_id,
            parent=self._parent,
            key_source=self._paid_by,
            bound=Budget(usd=bound.usd, tokens=bound.tokens),
        )
        self._charges.append(hold)
        return hold

    def _emit(self, hold: Hold) -> None:
        emit_spend_hold(
            node=self._label.node, kind=self._label.kind, hold=hold, ledger=self._held_on
        )

    def _charge(self, price: CallPrice, tokens: int) -> Bill:
        bill = Bill(
            hold_id=self._hold_id,
            key_source=self._paid_by,
            price=price,
            tokens=tokens,
            ledger=self._ledger,
        )
        self._charges.append(bill)
        return bill

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
        """The book closes on every part and the unknown remainder whatever its writes do: a write
        that raises still leaves it settled whole, and the raise reaches the caller."""
        self._finish()
        priced = [self._price(part) for part in parts]
        try:
            # Every part is on the account before any ledger write can raise past the rest.
            bills = [self._charge(one.price, one.tokens) for one in priced]
            if not parts:
                released = Release(hold_id=self._hold_id)
                self._charges.append(released)
                append_record(released, self._held_on)
            for one, bill in zip(priced, bills, strict=True):
                self._write(one, bill)
            if unknown is not None:
                self._emit(self._hold(uuid.uuid4().hex, unknown))
        finally:
            self._close_book(unknown)

    def _close_book(self, unknown: SendBound | None) -> None:
        if unknown is not None:
            self._book.keep_held(unknown, self._label.kind, self._paid_by)
        if self._priced:
            self._book.release(self._bound, self._label.kind, self._paid_by)
        else:
            self._book.unpriced(self._bound, self._label.kind, self._paid_by)
        for record in self._unseen:
            self._book.count(record)
        for one in self._unlanded:
            self._book._spend(
                self._label.kind, one.price.bill_or_rate_usd, one.tokens, self._paid_by
            )

    def release(self) -> None:
        self.settle()

    def unreported(self) -> None:
        """Writes nothing: the hold stays open on the ledger, and the book keeps its bound held."""
        self._finish()
        self._book.unreported(self._bound, self._label.kind, self._paid_by)

    def _finish(self) -> None:
        if self.closed:
            # A second close would release the hold twice and admit sends on room nobody has.
            raise RuntimeError(f"{self._label.node}: an admission is closed once")
        self.closed = True
        _OUT.discard(self._hold_id)


@contextmanager
def admitted(
    label: CallLabel,
    bound: SendBound,
    *,
    model: str | None,
    provider: str | None,
    paid_by: KeySource,
) -> Iterator[Admission]:
    label = filed(label)
    book = _bound_book(label.node)
    reservation = run_scope().reservation
    admission = Admission(
        book,
        label,
        bound,
        reservation=reservation,
        model=model,
        provider=provider,
        paid_by=paid_by,
    )
    try:
        yield admission
    finally:
        if not admission.closed:
            admission.unreported()


class Unreported(NamedTuple):
    usd: float
    tokens: int
    sends: int
    # The part of the above held on the account's own keys, which its coupon never counts.
    off_coupon_usd: float = 0.0
    off_coupon_tokens: int = 0


def unreported_on(ledger: Path) -> Unreported:
    """``store/account_spend.py`` cannot see ``_OUT``; it asks the run's phase for liveness."""
    # The ledger's OWN lines only: walking a fork chain holds the parent's open sends twice.
    trail = money_on(ledger)
    left = [hold for hold_id, hold in trail.open.items() if hold_id not in _OUT]
    # A hold with no bound was admitted where no ceiling stood, and names no money to keep.
    money = [*left, *trail.unpriced]
    own_left = [hold for hold in left if not draws_coupon(hold.key_source)]
    own_money = [hold for hold in money if not draws_coupon(hold.key_source)]
    return Unreported(
        sum(hold.bound.usd or 0.0 for hold in money),
        sum(hold.bound.tokens or 0 for hold in left),
        len(money),
        off_coupon_usd=sum(hold.bound.usd or 0.0 for hold in own_money),
        off_coupon_tokens=sum(hold.bound.tokens or 0 for hold in own_left),
    )
