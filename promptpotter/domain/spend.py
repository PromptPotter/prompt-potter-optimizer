"""What a cycle's money looks like: the two sub-buckets and the totals every consumer reads.

Apart from ``results.py`` on purpose. Money is the one concern here that is not about rounds,
candidates or verdicts — and it is the concern a program reusing this engine is most likely to
want on its own terms, so the seam is a file rather than a section to carve out.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel

__all__ = ["TOKEN_KIND_BUCKET", "SpendBucket", "SpendRollup", "TokenUsageKind"]


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
    """A cycle's spend: the two buckets, and the totals every consumer reads off them.
    ``total_used_usd`` is the BILL a budget caps; ``total_incurred_usd`` prices cache hits too."""

    backend: SpendBucket = Field(default_factory=SpendBucket)
    loop: SpendBucket = Field(default_factory=SpendBucket)
    # Scoring's own LLM spend, kept apart from `loop` — see `TokenUsageKind`.
    judge: SpendBucket = Field(default_factory=SpendBucket)
    total_used_usd: float = 0.0
    total_incurred_usd: float = 0.0
    # Cumulative BILLED tokens across both buckets — the token halt probe's source. Cache hits are
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
