from __future__ import annotations

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
    Lapse,
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
    NOTHING_PAID,
    Budget,
    CeilingMeter,
    KeySource,
    TokenAccount,
    TokenUsageKind,
    UserSpend,
    draws_coupon,
    usd_text,
)
from promptpotter.infrastructure.llm.telemetry import (
    append_record,
    billing_ledger,
    call_price,
    campaign_priced,
    current_round,
    filed_kind,
    token_usage_record,
)
from promptpotter.infrastructure.store.read_model import Money, money_on, still_held
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

    @property
    def budget(self) -> Budget:
        return Budget(usd=self.usd, tokens=self.tokens)


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

    def overshoot(self) -> bool:
        return self.campaigns.read_standing_budget(self.hop).overshoot

    def share(self) -> Budget:
        """The most the cycle may have metered of its campaign's ceiling, which a reshare moves."""
        share = self.campaigns.last(self.hop, CycleShareRecord)
        if share is None:
            raise RuntimeError(f"no share landed on {self.hop} before its book opened")
        return share.allotment


# What an enclosing book files a nested run's sends as, as its ledger's copy of them does.
_ENCLOSED: TokenUsageKind = "backend"


def _resource(hold: Hold) -> TokenUsageKind:
    return _ENCLOSED if hold.resource is None else hold.resource


class SpendBook:
    """A run's admission, read at each send off the one fold of money (`read_model.Money`): its
    share off the ledger it meters, its reserve off its job's hold on the account."""

    def __init__(
        self,
        *,
        declared: Budget,
        share: Budget | LiveCycle,
        charges: ChargeBook,
        hold_id: str,
        headroom: Budget,
        metered_on: Path | None,
        meters: CeilingMeter,
        opened: float,
        cycle: LiveCycle | None = None,
    ) -> None:
        # The launch's own axis (`wall_clock_s`); money binds on the share and the reserve.
        self.declared = declared.only(CYCLE_AXES)
        self.share = share
        self.charges = charges
        # The job's hold every call and cell of this book draws on.
        self.hold_id = hold_id
        # What the coupon admits on an axis the job's hold leaves unbounded: a paid call's.
        self.headroom = headroom
        # The cycle's ledger its share meters (replays and a nested run's copies live there);
        # ``None`` meters what its job's hold drew.
        self.metered_on = metered_on
        self.cycle = cycle
        self.meters = meters
        # The launch's ``time.monotonic()``: a book re-armed mid-launch keeps its clock.
        self.opened = opened
        self.set_aside_usd = 0.0
        self.set_aside_tokens = 0
        # The ledger a nested run's sends are copied onto, as this book's root.
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
        return self.headroom.over(self._money().holds[self.hold_id].bound)

    def _money(self) -> Money:
        return money_on(self.charges.path)

    def root(self) -> SpendBook:
        return self if self.enclosing is None else self.enclosing.root()

    def clock_left(self) -> float | None:
        """Seconds this launch may still run; ``None`` where no wall clock bounds it."""
        limit = self.budget.wall_clock_s
        return None if limit is None else limit - (time.monotonic() - self.opened)

    def binds(self, kind: TokenUsageKind) -> bool:
        return kind in METER_KINDS[self.meters]

    @property
    def prices_replays(self) -> bool:
        return METER_PRICES_REPLAYS[self.meters]

    def replays_free(self, cell_key: str) -> bool:
        """A bill meter prices no replay; the other prices one the first time its campaign reads it."""
        return not self.prices_replays or campaign_priced(cell_key)

    @property
    def spent(self) -> UserSpend:
        """What the share counts, in its meter's units, set-aside and calls out apart."""
        return self._spent(self._money(), host=False)

    def _filed(self, money: Money, hold: Hold) -> TokenUsageKind | None:
        """*hold*'s kind as this book files it; ``None`` where it draws on no hold of its job."""
        kind, above = hold.resource, hold.parent
        while above is not None and above != self.hold_id:
            parent = money.holds[above]
            if parent.resource is None:
                kind = _ENCLOSED
            above = parent.parent
        return None if above is None else (_ENCLOSED if kind is None else kind)

    def _spent(self, money: Money, *, host: bool) -> UserSpend:
        """What the share (*host*: the reserve, on the box's key) counts as spent and unreported:
        its run's sends, and an earlier run's on its cycle."""
        if self.metered_on is None:
            drawn = money.drawn.get(self.hold_id, NOTHING_PAID)
            return drawn.host if host else drawn.total
        on = money_on(self.metered_on)
        ledger = self.charges.held_on(self.metered_on)

        def mine(hold: Hold) -> bool:
            kind = self._filed(money, hold)
            if kind is None:
                return hold.ledger == ledger
            return host or self.binds(kind)

        whole, unpriced = money.unreported_holds()
        left = still_held(filter(mine, whole), filter(mine, unpriced))
        if host:
            return on.spent.cost.host.plus(left.host)
        metered = on.metered[self.meters]
        return UserSpend(metered.usd_amount, metered.token_amount, 0).plus(left.total)

    def _out(self, money: Money, *, host: bool, beside: TokenUsageKind | None) -> tuple[float, int]:
        """What the calls and cells out under its job still hold, on the share those it meters;
        *beside*'s are the caller's to count."""
        usd, tokens = 0.0, 0
        for hold in money.open.values():
            kind = _resource(hold)
            if hold.parent != self.hold_id or kind == beside:
                continue
            if draws_coupon(hold.key_source) if host else self.binds(kind):
                held_usd, held_tokens = money.kept(hold, host=host)
                usd, tokens = usd + held_usd, tokens + held_tokens
        return usd, tokens

    def _left(
        self, money: Money, *, host: bool, beside: TokenUsageKind | None
    ) -> tuple[float | None, int | None]:
        """What the share (*host*: the reserve) still admits, below nothing once overdrawn;
        ``None`` on an axis it leaves unbounded."""
        limit = self.reserve if host else self.allotment
        if limit.usd is None and limit.tokens is None:
            return None, None
        spent = self._spent(money, host=host)
        out_usd, out_tokens = self._out(money, host=host, beside=beside)
        return (
            None
            if limit.usd is None
            else limit.usd - spent.at_most_usd - self.set_aside_usd - out_usd,
            None
            if limit.tokens is None
            else limit.tokens - spent.at_most_tokens - self.set_aside_tokens - out_tokens,
        )

    def exhausted(self) -> ErrorCategory | None:
        allotment = self.allotment
        if allotment.usd is not None or allotment.tokens is not None:
            spent = self.spent
            if (
                allotment.usd is not None
                and spent.at_most_usd + self.set_aside_usd >= allotment.usd
            ):
                return ErrorCategory.SPEND_CEILING
            tokens = spent.at_most_tokens + self.set_aside_tokens
            if allotment.tokens is not None and tokens >= allotment.tokens:
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
        money = self._money()
        share_usd, share_tokens = self._left(money, host=False, beside=beside)
        held_usd, held_tokens = (
            self._left(money, host=True, beside=beside) if draws_coupon(paid_by) else (None, None)
        )
        for reserve, left in ((False, share_usd), (True, held_usd)):
            if left is not None:
                yield ErrorCategory.SPEND_CEILING, reserve, _admits(left, bound.usd, reserve)
        for reserve, left_t in ((False, share_tokens), (True, held_tokens)):
            if left_t is not None:
                yield ErrorCategory.TOKEN_CEILING, reserve, _admits(left_t, bound.tokens, reserve)

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
        label: CallLabel,
        *,
        what: str,
        paid_by: KeySource,
        ledger: str | None,
    ) -> str:
        """THE door a call's or a cell's hold opens through: refused where a level has no room for
        it, then one write on the account, under its run's open cell or else its job."""
        parent = draws_on(self)
        money = self._money()
        for book, filed_as in self._levels(label.kind):
            past = book._past_cells(money, bound, parent)
            book.refuse_unless_room(past, filed_as, what, paid_by)
        hold_id = uuid.uuid4().hex
        self.charges.append(
            Hold(
                hold_id=hold_id,
                parent=parent,
                key_source=paid_by,
                bound=bound.budget,
                resource=label.kind,
                node=label.node,
                ledger=ledger,
            )
        )
        return hold_id

    def expected(self, bound: SendBound, node: str) -> SendBound:
        """What a call of *node* holds: its worst case *bound*, or with the cycle's `overshoot` on
        the mean its node's calls in this run billed, on each axis *bound* bounds, never past it."""
        if self.cycle is None or not self.cycle.overshoot():
            return bound
        if (calls := self._money().by_node.get((self.hold_id, node))) is None:
            return bound
        usd, tokens = calls.mean
        return SendBound(
            input_tokens=0,
            output_tokens=None if bound.tokens is None else min(bound.tokens, tokens),
            usd=None if bound.usd is None else min(bound.usd, usd),
            unpriced=bound.unpriced,
        )

    def _past_cells(self, money: Money, bound: SendBound, parent: str) -> SendBound:
        """What *bound* asks of this book past what each cell between *parent* and its job still
        holds: a cell's reservation stops a cell, never the estimate."""
        usd, tokens = bound.usd, bound.tokens
        above = parent
        while above != self.hold_id:
            cell = money.holds[above]
            if cell.resource is not None:
                left_usd, left_tokens = money.left(cell)
                if usd is not None and left_usd is not None:
                    usd = max(0.0, usd - max(0.0, left_usd))
                if tokens is not None and left_tokens is not None:
                    tokens = max(0, tokens - max(0, left_tokens))
            if cell.parent is None:
                raise RuntimeError(f"hold {parent} draws on no hold of job {self.hold_id}")
            above = cell.parent
        return SendBound(input_tokens=0, output_tokens=tokens, usd=usd, unpriced=bound.unpriced)

    def _levels(self, kind: TokenUsageKind) -> Iterator[tuple[SpendBook, TokenUsageKind]]:
        """This book and every book enclosing it, each with the kind it files *kind* as, where it
        binds; the root always, whose reserve binds every send on the box's key."""
        book: SpendBook | None = self
        while book is not None:
            if book.binds(kind) or book.enclosing is None:
                yield book, kind
            book, kind = book.enclosing, _ENCLOSED

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
        money = self._money()
        spent = self._spent(money, host=reserve)
        out_usd, out_tokens = self._out(money, host=reserve, beside=None)
        if category is ErrorCategory.SPEND_CEILING:
            # Only a bounded axis refuses, so the cap it names is set.
            cap = bounds.usd_amount
            owed = [
                (spent.sent_usd, CEILING_METER_LABELS["bill" if reserve else self.meters]),
                (spent.unreported_usd, "unreported"),
                (out_usd, "in calls still out"),
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
        return (
            f"{what} holds {each.tokens:,} tokens while it is out, and the "
            f"{bounds.token_amount:,}-token {limit} holds {spent.used_tokens:,} spent, "
            f"{spent.unreported_tokens:,} unreported, {out_tokens:,} for calls out and "
            f"{self.set_aside_tokens:,} set aside for the bench"
        )

    def set_aside(self, usd: float, tokens: int) -> None:
        self.set_aside_usd = usd
        self.set_aside_tokens = tokens

    @property
    def nested(self) -> bool:
        return self.enclosing is not None

    def file(self, record: TokenUsageRecord, ledger: CycleEventLog) -> None:
        """A call's usage on *ledger*, a replay's included; a nested run's lands on its root's
        ledger first, the copy money is summed off."""
        if self.nested:
            self.root().mirror(record)
        append_record(record, ledger)

    def mirror(self, record: TokenUsageRecord) -> None:
        """The root ledger's copy of a nested run's call, which its meter counts."""
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

    def prices_sends(self) -> bool:
        """Whether ANY send is held in dollars here: a price list is then needed whoever pays."""
        return self.allotment.usd is not None or self.reserve.usd is not None

    def coupon_left(self) -> Budget:
        """What the reserve still admits on the box's key, past what it counted and has out."""
        return _at_least_nothing(*self._left(self._money(), host=True, beside=None))

    def money_left(self) -> Budget:
        """What the allotment still admits past spend and holds: an enclosed run's whole ceiling."""
        return _at_least_nothing(*self._left(self._money(), host=False, beside=None))


def _at_least_nothing(usd: float | None, tokens: int | None) -> Budget:
    return Budget(
        usd=None if usd is None else max(0.0, usd),
        tokens=None if tokens is None else max(0, tokens),
    )


def draws_on(book: SpendBook) -> str:
    """The hold a new one under *book* draws on: its run's open cell reservation, else its job's."""
    cell = run_scope().reservation
    if cell is not None and money_on(book.charges.path).holds[cell].parent == book.hold_id:
        return cell
    return book.hold_id


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


def reservation_left() -> SendBound:
    if (cell := run_scope().reservation) is None:
        raise RuntimeError("no cell reservation is open to bound this send")
    money = money_on(require_spend_book("a cell's reservation").charges.path)
    usd, tokens = money.left(money.holds[cell])
    return SendBound(
        input_tokens=0,
        output_tokens=0 if tokens is None else max(0, tokens),
        usd=0.0 if usd is None else max(0.0, usd),
    )


@contextmanager
def reserved(label: CallLabel, bound: SendBound, *, paid_by: KeySource) -> Iterator[None]:
    """``bound`` is the run a cell DECLARES, never its worst case, which fits one cell at a time:
    a hold its sends draw on, released at its exit."""
    label = filed(label)
    book = require_spend_book(label.node)
    billing_ledger(label.node)
    cell = book.hold(bound, label, what=label.node, paid_by=paid_by, ledger=None)
    try:
        with scoped(reservation=cell):
            yield
    finally:
        book.charges.append(Release(hold_id=cell))


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


class Admission:
    def __init__(
        self,
        book: SpendBook,
        label: CallLabel,
        bound: SendBound,
        *,
        model: str | None,
        provider: str | None,
        paid_by: KeySource,
    ) -> None:
        self._book = book
        self._label = label
        self._paid_by = paid_by
        self._bound = bound
        self._model = model
        self._provider = provider
        self._started = time.monotonic()
        # A nested run's sends land on its ROOT's ledger too; the account's charges take each once.
        self._root = book.root()
        self._charges = book.charges
        # Its usage lands where the ledger was bound at admission, whatever a probe mutes after.
        self._ledger = billing_ledger(label.node)
        self.closed = False
        what = f"{label.node} on {model}" if model else label.node
        self._hold_id = book.hold(
            book.expected(bound, label.node),
            label,
            what=what,
            paid_by=paid_by,
            ledger=self._charges.held_on((self._root.ledger or self._ledger).path),
        )

    def _price(self, part: Billed) -> _Part:
        price = call_price(
            self._label.kind,
            part.usage,
            model=part.model or self._model,
            provider=part.provider or self._provider,
            cost_usd=part.cost_usd,
            recorded=part.rate_priced_usd,
        )
        return _Part(part, price, part.usage.input + part.usage.output)

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
            mirrored=self._book.nested,
        )
        self._book.file(record, self._ledger)

    def close(self, outcome: SendOutcome) -> None:
        if outcome.reported is not None:
            self.settle(*outcome.reported.parts, unknown=outcome.reported.unknown)
        elif outcome.may_have_billed:
            self.unreported()
        else:
            self.release()

    def settle(self, *parts: Billed, unknown: SendBound | None = None) -> None:
        """Every part is on the account, in one append, before any ledger write can raise."""
        self._finish()
        priced = [self._price(part) for part in parts]
        bills = [
            Bill(
                hold_id=self._hold_id, key_source=self._paid_by, price=one.price, tokens=one.tokens
            )
            for one in priced
        ]
        closing: list[Lapse | Release] = []
        if unknown is not None:
            closing.append(Lapse(hold_id=self._hold_id, bound=unknown.budget))
        elif not parts:
            closing.append(Release(hold_id=self._hold_id))
        self._charges.append(*bills, *closing)
        for one, bill in zip(priced, bills, strict=True):
            self._write(one, bill)

    def release(self) -> None:
        self.settle()

    def unreported(self) -> None:
        self._finish()
        self._charges.append(Lapse(hold_id=self._hold_id, bound=self._bound.budget))

    def _finish(self) -> None:
        if self.closed:
            # A second close would close the hold twice and draw a lapse nobody sent.
            raise RuntimeError(f"{self._label.node}: an admission is closed once")
        self.closed = True


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
    admission = Admission(
        require_spend_book(label.node),
        label,
        bound,
        model=model,
        provider=provider,
        paid_by=paid_by,
    )
    try:
        yield admission
    finally:
        if not admission.closed:
            admission.unreported()
