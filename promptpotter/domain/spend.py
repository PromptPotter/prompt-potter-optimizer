"""What a cycle's money looks like: the two sub-buckets and the totals every consumer reads.

Apart from ``results.py`` on purpose. Money is the one concern here that is not about rounds,
candidates or verdicts — and it is the concern a program reusing this engine is most likely to
want on its own terms, so the seam is a file rather than a section to carve out.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping
from functools import reduce
from typing import TYPE_CHECKING, Literal, NamedTuple, NotRequired, TypedDict, get_args

from pydantic import ConfigDict, Field, PrivateAttr, ValidationError, field_validator

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.instrument import MeasurementRole

if TYPE_CHECKING:
    # Type-only: the record's module imports this one for `TokenUsageKind`.
    from promptpotter.domain.run_records import TokenUsageRecord

__all__ = [
    "NESTED_NODE_PREFIX",
    "ROLE_SPEND_KIND",
    "SEARCH_KINDS",
    "BudgetChange",
    "CeilingMeter",
    "KindSpend",
    "MeteredSpend",
    "SpendBucket",
    "SpendCeilings",
    "SpendRollup",
    "StepTokenUsage",
    "TokenAccount",
    "TokenUsageKind",
    "bill_is_floor",
    "declare_ceiling",
]


def _count(value: object) -> int:
    # `bool` first: it is an `int` subclass, so a backend answering `true` meters as one token.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


class StepTokenUsage(TypedDict):
    """Per-LLM-node ``step_tokens`` entry — the WIRE spelling of :class:`TokenAccount`, which
    deserializes one. A ``NotRequired`` key is absent where the provider surfaced nothing."""

    input: int
    output: int
    estimated: bool
    cost_usd: NotRequired[float]
    model: NotRequired[str]
    provider: NotRequired[str]
    # WHICH upstream host answered, where `provider` names a gateway that routes onward; absent
    # for a provider that is its own host.
    served_by: NotRequired[str]
    finish_reason: NotRequired[str]
    reasoning: NotRequired[int]
    # Distinct from the `cached` flag beside it: that one says WE replayed the call, this one that
    # THEY discounted it. No `cache_write` peer — a field no producer sets is not a state.
    cache_read: NotRequired[int]


class TokenAccount(StrictModel):
    """What ONE metered thing consumed — a provider round-trip, a measured row, a searchpoint.

    Every subset stays a subset, never a further total: ``reasoning`` of ``output`` (the provider
    bills thinking as output), both cache counts of ``input`` — Anthropic included, whose client
    normalizes. ``cache_read=None`` means no breakdown was reported; ``0`` means one was and there
    was no hit."""

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
        # Summed, never averaged. Reads sum too; only both-absent stays absent.
        reads = [r for r in (self.cache_read, other.cache_read) if r is not None]
        return TokenAccount(
            input=self.input + other.input,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
            cache_read=sum(reads) if reads else None,
            cache_write=self.cache_write + other.cache_write,
        )

    @classmethod
    def from_step_entry(cls, entry: Mapping[str, object]) -> TokenAccount:
        return cls(
            input=_count(entry.get("input")),
            output=_count(entry.get("output")),
            reasoning=_count(entry.get("reasoning")),
            cache_read=_optional_count(entry.get("cache_read")),
        )

    @classmethod
    def from_step_tokens(cls, pipeline_data: Mapping[str, object] | None) -> TokenAccount | None:
        """A measured row's account, folded over its per-node entries — the only answer to what a
        cell cost in tokens, since nothing upstream of the entries carries a sum.

        A MIXED row folds to the pessimistic share: reads sum but the denominator stays every
        node's input, which is what the surfaces rendering it claim. ``None`` where the row carries
        no entries, so "reported nothing" stays distinct from "reported zero"."""
        raw = (pipeline_data or {}).get("step_tokens")
        if not isinstance(raw, Mapping):
            return None
        entries = [e for e in raw.values() if isinstance(e, Mapping)]
        if not entries:
            return None
        return reduce(operator.add, (cls.from_step_entry(e) for e in entries))

    @classmethod
    def from_measured_rows(cls, rows: Iterable[Mapping[str, object]]) -> TokenAccount | None:
        """A whole SEARCHPOINT's account — every cell it was measured on, folded.

        REPLAYED rows are excluded: their counts are the banked call's, so folding them in reports
        a prefix discount this run never bought. Same exclusion the spend buckets fold under
        (:meth:`SpendRollup.bank`). ``None`` where no measured row carried one."""
        accounts = [
            a
            for r in rows
            if not r.get("cached")
            and (a := cls.from_step_tokens(_as_mapping(r.get("pipeline_data")))) is not None
        ]
        if not accounts:
            return None
        return reduce(operator.add, accounts)

    @classmethod
    def from_payload(cls, usage: object) -> TokenAccount:
        """One account off a ledger ``llm_call`` payload — this model's own dump, read back.

        Anything else degrades to an EMPTY account. Not a compatibility shim: the ledger is a
        chronology a resume REPLAYS, so a record any build wrote is data this one has to render
        rather than die on. The live path holds the typed account and never comes through here."""
        if not isinstance(usage, Mapping):
            return cls()
        try:
            return cls.model_validate(usage)
        except ValidationError:
            return cls()

    def cache_share(self, *, replayed: bool) -> float | None:
        """Fraction of ``input`` the PROVIDER served off its own prefix cache — the ONE reading,
        so no surface decides for itself when the number means nothing.

        ``None`` wherever it is unanswerable, *replayed* included: OUR archive served that call, so
        the counts are the banked row's and a discount printed beside them claims one this run
        never got. ``0.0`` is a MEASUREMENT — a renderer wanting silence there tests truthiness,
        not ``is not None``."""
        if replayed or self.cache_read is None or self.input <= 0:
            return None
        return self.cache_read / self.input


TokenUsageKind = Literal["optimizer", "backend", "judge", "diagnostic", "bench"]
"""Who spent it, and therefore which bucket it lands in. ``judge`` is a third arm rather than a
flavour of either: folded into ``optimizer`` an operator reads grading cost as optimizer cost, folded
into ``backend`` as the measured system's (``judges/CLAUDE.md`` § Scoring, never the optimizer
loop). ``diagnostic`` is what a `verify` / `ab` / `noise-floor` spends — it answers a question ABOUT
the search rather than advancing it, so folding it into `backend` would report re-measuring a
candidate as the cost of finding one. ``bench`` is the held-out pass, the price of the headline
every optimizer is compared on. Each is a bucket and not an exemption: inside a ``bill`` ceiling,
because the loop fires both itself; a ``search_incurred`` one meters them beside it."""

CeilingMeter = Literal["bill", "search_incurred"]
"""What a run's spend ceiling counts. ``bill`` — every kind, billed, a replay free: the run's own
cost. ``search_incurred`` — ``SEARCH_KINDS`` alone, replays priced: a controlled arm's declared
budget, which a sibling arm's cache cannot stretch and its bench pass cannot eat into."""

ROLE_SPEND_KIND: dict[str, TokenUsageKind] = {MeasurementRole.BENCH: "bench"}
"""The scoring passes whose ROLE files their spend, whatever each call would otherwise bank as —
keyed by the ``MeasurementRole`` that also names the pass's archive run."""

NESTED_NODE_PREFIX = "inner:"
"""What a nested run's bill is filed under on its outer ledger, before its own node's name
(``spend_book.py::SpendBook.mirror``) — so the outer rollup can keep those nodes apart."""


class SpendCeilings(NamedTuple):
    """A ceiling in the two units spend is metered in; ``None`` on an arm means unmetered."""

    usd: float | None
    tokens: int | None


class BudgetChange(NamedTuple):
    """A move of those two ceilings; ``None`` leaves the arm untouched."""

    usd: float | None
    tokens: int | None


def declare_ceiling(base: SpendCeilings, *layers: BudgetChange) -> SpendCeilings:
    """Lay each declaration over *base*, per arm, the LAST set arm winning — raise or lower alike.
    Declaring is preference, never authority: the account bound is admission's, applied to what
    this returns, which is why no layer here has to be trusted only downward."""
    usd, tokens = base
    for layer in layers:
        usd = usd if layer.usd is None else layer.usd
        tokens = tokens if layer.tokens is None else layer.tokens
    return SpendCeilings(usd, tokens)


def bill_is_floor(unpriced_tokens: int) -> bool:
    """THE rule for "this USD bill understates": billed tokens no rate priced. Every served spend
    model stamps its ``bill_is_floor`` here, so no surface compares the count itself."""
    return unpriced_tokens > 0


class SpendBucket(StrictModel):
    """One spend sub-bucket — a spend kind's, or one node's.

    Mutated only through ``SpendRollup.bank`` / ``absorb``. ``used_usd`` is the BILL;
    ``incurred_usd`` prices cache hits too."""

    used_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # How much of ``output_tokens`` bought hidden reasoning rather than an answer — a SUBSET of
    # it, never added into a total. It answers latency, not money.
    reasoning_tokens: int = 0
    # How much of ``input_tokens`` the PROVIDER served from, and wrote to, its own prompt cache —
    # both SUBSETS of it, never added into a total. Distinct from a cached CALL, which reached no
    # provider at all: these price part of a call that did.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    rate_known: bool = False
    # Billed tokens whose USD cost could not be resolved (no wire cost AND no rate on file).
    # >0 means the USD cap is blind to real spend here; the token cap backstops.
    unpriced_tokens: int = 0

    incurred_usd: float = 0.0
    # >0 ⇒ ``incurred_usd`` UNDERSTATES what this search costs, so anything dividing by it
    # reads cheapness that never happened. The L4 no-evidence guard refuses such a cell.
    incurred_unpriced_tokens: int = 0

    def metered_usd(self, meter: CeilingMeter) -> float:
        return self.incurred_usd if METER_PRICES_REPLAYS[meter] else self.used_usd

    @property
    def cache_share(self) -> float | None:
        """:meth:`TokenAccount.cache_share` over the bucket. It folds billed calls alone, so no
        replay arm applies; a 0 sum reads as unreported, since a sum cannot tell the two apart."""
        return TokenAccount(
            input=self.input_tokens, cache_read=self.cache_read_tokens or None
        ).cache_share(replayed=False)

    def bank(self, record: TokenUsageRecord) -> None:
        usd = record.cost_usd
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
            # Only the billed side: a reuse-cache hit reached no provider, so counting its
            # replayed cache tokens would report a prefix holding on calls never made.
            self.cache_read_tokens += int(record.cache_read_tokens)
            self.cache_write_tokens += int(record.cache_write_tokens)
            if usd is not None:
                self.used_usd = round(self.used_usd + usd, 6)
                self.rate_known = True
            elif in_tok or out_tok:
                # Billed but with no resolvable cost, so the USD cap cannot see this spend.
                # Tracked so the dashboard flags the cap as inactive.
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
    ``total_used_usd`` is the BILL a budget caps; ``total_incurred_usd`` prices cache hits too."""

    # TOTAL over `TokenUsageKind`, whatever a caller hands in — the totals fold over it.
    by_kind: dict[TokenUsageKind, SpendBucket] = Field(default_factory=_bucket_per_kind)
    total_used_usd: float = 0.0
    total_incurred_usd: float = 0.0
    # Cumulative BILLED tokens across every bucket — the token halt probe's source. Cache hits are
    # excluded: a cap bounds what the run spends, not what it would have spent.
    total_tokens_used: int = 0
    # Billed tokens with no resolvable USD rate. >0 means ``total_used_usd`` UNDERSTATES real spend
    # — it is a floor, not the total.
    unpriced_tokens: int = 0
    # Both are FOLDED beside the USD totals (`bank`), never derived on read: a `@computed_field`
    # serializes but does not round-trip, and a resume re-folds this whole state off the ledger
    # (`resolve_resume_state`) before carrying it. Serving them is also what keeps the gauge and
    # the halt gate one computation.

    # The same calls again, outside every total and off the wire: only `review.md` reads them, off
    # a ledger fold. A nested run's bill is its own table, since its nodes and roles are not ours.
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
        """One call, at the price it carries, into its bucket and the totals — the ONE fold, so a
        cycle's live rollup and a ledger re-read of it cannot disagree."""
        self.by_kind[record.kind].bank(record)
        if record.node.startswith(NESTED_NODE_PREFIX):
            _bank_into(self._by_nested_node, record.node.removeprefix(NESTED_NODE_PREFIX), record)
        else:
            _bank_into(self._by_node, record.node, record)
            _bank_into(self._by_role, record.role, record)
        self._retotal()

    def absorb(self, other: SpendRollup) -> None:
        """Add *other* — another ledger's whole rollup — bucket by bucket. A line's spend is the
        sum over its ledgers, each folded once."""
        _absorb_into(self.by_kind, other.by_kind)
        _absorb_into(self._by_role, other.by_role)
        _absorb_into(self._by_node, other.by_node)
        _absorb_into(self._by_nested_node, other.by_nested_node)
        self._retotal()

    def _retotal(self) -> None:
        # Over every kind, never a hand-named pair: the budget gate reads `total_used_usd`, so a
        # bucket left out of this fold is spend the cap cannot see.
        buckets = self.by_kind.values()
        self.total_used_usd = round(sum(b.used_usd for b in buckets), 6)
        self.total_incurred_usd = round(sum(b.incurred_usd for b in buckets), 6)
        self.total_tokens_used = sum(b.input_tokens + b.output_tokens for b in buckets)
        self.unpriced_tokens = sum(b.unpriced_tokens for b in buckets)

    @property
    def by_role(self) -> Mapping[MeasurementRole | None, SpendBucket]:
        """This run's own calls by the scoring pass that paid them; ``None`` outside every pass."""
        return self._by_role

    @property
    def by_node(self) -> Mapping[str, SpendBucket]:
        """This run's own calls by the node that made them: a node whose route scatters reads as
        a cold cache share beside its spend, which a bucket pooling nodes hides."""
        return self._by_node

    @property
    def by_nested_node(self) -> Mapping[str, SpendBucket]:
        """What nested runs billed onto this ledger, by THEIR node (``NESTED_NODE_PREFIX`` off)."""
        return self._by_nested_node

    @property
    def incurred_unpriced_tokens(self) -> int:
        """Incurred-side twin of :attr:`unpriced_tokens`. >0 ⇒ the L4 efficiency proxy would divide by
        an understated cost and read cheapness that never happened, so such a cell is refused."""
        return sum(b.incurred_unpriced_tokens for b in self.by_kind.values())

    def metered(self, meters: CeilingMeter) -> tuple[float, int]:
        """What a ceiling of ``meters`` has already counted: USD, then billed tokens."""
        counted = [self.by_kind[k] for k in METER_KINDS[meters]]
        return (
            round(sum(b.metered_usd(meters) for b in counted), 6),
            sum(b.input_tokens + b.output_tokens for b in counted),
        )

    @property
    def search_replay_share(self) -> float | None:
        """The share of the search's incurred USD a replay answered — billed nothing, since a
        cache another run paid into served it. ``None`` where the search incurred nothing."""
        search = [self.by_kind[k] for k in SEARCH_KINDS]
        incurred = sum(b.incurred_usd for b in search)
        return None if incurred <= 0.0 else 1.0 - sum(b.used_usd for b in search) / incurred

    def billed_beside_incurred(self) -> str:
        """The one text reading of the two totals, so no surface prints a bare "cost"."""
        share = self.search_replay_share
        replayed = "" if share is None else f" · {share:.0%} of the search replayed"
        return (
            f"billed ${self.total_used_usd:.4f} · incurred ${self.total_incurred_usd:.4f}{replayed}"
        )

    @property
    def search_incurred_usd(self) -> float | None:
        """What the SEARCH incurred (``SEARCH_KINDS``), replays priced — ``None`` where a search
        bucket carries tokens no rate priced, since dividing by an understated cost reads cheapness
        nobody bought."""
        search = [self.by_kind[k] for k in SEARCH_KINDS]
        if any(b.incurred_unpriced_tokens for b in search):
            return None
        return sum(b.incurred_usd for b in search)


class KindSpend(StrictModel):
    """One spend kind as every surface reads it: in the meter's units, beside its bill and what it
    incurred, with its billed tokens and its provider prefix-cache reading."""

    # Whether the cap's meter counts this kind; `False` ⇒ metered beside the cap, never inside it.
    counted: bool
    metered_usd: float
    billed_usd: float
    incurred_usd: float
    tokens: int
    cache_share: float | None
    cache_write_tokens: int
    rate_known: bool

    @classmethod
    def of(cls, bucket: SpendBucket, meter: CeilingMeter, *, counted: bool) -> KindSpend:
        return cls(
            counted=counted,
            metered_usd=bucket.metered_usd(meter),
            billed_usd=bucket.used_usd,
            incurred_usd=bucket.incurred_usd,
            tokens=bucket.input_tokens + bucket.output_tokens,
            cache_share=bucket.cache_share,
            cache_write_tokens=bucket.cache_write_tokens,
            rate_known=bucket.rate_known,
        )


class MeteredSpend(StrictModel):
    """What a run's spend caps have counted, in the units they meter, by kind.
    Every surface sets this beside a cap, so none picks the bill or the incurred total itself."""

    meter: CeilingMeter
    # What the meter's counted kinds sum to; `metered_tokens` are their billed tokens.
    metered_usd: float
    metered_tokens: int
    # THE spend every surface leads with: the providers' bill over every kind, a replay free.
    # `metered_usd` is read only beside a cap, `incurred_usd` only in the breakdown under this.
    billed_usd: float
    # `billed_usd` understates, by what `unpriced_tokens` cost: the USD cap cannot see past it.
    bill_is_floor: bool
    # `metered_usd` IS `billed_usd` under this meter, so a cap needs no second figure beside it.
    metered_is_bill: bool
    incurred_usd: float
    billed_tokens: int
    unpriced_tokens: int
    # Total over every kind; `replay_share` is the search's, `None` where it incurred nothing.
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


SEARCH_KINDS: tuple[TokenUsageKind, ...] = ("optimizer", "backend", "judge")
"""The spend that FINDS a result — what a lift is priced in. The bench's pass is the instrument
grading the result, and a diagnostic asks a question about it; neither is the search's."""

METER_KINDS: dict[CeilingMeter, tuple[TokenUsageKind, ...]] = {
    "bill": get_args(TokenUsageKind),
    "search_incurred": SEARCH_KINDS,
}

METER_PRICES_REPLAYS: dict[CeilingMeter, bool] = {"bill": False, "search_incurred": True}
"""Whether a meter counts a replayed call at the price it would have billed. Total over
``CeilingMeter`` beside ``METER_KINDS``, so a meter declared in neither raises where it is read."""
