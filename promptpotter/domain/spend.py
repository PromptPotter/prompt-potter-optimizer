from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import reduce
from typing import Annotated, Literal, NamedTuple, get_args

from pydantic import (
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from promptpotter.domain.layer_fold import UNBOUNDED, Unbounded
from promptpotter.domain.paired_reading import level_text
from promptpotter.domain.strict_model import (
    FrozenModel,
    NonNegativeWireFloat,
    PositiveWireFloat,
    StrictModel,
    WireInt,
)
from promptpotter.domain.wire_record import ALWAYS, FLAT, WireRecord
from promptpotter.shared.worded import Worded


def _reported_sum(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else a + b


class TokenAccount(FrozenModel):
    """What ONE metered thing consumed: a provider round-trip, a measured row or a searchpoint.

    ``reasoning`` is a subset of ``output`` and both cache counts of ``input``, never further
    totals. A null part means no breakdown was reported; ``0`` means one was, and it was zero. A sum
    adds the parts reported and stays null only where neither side reported one.
    """

    input: int = 0
    output: int = 0
    reasoning: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None

    @property
    def total(self) -> int:
        return self.input + self.output

    def __add__(self, other: TokenAccount) -> TokenAccount:
        return TokenAccount(
            input=self.input + other.input,
            output=self.output + other.output,
            reasoning=_reported_sum(self.reasoning, other.reasoning),
            cache_read=_reported_sum(self.cache_read, other.cache_read),
            cache_write=_reported_sum(self.cache_write, other.cache_write),
        )

    @classmethod
    def from_step_tokens(cls, step_tokens: Mapping[str, StepUsage]) -> TokenAccount | None:
        """``None`` where the row carries no entries: "reported nothing" is not "reported zero"."""
        if not step_tokens:
            return None
        return reduce(operator.add, (usage.tokens for usage in step_tokens.values()))

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, StepUsage]]) -> TokenAccount | None:
        """Each row's step tokens summed; ``None`` where no row reported any."""
        accounts = [a for r in rows if (a := cls.from_step_tokens(r)) is not None]
        if not accounts:
            return None
        return reduce(operator.add, accounts)

    def cache_share(self, *, replayed: bool) -> float | None:
        """``None`` where unanswerable, a replay included (its counts are the banked row's); ``0.0`` is a MEASUREMENT."""
        if replayed or self.cache_read is None or self.input <= 0:
            return None
        return self.cache_read / self.input

    def prefix(self, *, replayed: bool) -> PrefixReading:
        return prefix_reading(self.cache_share(replayed=replayed), replayed=replayed)


@dataclass(frozen=True, slots=True)
class StepUsage:
    # THEIR cache parts; the `cached` flag beside a cell says WE replayed it.
    tokens: TokenAccount = field(default_factory=TokenAccount, metadata=FLAT)
    estimated: bool = field(default=False, metadata=ALWAYS)
    # What the provider REPORTED it billed; absent where it reported nothing, never our rate's price.
    cost_usd: float | None = None
    rate_priced_usd: float | None = None
    model: str | None = None
    provider: str | None = None
    # The upstream host that answered, where `provider` names a gateway.
    served_by: str | None = None
    finish_reason: str | None = None
    # The counts above sum every request the node sent; these left and reported no usage (a 5xx).
    unreported_attempts: int = 0

    @classmethod
    def from_wire(cls, entry: Mapping[str, object]) -> StepUsage:
        return _STEP_USAGE.read(entry)

    def wire(self) -> dict[str, object]:
        return _STEP_USAGE.write(self)


_STEP_USAGE = WireRecord.of(
    StepUsage,
    "StepTokenUsage",
    "Per-LLM-node ``step_tokens`` entry — the WIRE spelling of :class:`TokenAccount`, which\n"
    "deserializes one. A ``NotRequired`` key is absent where the provider surfaced nothing.",
)


PrefixState = Literal["discounted", "cold", "unreported", "replayed"]

PREFIX_STATE_TITLES: Worded[PrefixState, str] = Worded(
    {
        "discounted": "The provider served this share of the input off its own prompt-prefix cache, "
        "billed at a discount. Unrelated to 📖, which means no provider was reached at all.",
        "cold": "The provider reported its cache accounting and served none of this input from it — "
        "the prefix was cold. A measurement, not a missing one.",
        "unreported": "This provider reported no cache accounting, so whether it collected the prefix "
        "is unknown. Not the same as no hit.",
        "replayed": "Replayed from our own archive — no provider was reached, so the cell cost "
        "nothing and there is no discount to report.",
    }
)
if set(PREFIX_STATE_TITLES) != set(get_args(PrefixState)):
    raise RuntimeError("PREFIX_STATE_TITLES is out of step with PrefixState (domain/spend.py)")


class PrefixReading(FrozenModel):
    """A provider's prefix-cache discount as every surface reads it, served whole."""

    state: PrefixState
    share: float | None = Field(description="Null on `unreported` and `replayed`.")
    badge: str = Field(
        description="`c39%` / `c0%` / `c?`, and empty on a replay, whose line already carries 📖."
    )


def prefix_reading(share: float | None, *, replayed: bool) -> PrefixReading:
    """*replayed* is passed beside *share*: ``cache_share`` folds a replay into ``None``, the unreported value."""
    if replayed:
        return PrefixReading(state="replayed", share=None, badge="")
    if share is None:
        return PrefixReading(state="unreported", share=None, badge="c?")
    return PrefixReading(
        state="discounted" if share > 0 else "cold", share=share, badge=f"c{share:.0%}"
    )


# A bucket each, never an exemption: folded into a neighbour, a kind's cost reads as that one's.
type TokenUsageKind = Literal["optimizer", "backend", "judge", "diagnostic", "bench"]

#: In DISPLAY ORDER.
SPEND_KIND_LABELS: Worded[TokenUsageKind, str] = Worded(
    {
        "backend": "Connector",
        "optimizer": "Optimizer",
        "judge": "Judge",
        "diagnostic": "Diagnostic",
        "bench": "Bench",
    }
)
if set(SPEND_KIND_LABELS) != set(get_args(TokenUsageKind.__value__)):
    raise RuntimeError("SPEND_KIND_LABELS is out of step with TokenUsageKind (domain/spend.py)")

# Whose wallet a send drew: the box's key under the account's coupon, or the account's own key.
KeySource = Literal["host", "user"]
KEY_SOURCE_LABELS: Worded[KeySource, str] = Worded({"host": "host key", "user": "own key"})


def draws_coupon(paid_by: KeySource | None) -> bool:
    """THE test of whether a send counts against the host's coupon; ``None`` is a replay no key paid."""
    return paid_by == "host"


# `search_incurred` is a controlled arm's budget: no sibling's cache or bench pass moves it.
CeilingMeter = Literal["bill", "search_incurred"]

RATE_PRICED_LABEL = "priced at our rate"

CEILING_METER_LABELS: Worded[CeilingMeter, str] = Worded(
    {
        "bill": f"billed + {RATE_PRICED_LABEL}",
        "search_incurred": "search incurred",
    }
)

# Prefixes a nested run's node on its outer ledger, so the outer rollup keeps those nodes apart.
NESTED_NODE_PREFIX = "inner:"


@dataclass(frozen=True, slots=True)
class LiftWord:
    """A budget axis's lift: ``None`` in memory, :data:`UNBOUNDED` on the wire (``Budget``'s
    validator and serializer translate), so its schema and TS type spell a lift by it, never null."""

    def __get_pydantic_json_schema__(
        self, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core)
        named = [arm for arm in schema["anyOf"] if arm != {"type": "null"}]
        return {**schema, "anyOf": [*named, {"const": UNBOUNDED, "type": "string"}]}


LIFTED = LiftWord()


class Budget(FrozenModel):
    """What bounds a run per axis: a named axis binds, ``unbounded`` lifts (read back as ``None``,
    no bound), and an omitted or null one inherits, as a null clears on every layer.
    ``usd`` is the ONE axis a head-to-head holds equal across its arms: cells, tokens, rounds and
    seconds are what each arm's money bought, and differ by arm."""

    usd: Annotated[NonNegativeWireFloat | None, LIFTED] = Field(
        default=None,
        description="The campaign's USD, in its ceiling meter: one book its live cycles share.",
    )
    tokens: Annotated[WireInt | None, LIFTED] = Field(
        default=None,
        ge=0,
        description="The campaign's billed tokens, shared like `usd`; the axis that still binds "
        "where a model has no rate on file.",
    )
    rounds: Annotated[WireInt | None, LIFTED] = Field(
        default=None,
        ge=0,
        description="Closed rounds after the origin, per cycle, read at each round boundary; "
        "0 measures the origin only.",
    )
    wall_clock_s: Annotated[PositiveWireFloat | None, LIFTED] = Field(
        default=None,
        description="Seconds ONE launch runs, its clock afresh at every launch; a remote cell "
        "still open at the edge is cut and the run stops `wall_clock`.",
    )

    def __init__(self, **axes: float | Unbounded | None) -> None:
        super().__init__(**axes)

    @model_validator(mode="before")
    @classmethod
    def _lifted(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        return {a: None if v == UNBOUNDED else v for a, v in data.items() if v is not None}

    @model_serializer(mode="wrap")
    def _named_only(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Named axes only, a lift spelled ``unbounded``: an unnamed one inherits on read back."""
        data: dict[str, object] = handler(self)
        return {
            axis: UNBOUNDED if value is None else value
            for axis, value in data.items()
            if axis in self.model_fields_set
        }

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """The wire omits what the input omits, so both directions are the fields' one schema."""
        return handler({key: value for key, value in core.items() if key != "serialization"})

    @property
    def named(self) -> frozenset[str]:
        return frozenset(self.model_fields_set)

    def _axes(self) -> frozenset[tuple[str, object]]:
        return frozenset((axis, getattr(self, axis)) for axis in self.model_fields_set)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Budget):
            return NotImplemented
        return self._axes() == other._axes()

    def __hash__(self) -> int:
        return hash(self._axes())

    def over(self, *layers: Budget) -> Budget:
        return Budget.model_validate(
            reduce(operator.or_, (b.model_dump() for b in (self, *layers)))
        )

    def only(self, axes: Iterable[str]) -> Budget:
        kept = frozenset(axes)
        return Budget.model_validate({a: v for a, v in self.model_dump().items() if a in kept})

    # The two reads of a Budget carrying an AMOUNT (metered, spent), which names both money axes.
    @property
    def usd_amount(self) -> float:
        if self.usd is None:
            raise ValueError(f"{self!r} carries no USD amount")
        return self.usd

    @property
    def token_amount(self) -> int:
        if self.tokens is None:
            raise ValueError(f"{self!r} carries no token amount")
        return self.tokens

    def less(self, *taken: Budget) -> Remainder:
        """What is LEFT per money axis this bound names once *taken* (amounts) is out."""
        usd, tokens = self.usd, self.tokens
        for amount in taken:
            usd = None if usd is None else usd - amount.usd_amount
            tokens = None if tokens is None else tokens - amount.token_amount
        return Remainder(usd, tokens)

    @classmethod
    def amount(cls, usd: float, tokens: int) -> Budget:
        return cls(usd=round(usd, 6), tokens=tokens)

    def plus(self, other: Budget) -> Budget:
        return Budget.amount(
            self.usd_amount + other.usd_amount, self.token_amount + other.token_amount
        )


class Remainder(NamedTuple):
    """What a bound still admits per money axis, below nothing once overdrawn: a provider bills
    after the send is admitted, so spend can pass a bound. ``None`` on an axis it leaves unbounded."""

    usd: float | None = None
    tokens: int | None = None

    @property
    def run_out(self) -> bool:
        """Nothing left on some axis it bounds."""
        return (self.usd is not None and self.usd <= 0) or (
            self.tokens is not None and self.tokens <= 0
        )

    @property
    def budget(self) -> Budget:
        """What is left as a bound: an overdraft bounds at nothing."""
        return Budget(
            usd=UNBOUNDED if self.usd is None else max(0.0, self.usd),
            tokens=UNBOUNDED if self.tokens is None else max(0, self.tokens),
        )

    def bound_over(self, spent: Budget) -> Budget:
        """The bound that leaves this past *spent* (an amount)."""
        room = self.budget
        return Budget(
            usd=UNBOUNDED if room.usd is None else room.usd + spent.usd_amount,
            tokens=UNBOUNDED if room.tokens is None else room.tokens + spent.token_amount,
        )


NOTHING_METERED = Budget.amount(0.0, 0)


class SpendCount(FrozenModel):
    """A spend book's count, by part, each an amount: the ceiling's stop and a send's room both
    read `out` beside `counted`."""

    billed: Budget = Field(description="Sent and billed, or priced off the rate table.")
    unreported: Budget = Field(
        description="Sends that ended with no bill, each counted at the bound it was held at."
    )
    set_aside: Budget = Field(description="Held back for the bench's passes.")
    out: Budget = Field(description="What the calls and cells still out hold.")

    @property
    def counted(self) -> Budget:
        # Unrounded, unlike `Budget.plus`: a send's room is compared against it to the cent's edge.
        parts = (self.billed, self.unreported, self.set_aside)
        return Budget(
            usd=sum(p.usd_amount for p in parts), tokens=sum(p.token_amount for p in parts)
        )

    def words(self, billed: str) -> str:
        """The parts of `counted` that hold money, *billed* naming what the first counts."""
        parts = (
            (self.billed, billed),
            (self.unreported, "unreported"),
            (self.set_aside, "set aside for the bench"),
        )
        return ", ".join(f"{usd_text(part.usd_amount)} {name}" for part, name in parts if part.usd)

    def plus(self, other: SpendCount) -> SpendCount:
        return SpendCount(**{part: mine.plus(getattr(other, part)) for part, mine in self})


#: The book a cap reads its used figure off: the campaign's ceiling its live cycles share, this
#: cycle's closed rounds, or this launch's clock, afresh at every launch.
CapBook = Literal["campaign", "cycle", "launch"]

CAP_BOOK_WORDS: Worded[CapBook, str] = Worded(
    {"campaign": "campaign book", "cycle": "this cycle", "launch": "this launch"}
)

#: Why a cap is armed below what was asked: the account's allowance (`quota.py::clamped_change`).
CapClamp = Literal["allowance"]

CAP_CLAMP_WORDS: Worded[CapClamp, str] = Worded({"allowance": "allowance"})


class CapAxisInfo(NamedTuple):
    title: str
    # The axis's name on a row that the section already heads as caps.
    short: str
    book: CapBook
    # What its book's figure is called beside the cap: spent, done, elapsed.
    used: str
    # What an operator types the axis in; the wire carries `scale` times it.
    unit: str
    scale: float
    # The input's step, in `unit`.
    step: float
    integer: bool
    # Where 0 is legal, a cap of 0 admits nothing more: the run halts after its current round.
    minimum: float | None
    exclusive_minimum: float | None


def _cap_axis(
    axis: str,
    title: str,
    short: str,
    book: CapBook,
    used: str,
    unit: str,
    scale: float,
    step: float,
) -> CapAxisInfo:
    """Integrality and floor are the axis's `Budget` field's, read off it."""
    field = Budget.model_fields[axis]
    ge = [float(m.ge) for m in field.metadata if hasattr(m, "ge")]
    gt = [float(m.gt) for m in field.metadata if hasattr(m, "gt")]
    number = get_args(get_args(field.annotation)[0])[0]
    return CapAxisInfo(
        title,
        short,
        book,
        used,
        unit,
        scale,
        step,
        number is int,
        ge[0] if ge else None,
        gt[0] if gt else None,
    )


type CapAxis = Literal["usd", "tokens", "rounds", "wall_clock_s"]

#: THE run cap axes, on every surface that sets or shows one.
CAP_AXIS_INFO: Worded[CapAxis, CapAxisInfo] = Worded(
    {
        "usd": _cap_axis("usd", "Spend cap", "Spend", "campaign", "spent", "USD", 1, 0.01),
        "tokens": _cap_axis(
            "tokens", "Token cap", "Tokens", "campaign", "spent", "tokens", 1, 1000
        ),
        "rounds": _cap_axis("rounds", "Round cap", "Rounds", "cycle", "done", "rounds", 1, 1),
        "wall_clock_s": _cap_axis(
            "wall_clock_s", "Wall-clock cap", "Clock", "launch", "elapsed", "minutes", 60, 5
        ),
    }
)
assert list(CAP_AXIS_INFO) == list(Budget.model_fields) == list(get_args(CapAxis.__value__))


def usd_text(usd: float) -> str:
    """A sum of money as a person reads it: the ``usd`` measurand's level (`money_places`)."""
    return level_text(usd, "usd")


def bill_text(usd: float, *, floor: bool) -> str:
    """A bill as a person reads it: `≥` where it understates (`bill_is_floor`)."""
    return f"{'≥' if floor else ''}{usd_text(usd)}"


def rate_priced_text(usd: float, *, priced: bool) -> str | None:
    """What our rate prices, said beside a bill and never inside it; null where no call was."""
    return f"{usd_text(usd)} {RATE_PRICED_LABEL}" if priced else None


def metered_line(meter: CeilingMeter, *, count: SpendCount, cap: Budget, share: Budget) -> str:
    """THE cycle's spend in its campaign's meter, said on every run, capped or not, a replay
    included: *count* is this cycle's, by part, *share* its part of the caps; tokens where they bind."""
    label, counted = CEILING_METER_LABELS[meter], count.counted
    usd = "" if share.usd is None else f" of its {usd_text(share.usd)} share"
    said = f"this cycle {usd_text(counted.usd_amount)}{usd}"
    if cap.tokens is not None:
        tokens = "" if share.tokens is None else f" of its {share.tokens:,} share"
        said += f", {counted.token_amount:,} tokens{tokens}"
    return f"{said} ({count.words(label) or label})"


def money_text(budget: Budget, *, on: Budget | None = None) -> str:
    """A budget's money axes in words, only those the face *on* bounds (its own by default)."""
    face = budget if on is None else on
    said = [
        *([usd_text(budget.usd)] if budget.usd is not None and face.usd is not None else []),
        *(
            [f"{budget.tokens:,} tokens"]
            if budget.tokens is not None and face.tokens is not None
            else []
        ),
    ]
    return " / ".join(said) or "unbounded"


class CellPrice(FrozenModel):
    """What one cell of a bank cost on its models, as the archive measured it."""

    usd: float = Field(
        description="Mean USD over the archive's newest rows of this bank and models."
    )
    seconds: float | None = Field(
        description="Mean seconds producing one took (`MeasuredCell.cost_s`, which a replay "
        "keeps); null where no priced row carried a timing."
    )
    cells: int = Field(description="Archive rows the means are over.")


# The campaign's: one book its live cycles share. The rest bind one cycle (`rounds`) or launch.
MoneyAxis = Literal["usd", "tokens"]
MONEY_AXES = frozenset(get_args(MoneyAxis))
CYCLE_AXES = frozenset({"rounds", "wall_clock_s"})
# What the coupon reserves of a run per axis its ceiling caps, at most the coupon's room: a run past
# a reserve this sized is past its own ceiling (`SpendBook.reserve_is_own`), not the allowance.
RESERVE_PER_CEILING = 2


def cycle_axes_only(budget: Budget) -> Budget:
    if money := budget.named & MONEY_AXES:
        raise ValueError(
            f"{', '.join(sorted(money))} bound the whole campaign, never one cycle: set it in the "
            "campaign's limits, or a launch's `budget`"
        )
    return budget


def money_axes_only(budget: Budget) -> Budget:
    if cycle := budget.named & CYCLE_AXES:
        raise ValueError(f"{', '.join(sorted(cycle))} bound one cycle, never the campaign")
    return budget


class HeldBudget(FrozenModel):
    """A launch past admission; ``standing`` is the axes an operator named, as asked: the account
    bounds a run through its job's reservation, never through a ceiling it writes."""

    admitted: Budget
    standing: Budget
    # The registry job holding its reservation.
    job_id: str
    step_rounds: int | None
    # Its share of the campaign's ceiling as admitted: lands on its cycle once that cycle is known.
    share: Budget


def bill_is_floor(unpriced_tokens: int) -> bool:
    return unpriced_tokens > 0


def bill_or_rate_usd(cost_usd: float | None, rate_priced_usd: float | None) -> float | None:
    """What a ceiling counts ONE call at; the two figures are never added into one called spent."""
    return cost_usd if cost_usd is not None else rate_priced_usd


def calls_rate_priced(rate_priced_usd: float) -> bool:
    return rate_priced_usd > 0


class UserSpend(NamedTuple):
    used_usd: float
    used_tokens: int
    unpriced_tokens: int
    unreported_usd: float = 0.0
    unreported_tokens: int = 0
    rate_priced_usd: float = 0.0

    @property
    def sent_usd(self) -> float:
        return self.used_usd + self.rate_priced_usd

    @property
    def at_most_usd(self) -> float:
        return self.sent_usd + self.unreported_usd

    @property
    def at_most_tokens(self) -> int:
        return self.used_tokens + self.unreported_tokens

    @property
    def sends_unreported(self) -> bool:
        return self.unreported_usd > 0

    def plus(self, other: UserSpend) -> UserSpend:
        return UserSpend(
            self.used_usd + other.used_usd,
            self.used_tokens + other.used_tokens,
            self.unpriced_tokens + other.unpriced_tokens,
            self.unreported_usd + other.unreported_usd,
            self.unreported_tokens + other.unreported_tokens,
            self.rate_priced_usd + other.rate_priced_usd,
        )


ZERO_SPEND = UserSpend(0.0, 0, 0)


class PaidSpend(NamedTuple):
    """Spend split by whose key paid it: the host's key draws the coupon, the account's own never."""

    host: UserSpend = ZERO_SPEND
    user: UserSpend = ZERO_SPEND

    @classmethod
    def of(cls, paid_by: KeySource, spent: UserSpend) -> PaidSpend:
        return cls(**{paid_by: spent})

    @property
    def total(self) -> UserSpend:
        return self.host.plus(self.user)

    def plus(self, other: PaidSpend) -> PaidSpend:
        return PaidSpend(self.host.plus(other.host), self.user.plus(other.user))


NOTHING_PAID = PaidSpend()


class Overshoot(FrozenModel):
    """The calls whose bills passed what their hold held, each drawn on every hold above it."""

    calls: int = Field(description="Calls billed past their hold.")
    usd: float = Field(description="What they billed past it, in USD.")

    def past(self, before: float, after: float) -> Overshoot:
        """A call's excess moved from *before* to *after*."""
        return Overshoot(calls=self.calls + (before == 0 < after), usd=self.usd + after - before)


NO_OVERSHOOT = Overshoot(calls=0, usd=0.0)


class LifetimeSpend(StrictModel):
    """What an account, or one campaign's share of it, was billed over its whole life."""

    bill: str = Field(
        description="What the providers REPORTED they billed, over every cycle, fork and "
        "forwarded inner run, plus what a deleted cycle banked, as a person reads it: `≥` where "
        "a token no rate prices makes it a floor. Never an estimate: a call priced off our rate "
        "table and a send whose bill never came are `caveats`."
    )
    billed_tokens: int = Field(
        description="Every token the sends billed, priced or not: what a token allowance counts."
    )
    caveats: list[str] = Field(
        description="What the allowance draws beyond the bill, a line each: the unpriced tokens, "
        "the rate-priced calls, the unreported sends; empty where the bill is the whole story."
    )

    @classmethod
    def of(cls, spent: UserSpend) -> LifetimeSpend:
        floor, priced = (
            bill_is_floor(spent.unpriced_tokens),
            calls_rate_priced(spent.rate_priced_usd),
        )
        rate_priced = rate_priced_text(spent.rate_priced_usd, priced=priced)
        return cls(
            billed_tokens=spent.used_tokens,
            bill=bill_text(spent.used_usd, floor=floor),
            caveats=[
                said
                for shown, said in (
                    (
                        floor,
                        f"{level_text(spent.unpriced_tokens, 'tokens')} billed by a model with no "
                        "known price: the dollar figure undercounts, so the token unit binds.",
                    ),
                    (
                        priced,
                        f"{rate_priced}: calls no provider reported a bill for, drawn at our rate.",
                    ),
                    (
                        spent.sends_unreported,
                        f"Up to {usd_text(spent.unreported_usd)} unreported: sends that ended "
                        "with no bill (cancelled, timed out, killed), drawn at the most they may "
                        "have cost.",
                    ),
                )
                if shown
            ],
        )


def spend_slot[K, M: StrictModel](held: dict[K, M], key: K, make: type[M]) -> M:
    """``held[key]``, made on first use. Never ``setdefault(key, M())``: that builds a model per
    record on a ledger fold's hot path."""
    slot = held.get(key)
    if slot is None:
        slot = held[key] = make()
    return slot


def replay_text(share: float) -> str:
    return f"{level_text(share, 'rate')} of the search replayed"


# The spend that FINDS a result, which a lift is priced in; a bench pass and a diagnostic grade it.
SEARCH_KINDS: tuple[TokenUsageKind, ...] = ("optimizer", "backend", "judge")

METER_KINDS: dict[CeilingMeter, tuple[TokenUsageKind, ...]] = {
    "bill": get_args(TokenUsageKind.__value__),
    "search_incurred": SEARCH_KINDS,
}

METER_PRICES_REPLAYS: dict[CeilingMeter, bool] = {"bill": False, "search_incurred": True}


def meter_usd[T: (float, float | None)](meter: CeilingMeter, *, sent: T, incurred: T) -> T:
    """THE meter rule: a ceiling counts what was sent, or under a replay-pricing meter all incurred."""
    return incurred if METER_PRICES_REPLAYS[meter] else sent
