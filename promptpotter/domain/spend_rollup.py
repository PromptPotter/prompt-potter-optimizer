"""A ledger's sends folded: by kind, by round and by meter, and the spend a stop banks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import get_args

from pydantic import (
    Field,
    PrivateAttr,
    field_validator,
)

from promptpotter.domain.records.money import TokenUsageRecord
from promptpotter.domain.spend import (
    CEILING_METER_LABELS,
    METER_KINDS,
    NESTED_NODE_PREFIX,
    RATE_PRICED_LABEL,
    SEARCH_KINDS,
    SPEND_KIND_LABELS,
    Budget,
    CeilingMeter,
    PrefixReading,
    SpendCount,
    TokenAccount,
    TokenUsageKind,
    bill_is_floor,
    bill_text,
    calls_rate_priced,
    meter_usd,
    metered_line,
    prefix_reading,
    rate_priced_text,
    replay_text,
    spend_slot,
    usd_text,
)
from promptpotter.domain.strict_model import (
    FrozenModel,
    StrictModel,
)
from promptpotter.shared.measurement_context import MeasurementRole


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
        price = record.price
        usd = price.bill_or_rate_usd
        in_tok = int(record.input_tokens)
        out_tok = int(record.output_tokens)

        if usd is not None:
            self.incurred_usd = round(self.incurred_usd + usd, 6)
        elif in_tok or out_tok:
            self.incurred_unpriced_tokens += in_tok + out_tok

        if record.bill is not None:
            self.input_tokens += in_tok
            self.output_tokens += out_tok
            # A sum over the parts REPORTED: an unreported part adds nothing, so each is a lower bound.
            self.reasoning_tokens += record.reasoning_tokens or 0
            # Billed side only: a replay's cache tokens would report a prefix holding on calls never made.
            self.cache_read_tokens += record.cache_read_tokens or 0
            self.cache_write_tokens += record.cache_write_tokens or 0
            if price.cost_usd is not None:
                self.used_usd = round(self.used_usd + price.cost_usd, 6)
            elif price.rate_priced_usd is not None:
                self.rate_priced_usd = round(self.rate_priced_usd + price.rate_priced_usd, 6)
            if usd is None and (in_tok or out_tok):
                self.unpriced_tokens += in_tok + out_tok

    def absorb(self, other: SpendBucket) -> None:
        for name in SpendBucket.model_fields:
            held, added = getattr(self, name), getattr(other, name)
            if isinstance(held, float):
                setattr(self, name, round(held + added, 6))
            else:
                setattr(self, name, held + added)


def _bucket_per_kind() -> dict[TokenUsageKind, SpendBucket]:
    return {kind: SpendBucket() for kind in get_args(TokenUsageKind.__value__)}


def _bank_into[K](buckets: dict[K, SpendBucket], key: K, record: TokenUsageRecord) -> None:
    spend_slot(buckets, key, SpendBucket).bank(record)


def _copied[K](buckets: dict[K, SpendBucket], key: K) -> dict[K, SpendBucket]:
    out = dict(buckets)
    if (bucket := out.get(key)) is not None:
        out[key] = bucket.model_copy()
    return out


def _absorb_into[K](buckets: dict[K, SpendBucket], other: Mapping[K, SpendBucket]) -> None:
    for key, theirs in other.items():
        spend_slot(buckets, key, SpendBucket).absorb(theirs)


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

    # Outside every total and off the wire: only the readout's stop reads them. A `{}` default, never
    # `default_factory`: pydantic signature-inspects a private factory on every instance.
    _by_role: dict[MeasurementRole | None, SpendBucket] = PrivateAttr(default={})
    _by_node: dict[str, SpendBucket] = PrivateAttr(default={})
    _by_nested_node: dict[str, SpendBucket] = PrivateAttr(default={})

    @field_validator("by_kind")
    @classmethod
    def _every_kind(
        cls, held: dict[TokenUsageKind, SpendBucket]
    ) -> dict[TokenUsageKind, SpendBucket]:
        return _bucket_per_kind() | held

    def bank(self, record: TokenUsageRecord) -> None:
        self.by_kind[record.price.kind].bank(record)
        if record.node.startswith(NESTED_NODE_PREFIX):
            _bank_into(self._by_nested_node, record.node.removeprefix(NESTED_NODE_PREFIX), record)
        else:
            _bank_into(self._by_node, record.node, record)
            _bank_into(self._by_role, record.role, record)
        self._retotal()

    def banked(self, record: TokenUsageRecord) -> SpendRollup:
        """*record* banked into a copy whose touched buckets are copied first: the rest is shared."""
        node = record.node.removeprefix(NESTED_NODE_PREFIX)
        out = self.model_copy(update={"by_kind": _copied(self.by_kind, record.price.kind)})
        out._by_role = _copied(self._by_role, record.role)
        out._by_node = _copied(self._by_node, node)
        out._by_nested_node = _copied(self._by_nested_node, node)
        out.bank(record)
        return out

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
        replayed = "" if share is None else f" · {replay_text(share)}"
        return (
            f"billed {usd_text(self.total_used_usd)} · "
            f"{RATE_PRICED_LABEL} {usd_text(self.total_rate_priced_usd)} · "
            f"incurred {usd_text(self.total_incurred_usd)}{replayed}"
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
    """What a run's spend caps have counted, in the units they meter, by kind, and its words."""

    meter: CeilingMeter
    metered_usd: float
    # The bill providers REPORTED, a replay free: the figure every surface leads with.
    billed_usd: float
    # Never spent, and counted by every cap.
    rate_priced_usd: float
    incurred_usd: float
    billed_tokens: int
    kinds: dict[TokenUsageKind, KindSpend]
    replay_share: float | None
    bill: str = Field(description="The bill as a person reads it: `≥` where it is a floor.")
    rate_priced_line: str | None = Field(
        description="What our rate priced, said beside the bill; null where no call was."
    )
    replay_line: str = Field(description="What share of the search a replay served free.")
    buckets_line: str = Field(
        description="Each kind in the meter's units: those inside the caps, then those beside."
    )

    @classmethod
    def of(cls, spend: SpendRollup, meter: CeilingMeter) -> MeteredSpend:
        counted = METER_KINDS[meter]
        kinds = {
            kind: KindSpend.of(b, meter, counted=kind in counted)
            for kind, b in spend.by_kind.items()
        }
        share = spend.search_replay_share
        return cls(
            meter=meter,
            metered_usd=spend.metered(meter).usd_amount,
            billed_usd=spend.total_used_usd,
            rate_priced_usd=spend.total_rate_priced_usd,
            incurred_usd=spend.total_incurred_usd,
            billed_tokens=spend.total_tokens_used,
            kinds=kinds,
            replay_share=share,
            bill=bill_text(spend.total_used_usd, floor=bill_is_floor(spend.unpriced_tokens)),
            rate_priced_line=rate_priced_text(
                spend.total_rate_priced_usd,
                priced=calls_rate_priced(spend.total_rate_priced_usd),
            ),
            replay_line="The search incurred nothing, so no share of it replayed"
            if share is None
            else replay_text(share),
            buckets_line=_buckets_line(meter, kinds),
        )


def _buckets_line(meter: CeilingMeter, kinds: Mapping[TokenUsageKind, KindSpend]) -> str:
    def said(inside: bool) -> str:
        return " · ".join(
            f"{label.lower()} {usd_text(kinds[key].metered_usd)}"
            for key, label in SPEND_KIND_LABELS.items()
            if kinds[key].counted is inside
        )

    inside, beside = said(True), said(False)
    return f"{CEILING_METER_LABELS[meter]}: {inside}" + (f" — beside: {beside}" if beside else "")


class StopSpend(FrozenModel):
    """A stop's spend: the caps bound the CAMPAIGN, so they pair with its count; the cycle's beside."""

    # The run's facts, banked: a read meters them (`metered`), so no word is banked.
    spent: SpendRollup
    meter: CeilingMeter
    # The stopping cycle's book, and its campaign's: every cycle's (`served_dashboard.spend_counts`).
    cycle: SpendCount
    campaign: SpendCount
    cap: Budget
    # This cycle's part of the caps, what its run's book bound at the stop.
    share: Budget

    @property
    def metered(self) -> MeteredSpend:
        return MeteredSpend.of(self.spent, self.meter)

    def meter_lines(self) -> list[str]:
        """The metered line, the cycle's count, then a line per cap the CAMPAIGN's count (every
        cycle's book) is set against."""
        meter, cap = self.meter, self.cap
        label, counted = CEILING_METER_LABELS[meter], self.campaign.counted
        lines = [metered_line(meter, count=self.cycle, cap=cap, share=self.share)]
        if cap.usd is not None:
            said = self.campaign.words(label) or label
            lines.append(
                f"campaign cap {usd_text(counted.usd_amount)} of {usd_text(cap.usd)} ({said})"
            )
        if cap.tokens is not None:
            lines.append(f"campaign cap {counted.token_amount:,} of {cap.tokens:,} tokens {label}")
        return lines


def metered(record: TokenUsageRecord, meter: CeilingMeter) -> tuple[float | None, int]:
    """ONE call as a ceiling of *meter* counts it: USD ``None`` where nothing prices it."""
    price = record.price
    if price.kind not in METER_KINDS[meter]:
        return 0.0, 0
    if record.bill is None:
        return meter_usd(meter, sent=0.0, incurred=price.bill_or_rate_usd), 0
    usd = price.bill_or_rate_usd
    return meter_usd(meter, sent=usd, incurred=usd), record.input_tokens + record.output_tokens
