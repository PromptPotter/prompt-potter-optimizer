"""What a cycle's money looks like: the two sub-buckets and the totals every consumer reads.

Apart from ``results.py`` on purpose. Money is the one concern here that is not about rounds,
candidates or verdicts — and it is the concern a program reusing this engine is most likely to
want on its own terms, so the seam is a file rather than a section to carve out.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping
from functools import reduce
from typing import Literal, NotRequired, TypedDict, get_args

from pydantic import ConfigDict, Field, ValidationError

from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "TOKEN_KIND_BUCKET",
    "SpendBucket",
    "SpendRollup",
    "StepTokenUsage",
    "TokenAccount",
    "TokenUsageKind",
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
        (``live_dashboard/view.py::_bank_call``). ``None`` where no measured row carried one."""
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


TokenUsageKind = Literal["optimizer", "backend", "judge"]
"""Who spent it, and therefore which bucket it lands in. ``judge`` is a third arm rather than a
flavour of either: folded into ``loop`` an operator reads grading cost as optimizer cost, folded
into ``backend`` as the measured system's (``judges/CLAUDE.md`` § Scoring, never the optimizer
loop)."""


class SpendBucket(StrictModel):
    """One spend sub-bucket (backend, optimizer-loop, or judge). Mutated only by
    ``_handle_token_usage``. ``used_usd`` is the BILL; ``incurred_usd`` prices cache hits too."""

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
    model: str | None = None
    # Billed tokens whose USD cost could not be resolved (no wire cost AND no rate on file).
    # >0 means the USD cap is blind to real spend here; the token cap backstops.
    unpriced_tokens: int = 0

    incurred_usd: float = 0.0
    # >0 ⇒ ``incurred_usd`` UNDERSTATES what this search costs, so anything dividing by it
    # reads cheapness that never happened. The L4 no-evidence guard refuses such a cell.
    incurred_unpriced_tokens: int = 0


class SpendRollup(StrictModel):
    """A cycle's spend: the three buckets, and the totals every consumer reads off them.
    ``total_used_usd`` is the BILL a budget caps; ``total_incurred_usd`` prices cache hits too."""

    backend: SpendBucket = Field(default_factory=SpendBucket)
    loop: SpendBucket = Field(default_factory=SpendBucket)
    # Scoring's own LLM spend, kept apart from `loop` — see `TokenUsageKind`.
    judge: SpendBucket = Field(default_factory=SpendBucket)
    total_used_usd: float = 0.0
    total_incurred_usd: float = 0.0
    # Cumulative BILLED tokens across every bucket — the token halt probe's source. Cache hits are
    # excluded: a cap bounds what the run spends, not what it would have spent.
    total_tokens_used: int = 0
    # Billed tokens with no resolvable USD rate. >0 means ``total_used_usd`` UNDERSTATES real spend
    # — it is a floor, not the total.
    unpriced_tokens: int = 0
    # Both are FOLDED beside the USD totals (`live_dashboard/view.py::_handle_token_usage`), never
    # derived on read: a `@computed_field` serializes but does not round-trip, and a resume
    # re-folds this whole state off the ledger (`resolve_resume_state`) before carrying it.
    # Serving them is also what keeps the gauge and the halt gate one computation.

    @property
    def buckets(self) -> tuple[SpendBucket, ...]:
        """Every sub-bucket, in declaration order. The ONE walk each total folds over — a caller
        naming them by hand is how a new bucket lands on disk and is left out of the totals the
        budget gate reads, silently, in the direction that under-reports spend."""
        return tuple(getattr(self, attr) for attr in TOKEN_KIND_BUCKET.values())

    @property
    def incurred_unpriced_tokens(self) -> int:
        """Incurred-side twin of :attr:`unpriced_tokens`. >0 ⇒ the L4 efficiency proxy would divide by
        an understated cost and read cheapness that never happened, so such a cell is refused."""
        return sum(b.incurred_unpriced_tokens for b in self.buckets)


TOKEN_KIND_BUCKET: dict[TokenUsageKind, str] = {
    "optimizer": "loop",
    "backend": "backend",
    "judge": "judge",
}
"""Which :class:`SpendRollup` bucket each :data:`TokenUsageKind` lands in — declared once.

TOTAL over the Literal, asserted at import. A two-way ``if kind == "optimizer" else backend``
at the banking site is what this replaces: it does not fail when a third kind appears, it
silently files the new one under ``backend``."""

assert set(TOKEN_KIND_BUCKET) == set(get_args(TokenUsageKind)), (
    "TOKEN_KIND_BUCKET must be total over TokenUsageKind"
)
assert set(TOKEN_KIND_BUCKET.values()) <= set(SpendRollup.model_fields), (
    "TOKEN_KIND_BUCKET names a bucket SpendRollup does not declare"
)
