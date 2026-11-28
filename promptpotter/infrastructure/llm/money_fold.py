"""THE money fold: holds, bills, releases and lapses, read off an account's charge book or a
ledger's own lines, which every spend book and spend read folds."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple, assert_never, get_args

from pydantic import TypeAdapter

from promptpotter.domain.records.run_records import (
    Bill,
    ChargeLine,
    Hold,
    Lapse,
    Release,
    TokenUsageRecord,
)
from promptpotter.domain.spend import (
    NO_OVERSHOOT,
    NOTHING_PAID,
    Budget,
    CeilingMeter,
    Overshoot,
    PaidSpend,
    Remainder,
    SpendRollup,
    UserSpend,
    draws_coupon,
    metered,
    spend_slot,
)
from promptpotter.infrastructure.store.io import complete_lines
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses

if TYPE_CHECKING:
    pass


def bill_paid(bill: Bill) -> PaidSpend:
    price, tokens = bill.price, bill.tokens
    if price.cost_usd is None and price.rate_priced_usd is None:
        return PaidSpend.of(bill.key_source, UserSpend(0.0, tokens, tokens))
    usd, rate_priced = price.cost_usd or 0.0, price.rate_priced_usd or 0.0
    return PaidSpend.of(bill.key_source, UserSpend(usd, tokens, 0, rate_priced_usd=rate_priced))


def _unreported_of(hold: Hold) -> PaidSpend:
    """Unpriced lands in the token residue: `$0.00` would report an unpriceable send as a free one."""
    bound = hold.bound
    tokens = bound.tokens or 0
    if bound.usd is None:
        return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, tokens, 0.0, tokens))
    return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, bound.usd, tokens))


def _unpriced_of(hold: Hold) -> PaidSpend:
    """Its bill already filed the tokens, as residue (:func:`bill_paid`); only the money is kept."""
    usd = hold.bound.usd
    return NOTHING_PAID if usd is None else PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, usd))


def still_held(open_holds: Iterable[Hold], unpriced: Iterable[Hold]) -> PaidSpend:
    total = NOTHING_PAID
    for hold in open_holds:
        total = total.plus(_unreported_of(hold))
    for hold in unpriced:
        total = total.plus(_unpriced_of(hold))
    return total


class Spent(NamedTuple):
    # What the ledger's subject COST: every answered send, a nested run's mirrored onto it included.
    cost: PaidSpend
    # What its own sends billed: the cost less the mirrored sends a nested run's own ledger bills.
    billed: PaidSpend

    def plus(self, spent: Spent) -> Spent:
        return Spent(self.cost.plus(spent.cost), self.billed.plus(spent.billed))


MoneyRecord = Hold | Bill | Release | Lapse | TokenUsageRecord


class NodeCalls(NamedTuple):
    """One node's billed calls under one job, and what they drew."""

    calls: int
    usd: float
    tokens: int

    @property
    def mean(self) -> tuple[float, int]:
        return self.usd / self.calls, -(-self.tokens // self.calls)

    def plus(self, cost: UserSpend, *, first: bool) -> NodeCalls:
        return NodeCalls(
            self.calls + first, self.usd + cost.at_most_usd, self.tokens + cost.at_most_tokens
        )


NO_CALLS = NodeCalls(0, 0.0, 0)


class Money(NamedTuple):
    """One subject's money: the account's charge book, or one ledger's OWN lines."""

    spent: Spent
    # Each hold no bill or release closed yet.
    open: dict[str, Hold]
    # Each hold at its latest bound, while open or while a kept hold or an unreported send names it.
    holds: dict[str, Hold]
    # What was drawn under each kept hold, by every call beneath it: its bills, a lapse at its
    # bound, an unpriced bill's bound money.
    drawn: dict[str, PaidSpend]
    # The holds a bill closed in tokens alone, naming neither a provider's figure nor a rate's
    # price: the bound's money stays held.
    unpriced: tuple[Hold, ...]
    # Each send a lapse closed, at what it may have cost.
    lapsed: tuple[Hold, ...]
    # Every ledger a hold's sends landed on (`Hold.ledger`).
    ledgers: frozenset[str]
    metered: dict[CeilingMeter, Budget]
    by_round: dict[int, SpendRollup]
    calls: int
    worked_s: float
    # By (open job hold, node): what a call of the node holds at with `overshoot` on.
    by_node: dict[tuple[str, str], NodeCalls]
    # By the ledger a call's sends land on (`Hold.ledger`).
    overshot: dict[str, Overshoot]

    def holding(self, hold_id: str) -> bool:
        """Whether *hold_id* and every hold above it is still open."""
        above: str | None = hold_id
        while above is not None:
            if above not in self.open:
                return False
            above = self.open[above].parent
        return True

    def unreported_holds(self) -> tuple[list[Hold], list[Hold]]:
        """The sends no open hold covers, at their bound and at its money alone: each send left
        open under a closed hold, each lapsed one and each unpriced one."""
        left = [
            hold
            for hold in self.open.values()
            if hold.ledger is not None and not self.holding(hold.hold_id)
        ]
        return [*left, *self.lapsed], list(self.unpriced)

    @property
    def unreported(self) -> PaidSpend:
        return still_held(*self.unreported_holds())

    def _spent(self, hold_id: str, host: bool) -> UserSpend:
        drawn = self.drawn.get(hold_id, NOTHING_PAID)
        return drawn.host if host else drawn.total

    def _below(self, hold_id: str, host: bool) -> tuple[float, int]:
        below = [
            self.kept(child, host=host) for child in self.open.values() if child.parent == hold_id
        ]
        return sum(u for u, _ in below), sum(t for _, t in below)

    def kept(self, hold: Hold, *, host: bool) -> tuple[float, int]:
        """What *hold* still holds of the money above it (*host*: of the coupon): on a bounded
        axis its bound less what was drawn under it, never below what its open children hold; on
        an unbounded one, or on the coupon where it draws no coupon, what they hold."""
        usd, tokens = self._below(hold.hold_id, host)
        if host and not draws_coupon(hold.key_source):
            return usd, tokens
        bound, spent = hold.bound, self._spent(hold.hold_id, host)
        return (
            usd if bound.usd is None else max(usd, bound.usd - spent.at_most_usd),
            tokens if bound.tokens is None else max(tokens, bound.tokens - spent.at_most_tokens),
        )

    def left(self, hold: Hold) -> Remainder:
        """What *hold* still admits: its bound less what was drawn under it and what its open
        children hold."""
        usd, tokens = self._below(hold.hold_id, False)
        bound, spent = hold.bound, self._spent(hold.hold_id, False)
        return Remainder(
            None if bound.usd is None else bound.usd - spent.at_most_usd - usd,
            None if bound.tokens is None else bound.tokens - spent.at_most_tokens - tokens,
        )

    def held_back(self, excluding: str | None) -> Budget:
        """What the open holds on the account still hold of its coupon, from each root but
        *excluding* (`kept`): a bill past a bound is drawn all the same."""
        roots = [
            self.kept(hold, host=True)
            for hold in self.open.values()
            if hold.parent is None and hold.hold_id != excluding
        ]
        return Budget.amount(sum(u for u, _ in roots), sum(t for _, t in roots))


class MoneyFold:
    """THE fold of money: a hold opens under its parent, each bill closes a call's and draws on
    every hold above it, a release closes one, a lapse closes a call's and draws what it may have
    cost, a replay is priced and charges nothing. It keeps aggregates and the holds still named:
    a settle's lines land in one append, so a closed call leaves at the first line naming another."""

    records: ClassVar[RecordClasses] = get_args(MoneyRecord)

    def __init__(self) -> None:
        self._spent = Spent(NOTHING_PAID, NOTHING_PAID)
        self._open: dict[str, Hold] = {}
        self._drawn: dict[str, PaidSpend] = {}
        self._holds: dict[str, Hold] = {}
        # How many kept holds name each kept hold as their parent.
        self._children: dict[str, int] = {}
        # The call a bill closed, whose settle's later lines may still name it.
        self._settling: str | None = None
        # Unreported sends, kept with every hold above them for the books that file them.
        self._unreported: set[str] = set()
        # Replaced, never mutated, so a view shares them: they grow past what one send changes.
        self._unpriced: tuple[Hold, ...] = ()
        self._lapsed: tuple[Hold, ...] = ()
        self._ledgers: frozenset[str] = frozenset()
        self._overshot: dict[str, Overshoot] = {}
        self._usd: dict[CeilingMeter, float] = dict.fromkeys(get_args(CeilingMeter), 0.0)
        self._tokens: dict[CeilingMeter, int] = dict.fromkeys(get_args(CeilingMeter), 0)
        self._by_round: dict[int, SpendRollup] = {}
        self._calls = 0
        self._worked_s = 0.0
        # What each kept call's bills drew, by its hold.
        self._cost: dict[str, UserSpend] = {}
        self._by_node: dict[tuple[str, str], NodeCalls] = {}
        # A copy, since the next feed banks into the live rollups; one per feed, not per read.
        self._view: Money | None = None

    def feed(self, record: MoneyRecord) -> None:
        self._view = None
        match record:
            case Bill() | Lapse() if record.hold_id == self._settling:
                pass
            case _:
                self._settled()
        match record:
            case Hold():
                self._hold(record)
            case Bill():
                self._bill(record, mirrored=False)
            case Release():
                self._close(record.hold_id)
            case Lapse():
                lapsed = self._holds[record.hold_id].model_copy(update={"bound": record.bound})
                self._lapsed = (*self._lapsed, lapsed)
                self._unreported.add(record.hold_id)
                self._draw(lapsed.parent, _unreported_of(lapsed))
                self._settling = None
                self._close(record.hold_id)
            case TokenUsageRecord():
                self._call(record)
            case _:
                assert_never(record)

    def _hold(self, hold: Hold) -> None:
        if hold.hold_id not in self._holds and hold.parent in self._holds:
            self._children[hold.parent] = self._children.get(hold.parent, 0) + 1
        self._holds[hold.hold_id] = hold
        self._open[hold.hold_id] = hold
        if hold.ledger is not None and hold.ledger not in self._ledgers:
            self._ledgers = self._ledgers | {hold.ledger}

    def _settled(self) -> None:
        settled, self._settling = self._settling, None
        if settled is not None:
            self._leave(settled)

    def _close(self, hold_id: str) -> None:
        self._open.pop(hold_id, None)
        self._leave(hold_id)

    def _leave(self, hold_id: str | None) -> None:
        """*hold_id* and each closed hold above it leave once no kept hold, settle or unreported
        send names them."""
        while (
            hold_id is not None
            and hold_id in self._holds
            and hold_id not in self._open
            and hold_id not in self._unreported
            and hold_id != self._settling
            and not self._children.get(hold_id)
        ):
            held = self._holds.pop(hold_id)
            self._drawn.pop(hold_id, None)
            self._cost.pop(hold_id, None)
            self._children.pop(hold_id, None)
            if held.resource is None:
                self._by_node = {k: v for k, v in self._by_node.items() if k[0] != hold_id}
            hold_id = held.parent
            if hold_id in self._children:
                self._children[hold_id] -= 1

    def _bill(self, bill: Bill, *, mirrored: bool) -> None:
        paid = bill_paid(bill)
        self._spent = Spent(
            self._spent.cost.plus(paid),
            self._spent.billed if mirrored else self._spent.billed.plus(paid),
        )
        # A bill names a hold of another trail where a nested run's copy is carried onto its root.
        if (held := self._holds.get(bill.hold_id)) is None:
            return
        self._settling = held.hold_id
        self._draw(held.parent, paid)
        self._open.pop(held.hold_id, None)
        if bill.price.bill_or_rate_usd is None and held.hold_id not in self._unreported:
            self._unpriced = (*self._unpriced, held)
            self._unreported.add(held.hold_id)
            unpriced = _unpriced_of(held)
            self._draw(held.parent, unpriced)
            paid = paid.plus(unpriced)
        self._cost_of(held, paid.total)

    def _draw(self, above: str | None, paid: PaidSpend) -> None:
        while above is not None and above in self._holds:
            self._drawn[above] = self._drawn.get(above, NOTHING_PAID).plus(paid)
            above = self._holds[above].parent

    def _cost_of(self, call: Hold, part: UserSpend) -> None:
        """*part* of what *call* drew, filed under its node at the job it runs under, and on its
        ledger past what it held."""
        before = self._cost.get(call.hold_id)
        first = before is None
        cost = part if before is None else before.plus(part)
        self._cost[call.hold_id] = cost
        if (held := call.bound.usd) is not None and call.ledger is not None:
            was = 0.0 if before is None else max(0.0, before.at_most_usd - held)
            if (past := max(0.0, cost.at_most_usd - held)) > was:
                overshot = self._overshot.get(call.ledger, NO_OVERSHOOT)
                self._overshot = {**self._overshot, call.ledger: overshot.past(was, past)}
        job = call.parent
        # A cell between a call and its job carries a resource; a job's hold carries none.
        while job in self._holds and self._holds[job].resource is not None:
            job = self._holds[job].parent
        if job is not None and job in self._holds and call.node is not None:
            key = (job, call.node)
            self._by_node[key] = self._by_node.get(key, NO_CALLS).plus(part, first=first)

    def _call(self, usage: TokenUsageRecord) -> None:
        for meter in self._usd:
            usd, tokens = metered(usage, meter)
            if usd is not None:
                self._usd[meter] += usd
            self._tokens[meter] += tokens
        spend_slot(self._by_round, usage.spend_round, SpendRollup).bank(usage)
        # A replay carries no charge: it cost nothing.
        if (bill := usage.bill) is not None:
            self._calls += 1
            self._worked_s += usage.duration_s
            self._bill(bill, mirrored=usage.mirrored)

    def value(self) -> Money:
        if self._view is None:
            self._view = Money(
                self._spent,
                dict(self._open),
                dict(self._holds),
                dict(self._drawn),
                self._unpriced,
                self._lapsed,
                self._ledgers,
                {m: Budget.amount(self._usd[m], self._tokens[m]) for m in self._usd},
                {n: held.model_copy(deep=True) for n, held in self._by_round.items()},
                self._calls,
                self._worked_s,
                dict(self._by_node),
                self._overshot,
            )
        return self._view


LedgerIndex.readers(MoneyFold)


def money_on(subject: Path, until: int | None = None) -> Money:
    """*subject* is a ledger, or an account's charge book (`ChargeBook.path`)."""
    return LedgerIndex.of(subject).view(MoneyFold, until)


_CHARGE_LINE: TypeAdapter[ChargeLine] = TypeAdapter(ChargeLine)


def charge_book_fold(raw: bytes) -> Money:
    """A charge book's bytes, folded as `money_on` folds the file: what proves one at a landing
    (`data_root.py::Folded`). A settle another writer's line split refuses, which a book written
    before each fact landed in one append can hold: folded on, its late Bill draws nothing and its
    late Lapse raises."""
    fold = MoneyFold()
    seen: set[str] = set()
    for line in complete_lines(BytesIO(raw)):
        if not line.strip():
            continue
        record = _CHARGE_LINE.validate_json(line)
        if isinstance(record, Hold):
            seen.add(record.hold_id)
        elif (
            isinstance(record, Bill | Lapse)
            and record.hold_id in seen
            and record.hold_id not in fold._holds
        ):
            raise ValueError(f"hold {record.hold_id}'s settle is split by another line")
        fold.feed(record)
    return fold.value()
