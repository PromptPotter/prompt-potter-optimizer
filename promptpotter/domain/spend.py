from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import reduce
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, get_args

from pydantic import (
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
)

from promptpotter.domain.strict_model import StrictModel, WireFloat, WireInt
from promptpotter.domain.wire_record import ALWAYS, WireRecord
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    # Type-only: the record's module imports this one for `TokenUsageKind`.
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.domain.scoring import MeasuredCell

__all__ = [
    "CEILING_METER_LABELS",
    "NESTED_NODE_PREFIX",
    "PREFIX_STATE_TITLES",
    "RATE_PRICED_LABEL",
    "ROLE_SPEND_KIND",
    "SEARCH_KINDS",
    "SPEND_KIND_LABELS",
    "CeilingMeter",
    "CloseSpend",
    "KindSpend",
    "MeteredSpend",
    "PrefixReading",
    "PrefixState",
    "SpendBucket",
    "SpendCeilings",
    "SpendRollup",
    "StepUsage",
    "TokenAccount",
    "TokenUsageKind",
    "bill_is_floor",
    "bill_or_rate_usd",
    "calls_rate_priced",
    "declare_ceiling",
    "prefix_reading",
]


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
    # The counts above are ONE sum over every attempt: nothing says which attempt billed what.
    attempts: int | None = None

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


class TokenAccount(StrictModel):
    """What ONE metered thing consumed: a provider round-trip, a measured row or a searchpoint.

    ``reasoning`` is a subset of ``output`` and both cache counts of ``input``, never further
    totals. A null ``cache_read`` means no breakdown was reported; ``0`` means one was, with no hit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

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


class PrefixReading(StrictModel):
    """A provider's prefix-cache discount as every surface reads it, served whole."""

    model_config = ConfigDict(frozen=True)

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


@dataclass(frozen=True, slots=True)
class SpendCeilings:
    """What a run may spend, in the two units spend is metered in; a null arm is unmetered."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    usd: Annotated[WireFloat, Field(ge=0.0)] | None = None
    tokens: Annotated[WireInt, Field(ge=0)] | None = None


def declare_ceiling(base: SpendCeilings, *layers: SpendCeilings) -> SpendCeilings:
    """Declaring is preference, never authority: admission applies the account bound to the result."""
    usd, tokens = base.usd, base.tokens
    for layer in layers:
        usd = usd if layer.usd is None else layer.usd
        tokens = tokens if layer.tokens is None else layer.tokens
    return SpendCeilings(usd, tokens)


def bill_is_floor(unpriced_tokens: int) -> bool:
    return unpriced_tokens > 0


def bill_or_rate_usd(cost_usd: float | None, rate_priced_usd: float | None) -> float | None:
    """What a ceiling counts ONE call at; the two figures are never added into one called spent."""
    return cost_usd if cost_usd is not None else rate_priced_usd


def calls_rate_priced(rate_priced_usd: float) -> bool:
    return rate_priced_usd > 0


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
        return self.incurred_usd if METER_PRICES_REPLAYS[meter] else self.sent_usd

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

    def metered(self, meters: CeilingMeter) -> tuple[float, int]:
        """USD, then billed tokens."""
        counted = [self.by_kind[k] for k in METER_KINDS[meters]]
        return (
            round(sum(b.metered_usd(meters) for b in counted), 6),
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
    # `metered_usd` IS `billed_usd` + `rate_priced_usd` under this meter.
    metered_is_bill: bool
    incurred_usd: float
    billed_tokens: int
    unpriced_tokens: int
    kinds: dict[TokenUsageKind, KindSpend]
    replay_share: float | None

    @classmethod
    def of(cls, spend: SpendRollup, meter: CeilingMeter) -> MeteredSpend:
        usd, tokens = spend.metered(meter)
        counted = METER_KINDS[meter]
        return cls(
            meter=meter,
            metered_usd=usd,
            metered_tokens=tokens,
            billed_usd=spend.total_used_usd,
            rate_priced_usd=spend.total_rate_priced_usd,
            calls_rate_priced=calls_rate_priced(spend.total_rate_priced_usd),
            rate_known=any(b.rate_known for b in spend.by_kind.values()),
            bill_is_floor=bill_is_floor(spend.unpriced_tokens),
            metered_is_bill=not METER_PRICES_REPLAYS[meter] and set(counted) == set(spend.by_kind),
            incurred_usd=spend.total_incurred_usd,
            billed_tokens=spend.total_tokens_used,
            unpriced_tokens=spend.unpriced_tokens,
            kinds={
                kind: KindSpend.of(b, meter, counted=kind in counted)
                for kind, b in spend.by_kind.items()
            },
            replay_share=spend.search_replay_share,
        )


class CloseSpend(StrictModel):
    """What a cycle's history had cost when a round closed, a fork's inherited prefix included."""

    model_config = ConfigDict(frozen=True)

    search_usd: float | None = Field(
        description="What the search incurred, replays priced (`SpendRollup.search_incurred_usd`). "
        "Null where a search call carries tokens no rate priced."
    )
    billed_usd: float = Field(description="What providers reported they billed.")
    rate_priced_usd: float = Field(
        description="What our rate table prices the calls no provider billed. Never spent."
    )
    calls: int = Field(description="Calls that reached a provider.")
    tokens: int = Field(description="Billed tokens.")
    worked_s: float = Field(
        description="Summed seconds of those calls — work, never clock: it exceeds the clock "
        "where cells ran concurrently."
    )


# The spend that FINDS a result, which a lift is priced in; a bench pass and a diagnostic grade it.
SEARCH_KINDS: tuple[TokenUsageKind, ...] = ("optimizer", "backend", "judge")

METER_KINDS: dict[CeilingMeter, tuple[TokenUsageKind, ...]] = {
    "bill": get_args(TokenUsageKind),
    "search_incurred": SEARCH_KINDS,
}

METER_PRICES_REPLAYS: dict[CeilingMeter, bool] = {"bill": False, "search_incurred": True}
