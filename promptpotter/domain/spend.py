from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import reduce
from typing import TYPE_CHECKING, Literal, NamedTuple, get_args

from pydantic import (
    Field,
    GetJsonSchemaHandler,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from promptpotter.domain.strict_model import FrozenModel, StrictModel, WireFloat, WireInt
from promptpotter.domain.wire_record import ALWAYS, WireRecord
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    # Type-only: the record's module imports this one for `TokenUsageKind`.
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.domain.scoring import MeasuredCell


@dataclass(frozen=True, slots=True)
class StepUsage:
    input: int = field(default=0, metadata=ALWAYS)
    output: int = field(default=0, metadata=ALWAYS)
    estimated: bool = field(default=False, metadata=ALWAYS)
    # What the provider REPORTED it billed; absent where it reported nothing, never our rate's price.
    cost_usd: float | None = None
    rate_priced_usd: float | None = None
    model: str | None = None
    provider: str | None = None
    # The upstream host that answered, where `provider` names a gateway.
    served_by: str | None = None
    finish_reason: str | None = None
    reasoning: int | None = None
    # THEY discounted the call; the `cached` flag beside it says WE replayed it.
    cache_read: int | None = None
    # The counts above sum every request the node sent; these left and reported no usage (a 5xx).
    unreported_attempts: int = 0

    @classmethod
    def from_wire(cls, entry: Mapping[str, object]) -> StepUsage:
        return _STEP_USAGE.read(entry)

    def wire(self) -> dict[str, object]:
        return _STEP_USAGE.write(self)

    @property
    def account(self) -> TokenAccount:
        return TokenAccount(
            input=self.input,
            output=self.output,
            reasoning=self.reasoning or 0,
            cache_read=self.cache_read,
        )


_STEP_USAGE = WireRecord.of(
    StepUsage,
    "StepTokenUsage",
    "Per-LLM-node ``step_tokens`` entry — the WIRE spelling of :class:`TokenAccount`, which\n"
    "deserializes one. A ``NotRequired`` key is absent where the provider surfaced nothing.",
)


class TokenAccount(FrozenModel):
    """What ONE metered thing consumed: a provider round-trip, a measured row or a searchpoint.

    ``reasoning`` is a subset of ``output`` and both cache counts of ``input``, never further
    totals. A null ``cache_read`` means no breakdown was reported; ``0`` means one was, with no hit.
    """

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int | None = None
    #: No ``None`` arm: no backend reports one, so absence is not expressible here.
    cache_write: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output

    def __add__(self, other: TokenAccount) -> TokenAccount:
        reads = [r for r in (self.cache_read, other.cache_read) if r is not None]
        return TokenAccount(
            input=self.input + other.input,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
            cache_read=sum(reads) if reads else None,
            cache_write=self.cache_write + other.cache_write,
        )

    @classmethod
    def from_step_tokens(cls, step_tokens: Mapping[str, StepUsage]) -> TokenAccount | None:
        """``None`` where the row carries no entries: "reported nothing" is not "reported zero"."""
        if not step_tokens:
            return None
        return reduce(operator.add, (usage.account for usage in step_tokens.values()))

    @classmethod
    def from_measured_rows(cls, rows: Iterable[MeasuredCell]) -> TokenAccount | None:
        """REPLAYED rows are excluded: their counts are the banked call's, a discount this run never bought."""
        accounts = [
            a
            for r in rows
            if not r.cached and (a := cls.from_step_tokens(r.pipeline.step_tokens)) is not None
        ]
        if not accounts:
            return None
        return reduce(operator.add, accounts)

    @classmethod
    def from_payload(cls, usage: object) -> TokenAccount:
        """Unreadable degrades to an EMPTY account: a resume REPLAYS the ledger, so a record renders, never raises."""
        if not isinstance(usage, Mapping):
            return cls()
        try:
            return cls.model_validate(usage)
        except ValidationError:
            return cls()

    def cache_share(self, *, replayed: bool) -> float | None:
        """``None`` where unanswerable, a replay included (its counts are the banked row's); ``0.0`` is a MEASUREMENT."""
        if replayed or self.cache_read is None or self.input <= 0:
            return None
        return self.cache_read / self.input

    def prefix(self, *, replayed: bool) -> PrefixReading:
        return prefix_reading(self.cache_share(replayed=replayed), replayed=replayed)


PrefixState = Literal["discounted", "cold", "unreported", "replayed"]

PREFIX_STATE_TITLES: dict[PrefixState, str] = {
    "discounted": "The provider served this share of the input off its own prompt-prefix cache, "
    "billed at a discount. Unrelated to 📖, which means no provider was reached at all.",
    "cold": "The provider reported its cache accounting and served none of this input from it — "
    "the prefix was cold. A measurement, not a missing one.",
    "unreported": "This provider reported no cache accounting, so whether it collected the prefix "
    "is unknown. Not the same as no hit.",
    "replayed": "Replayed from our own archive — no provider was reached, so there is no discount "
    "to report.",
}
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
TokenUsageKind = Literal["optimizer", "backend", "judge", "diagnostic", "bench"]

#: In DISPLAY ORDER.
SPEND_KIND_LABELS: dict[TokenUsageKind, str] = {
    "backend": "Connector",
    "optimizer": "Optimizer",
    "judge": "Judge",
    "diagnostic": "Diagnostic",
    "bench": "Bench",
}
if set(SPEND_KIND_LABELS) != set(get_args(TokenUsageKind)):
    raise RuntimeError("SPEND_KIND_LABELS is out of step with TokenUsageKind (domain/spend.py)")

# `search_incurred` is a controlled arm's budget: no sibling's cache or bench pass moves it.
CeilingMeter = Literal["bill", "search_incurred"]

RATE_PRICED_LABEL = "priced at our rate"

CEILING_METER_LABELS: dict[CeilingMeter, str] = {
    "bill": f"billed + {RATE_PRICED_LABEL}",
    "search_incurred": "search incurred",
}

# A pass whose ROLE files its spend, whatever each of its calls would otherwise bank as.
ROLE_SPEND_KIND: dict[str, TokenUsageKind] = {MeasurementRole.BENCH: "bench"}

# Prefixes a nested run's node on its outer ledger, so the outer rollup keeps those nodes apart.
NESTED_NODE_PREFIX = "inner:"


class Budget(FrozenModel):
    """What bounds a run per axis: a named axis binds, a named null lifts, an omitted one inherits."""

    usd: WireFloat | None = Field(
        default=None,
        ge=0.0,
        description="The campaign's USD, in its ceiling meter: one book its live cycles share.",
    )
    tokens: WireInt | None = Field(
        default=None,
        ge=0,
        description="The campaign's billed tokens, shared like `usd`; the axis that still binds "
        "where a model has no rate on file.",
    )
    rounds: WireInt | None = Field(
        default=None,
        ge=0,
        description="Closed rounds after the origin, per cycle, read at each round boundary; "
        "0 measures the origin only.",
    )
    wall_clock_s: WireFloat | None = Field(
        default=None,
        gt=0.0,
        description="Seconds ONE launch runs, its clock afresh at every launch; a remote cell "
        "still open at the edge is cut and the run stops `wall_clock`.",
    )

    @model_serializer(mode="wrap")
    def _named_only(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """An unnamed axis written as ``null`` would read back as a lift nobody asked for."""
        data: dict[str, object] = handler(self)
        return {axis: value for axis, value in data.items() if axis in self.model_fields_set}

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
        values = {axis: getattr(self, axis) for axis in self.model_fields_set}
        for layer in layers:
            values |= {axis: getattr(layer, axis) for axis in layer.model_fields_set}
        return Budget(**values)

    def only(self, axes: Iterable[str]) -> Budget:
        return Budget(**{axis: getattr(self, axis) for axis in self.named.intersection(axes)})

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

    @classmethod
    def amount(cls, usd: float, tokens: int) -> Budget:
        return cls(usd=round(usd, 6), tokens=tokens)

    def plus(self, other: Budget) -> Budget:
        return Budget.amount(
            self.usd_amount + other.usd_amount, self.token_amount + other.token_amount
        )


NOTHING_METERED = Budget.amount(0.0, 0)

# The campaign's: one book its live cycles share. The rest bind one cycle (`rounds`) or launch.
MONEY_AXES = frozenset({"usd", "tokens"})
CYCLE_AXES = frozenset({"rounds", "wall_clock_s"})


def cycle_axes_only(budget: Budget) -> Budget:
    if money := budget.named & MONEY_AXES:
        raise ValueError(
            f"{', '.join(sorted(money))} bound the whole campaign, never one cycle: set it with "
            "`set-limits` or a launch's `--max-usd` / `--max-tokens`"
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


class LifetimeSpend(StrictModel):
    """What an account, or one campaign's share of it, was billed over its whole life."""

    billed_usd: float = Field(
        description="What the providers REPORTED they billed, over every cycle, fork and "
        "forwarded inner run, plus what a deleted cycle banked. Never an estimate: a call priced "
        "off our rate table is `rate_priced_usd`, a send whose bill never came `unreported_usd`."
    )
    rate_priced_usd: float = Field(
        description="What our rate table prices the calls no provider reported a cost for. Not "
        "spent — an estimate, shown beside `billed_usd` and never added into it. It binds the "
        "ceiling beside `billed_usd`."
    )
    calls_rate_priced: bool = Field(
        description="Some call was priced at our rate, so there is a `rate_priced_usd` to show "
        "beside the bill."
    )
    bill_is_floor: bool = Field(
        description="`billed_usd` and `rate_priced_usd` understate, by what `unpriced_tokens` "
        "cost; the token ceiling is then the binding one."
    )
    unpriced_tokens: int = Field(
        description="Tokens no provider billed and no rate on file prices."
    )
    sends_unreported: bool = Field(
        description="Some send ended with no bill, so up to `unreported_usd` more may have left "
        "the account than `billed_usd` says."
    )
    unreported_usd: float = Field(
        description="The most that sends which ended with no bill (cancelled, timed out, killed "
        "with a run) may have cost, at the bounds they were admitted on. Not spent — unknown. It "
        "binds the ceiling beside `billed_usd`."
    )

    @classmethod
    def of(cls, spent: UserSpend) -> LifetimeSpend:
        return cls(
            billed_usd=round(spent.used_usd, 6),
            rate_priced_usd=round(spent.rate_priced_usd, 6),
            calls_rate_priced=calls_rate_priced(spent.rate_priced_usd),
            bill_is_floor=bill_is_floor(spent.unpriced_tokens),
            unpriced_tokens=spent.unpriced_tokens,
            sends_unreported=spent.sends_unreported,
            unreported_usd=round(spent.unreported_usd, 6),
        )


class SpendBucket(StrictModel):
    """One spend sub-bucket: a spend kind's, or one node's.

    ``used_usd`` is the BILL providers reported, ``rate_priced_usd`` our rate table's price of the
    calls none billed, and ``incurred_usd`` counts both and prices cache hits too.
    """

    used_usd: float = 0.0
    rate_priced_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # A SUBSET of ``output_tokens``, never added into a total.
    reasoning_tokens: int = 0
    # SUBSETS of ``input_tokens``; a cached CALL reached no provider, these price part of one that did.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    rate_known: bool = False
    # No wire cost AND no rate on file: >0 means the USD cap is blind here and the token cap backstops.
    unpriced_tokens: int = 0

    incurred_usd: float = 0.0
    # >0 ⇒ ``incurred_usd`` UNDERSTATES, so anything dividing by it reads cheapness that never happened.
    incurred_unpriced_tokens: int = 0

    @property
    def sent_usd(self) -> float:
        """What a ``bill`` ceiling counts; never a figure called spent."""
        return round(self.used_usd + self.rate_priced_usd, 6)

    def metered_usd(self, meter: CeilingMeter) -> float:
        return meter_usd(meter, sent=self.sent_usd, incurred=self.incurred_usd)

    @property
    def cache_share(self) -> float | None:
        """A 0 sum reads as unreported: a sum cannot tell "no hit" from "no breakdown"."""
        return TokenAccount(
            input=self.input_tokens, cache_read=self.cache_read_tokens or None
        ).cache_share(replayed=False)

    @property
    def sent(self) -> bool:
        return self.sent_usd > 0 or self.input_tokens > 0

    @property
    def prefix(self) -> PrefixReading:
        """Cache WRITES ride the badge: writes with no reads pay for a prefix nothing collects."""
        reading = prefix_reading(self.cache_share, replayed=False)
        if not self.cache_write_tokens:
            return reading
        return reading.model_copy(update={"badge": f"{reading.badge} ·w{self.cache_write_tokens}"})

    def bank(self, record: TokenUsageRecord) -> None:
        usd = record.bill_or_rate_usd
        in_tok = int(record.input_tokens)
        out_tok = int(record.output_tokens)

        if usd is not None:
            self.incurred_usd = round(self.incurred_usd + usd, 6)
        elif in_tok or out_tok:
            self.incurred_unpriced_tokens += in_tok + out_tok

        if not record.cached:
            self.input_tokens += in_tok
            self.output_tokens += out_tok
            self.reasoning_tokens += int(record.reasoning_tokens)
            # Billed side only: a replay's cache tokens would report a prefix holding on calls never made.
            self.cache_read_tokens += int(record.cache_read_tokens)
            self.cache_write_tokens += int(record.cache_write_tokens)
            if record.cost_usd is not None:
                self.used_usd = round(self.used_usd + record.cost_usd, 6)
            elif record.rate_priced_usd is not None:
                self.rate_priced_usd = round(self.rate_priced_usd + record.rate_priced_usd, 6)
            if usd is not None:
                self.rate_known = True
            elif in_tok or out_tok:
                self.unpriced_tokens += in_tok + out_tok

    def absorb(self, other: SpendBucket) -> None:
        for name in SpendBucket.model_fields:
            held, added = getattr(self, name), getattr(other, name)
            if isinstance(held, bool):
                setattr(self, name, held or added)
            elif isinstance(held, float):
                setattr(self, name, round(held + added, 6))
            else:
                setattr(self, name, held + added)


def _bucket_per_kind() -> dict[TokenUsageKind, SpendBucket]:
    return {kind: SpendBucket() for kind in get_args(TokenUsageKind)}


def _bank_into[K](buckets: dict[K, SpendBucket], key: K, record: TokenUsageRecord) -> None:
    buckets.setdefault(key, SpendBucket()).bank(record)


def _absorb_into[K](buckets: dict[K, SpendBucket], other: Mapping[K, SpendBucket]) -> None:
    for key, theirs in other.items():
        buckets.setdefault(key, SpendBucket()).absorb(theirs)


class SpendRollup(StrictModel):
    """A cycle's spend: a bucket per spend kind, and the totals every consumer reads off them.

    A budget caps ``total_used_usd`` and ``total_rate_priced_usd`` together; ``total_incurred_usd``
    prices cache hits too.
    """

    by_kind: dict[TokenUsageKind, SpendBucket] = Field(default_factory=_bucket_per_kind)
    total_used_usd: float = 0.0
    total_rate_priced_usd: float = 0.0
    total_incurred_usd: float = 0.0
    # BILLED tokens only: a cap bounds what the run spends, not what a replay would have.
    total_tokens_used: int = 0
    # >0 means the two USD totals above are a floor, not the total.
    unpriced_tokens: int = 0
    # Folded in `bank`, never a `@computed_field`: that serializes but does not round-trip on resume.

    # Outside every total and off the wire: only `review.md` reads them.
    _by_role: dict[MeasurementRole | None, SpendBucket] = PrivateAttr(default_factory=dict)
    _by_node: dict[str, SpendBucket] = PrivateAttr(default_factory=dict)
    _by_nested_node: dict[str, SpendBucket] = PrivateAttr(default_factory=dict)

    @field_validator("by_kind")
    @classmethod
    def _every_kind(
        cls, held: dict[TokenUsageKind, SpendBucket]
    ) -> dict[TokenUsageKind, SpendBucket]:
        return _bucket_per_kind() | held

    def bank(self, record: TokenUsageRecord) -> None:
        self.by_kind[record.kind].bank(record)
        if record.node.startswith(NESTED_NODE_PREFIX):
            _bank_into(self._by_nested_node, record.node.removeprefix(NESTED_NODE_PREFIX), record)
        else:
            _bank_into(self._by_node, record.node, record)
            _bank_into(self._by_role, record.role, record)
        self._retotal()

    def absorb(self, other: SpendRollup) -> None:
        _absorb_into(self.by_kind, other.by_kind)
        _absorb_into(self._by_role, other.by_role)
        _absorb_into(self._by_node, other.by_node)
        _absorb_into(self._by_nested_node, other.by_nested_node)
        self._retotal()

    def _retotal(self) -> None:
        # Over every kind, never a hand-named pair: a bucket left out is spend the cap cannot see.
        buckets = self.by_kind.values()
        self.total_used_usd = round(sum(b.used_usd for b in buckets), 6)
        self.total_rate_priced_usd = round(sum(b.rate_priced_usd for b in buckets), 6)
        self.total_incurred_usd = round(sum(b.incurred_usd for b in buckets), 6)
        self.total_tokens_used = sum(b.input_tokens + b.output_tokens for b in buckets)
        self.unpriced_tokens = sum(b.unpriced_tokens for b in buckets)

    @property
    def by_role(self) -> Mapping[MeasurementRole | None, SpendBucket]:
        """This run's own calls by the scoring pass that paid them; ``None`` outside every pass."""
        return self._by_role

    @property
    def by_node(self) -> Mapping[str, SpendBucket]:
        """Per node: a pooled bucket hides the one whose scattering route reads a cold cache."""
        return self._by_node

    @property
    def by_nested_node(self) -> Mapping[str, SpendBucket]:
        """What nested runs billed onto this ledger, by THEIR node (``NESTED_NODE_PREFIX`` off)."""
        return self._by_nested_node

    @property
    def incurred_unpriced_tokens(self) -> int:
        """>0 ⇒ the incurred cost is understated, so an L4 cell dividing by it is refused."""
        return sum(b.incurred_unpriced_tokens for b in self.by_kind.values())

    @property
    def sent_usd(self) -> float:
        return round(self.total_used_usd + self.total_rate_priced_usd, 6)

    def metered(self, meters: CeilingMeter) -> Budget:
        counted = [self.by_kind[k] for k in METER_KINDS[meters]]
        return Budget.amount(
            sum(b.metered_usd(meters) for b in counted),
            sum(b.input_tokens + b.output_tokens for b in counted),
        )

    @property
    def search_replay_share(self) -> float | None:
        """``None`` where the search incurred nothing."""
        search = [self.by_kind[k] for k in SEARCH_KINDS]
        incurred = sum(b.incurred_usd for b in search)
        return None if incurred <= 0.0 else 1.0 - sum(b.sent_usd for b in search) / incurred

    def billed_beside_incurred(self) -> str:
        share = self.search_replay_share
        replayed = "" if share is None else f" · {share:.0%} of the search replayed"
        return (
            f"billed ${self.total_used_usd:.4f} · "
            f"{RATE_PRICED_LABEL} ${self.total_rate_priced_usd:.4f} · "
            f"incurred ${self.total_incurred_usd:.4f}{replayed}"
        )

    @property
    def search_incurred_usd(self) -> float | None:
        """``None`` on unpriced search tokens: an understated divisor reads as false cheapness."""
        search = [self.by_kind[k] for k in SEARCH_KINDS]
        if any(b.incurred_unpriced_tokens for b in search):
            return None
        return sum(b.incurred_usd for b in search)


class KindSpend(StrictModel):
    """One spend kind as every surface reads it: metered, billed, rate-priced and incurred."""

    # `False` ⇒ metered beside the cap, never inside it.
    counted: bool
    # `False` ⇒ no provider was reached for this kind, so it has no prefix reading to show.
    sent: bool
    metered_usd: float
    billed_usd: float
    rate_priced_usd: float
    incurred_usd: float
    tokens: int
    prefix: PrefixReading

    @classmethod
    def of(cls, bucket: SpendBucket, meter: CeilingMeter, *, counted: bool) -> KindSpend:
        return cls(
            counted=counted,
            sent=bucket.sent,
            metered_usd=bucket.metered_usd(meter),
            billed_usd=bucket.used_usd,
            rate_priced_usd=bucket.rate_priced_usd,
            incurred_usd=bucket.incurred_usd,
            tokens=bucket.input_tokens + bucket.output_tokens,
            prefix=bucket.prefix,
        )


class MeteredSpend(StrictModel):
    """What a run's spend caps have counted, in the units they meter, by kind."""

    meter: CeilingMeter
    metered_usd: float
    metered_tokens: int
    # The bill providers REPORTED, a replay free: the figure every surface leads with.
    billed_usd: float
    # Never spent, and counted by every cap.
    rate_priced_usd: float
    calls_rate_priced: bool
    # `False` ⇒ no USD figure here says anything, and a kind reads in tokens.
    rate_known: bool
    bill_is_floor: bool
    incurred_usd: float
    billed_tokens: int
    unpriced_tokens: int
    kinds: dict[TokenUsageKind, KindSpend]
    replay_share: float | None

    @classmethod
    def of(cls, spend: SpendRollup, meter: CeilingMeter) -> MeteredSpend:
        metered = spend.metered(meter)
        counted = METER_KINDS[meter]
        return cls(
            meter=meter,
            metered_usd=metered.usd_amount,
            metered_tokens=metered.token_amount,
            billed_usd=spend.total_used_usd,
            rate_priced_usd=spend.total_rate_priced_usd,
            calls_rate_priced=calls_rate_priced(spend.total_rate_priced_usd),
            rate_known=any(b.rate_known for b in spend.by_kind.values()),
            bill_is_floor=bill_is_floor(spend.unpriced_tokens),
            incurred_usd=spend.total_incurred_usd,
            billed_tokens=spend.total_tokens_used,
            unpriced_tokens=spend.unpriced_tokens,
            kinds={
                kind: KindSpend.of(b, meter, counted=kind in counted)
                for kind, b in spend.by_kind.items()
            },
            replay_share=spend.search_replay_share,
        )


# The spend that FINDS a result, which a lift is priced in; a bench pass and a diagnostic grade it.
SEARCH_KINDS: tuple[TokenUsageKind, ...] = ("optimizer", "backend", "judge")

METER_KINDS: dict[CeilingMeter, tuple[TokenUsageKind, ...]] = {
    "bill": get_args(TokenUsageKind),
    "search_incurred": SEARCH_KINDS,
}

METER_PRICES_REPLAYS: dict[CeilingMeter, bool] = {"bill": False, "search_incurred": True}


def meter_usd[T: (float, float | None)](meter: CeilingMeter, *, sent: T, incurred: T) -> T:
    """THE meter rule: a ceiling counts what was sent, or under a replay-pricing meter all incurred."""
    return incurred if METER_PRICES_REPLAYS[meter] else sent


def metered(record: TokenUsageRecord, meter: CeilingMeter) -> tuple[float | None, int]:
    """ONE call as a ceiling of *meter* counts it: USD ``None`` where nothing prices it."""
    if record.kind not in METER_KINDS[meter]:
        return 0.0, 0
    usd = meter_usd(
        meter,
        sent=0.0 if record.cached else record.bill_or_rate_usd,
        incurred=record.bill_or_rate_usd,
    )
    return usd, 0 if record.cached else record.input_tokens + record.output_tokens
