"""mask — a criterion projected over the realized lineage (backend, read-only).

Background infrastructure: one tree-recursive fold (``find_divergences``) over the
realized **record**, parameterized by a per-round **verdict** strategy. It finds
where an alternative criterion would have forked the record (the divergence point)
and marks the counterfactual descendant subtree — it never builds a second tree.
"mask" is internal naming; the operator sees only a served divergence overlay.

Design + invariants: ``docs/specs/mask-projection.md``.
"""

from __future__ import annotations

from promptpotter.application.mask.divergence import (
    Divergence,
    DivergenceResult,
    Verdict,
    VerdictOutcome,
    find_divergences,
)
from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
)
from promptpotter.application.mask.verdicts import make_scoring_verdict

__all__ = [
    "Divergence",
    "DivergenceResult",
    "MaskCandidate",
    "MaskCycle",
    "MaskRecord",
    "MaskRound",
    "Verdict",
    "VerdictOutcome",
    "find_divergences",
    "make_scoring_verdict",
]
