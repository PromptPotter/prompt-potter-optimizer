"""Per-round display summary for ``dashboard.json::rounds[]``.

Pure projection over a :class:`RoundResult` — emits the strict subset of
``CandidateScore`` fields the webapp's chart, lineage tree, and trend
sparkline consume. Deep audit (per-sample rows, full evaluator output,
prompt content) stays in ``.runtime/cache/rounds/round_NNNN.json`` and
is fetched on demand by the deep-inspection consumers.
"""

from __future__ import annotations

from typing import Any

from promptpotter.domain.results import (
    RoundResult,
    RoundSummary,
    RoundSummaryCandidate,
)


def build_round_summary(rr: RoundResult) -> RoundSummary:
    """One :class:`RoundSummary` from a closed-round :class:`RoundResult`.

    Winner detection follows ``_build_scoreboard`` in
    ``application/optimization/cycle.py``: the candidate whose
    ``changes_description`` matches the round's elected label is the
    winner. The round label is set by the picker; this builder is the
    sole writer of the persisted ``is_winner`` flag on the summary row.
    """
    winner_label = rr.label
    candidates: list[RoundSummaryCandidate] = []
    for c in rr.candidate_scores:
        cd: dict[str, Any] = c
        changes = str(cd.get("changes_description") or "")
        candidates.append(
            RoundSummaryCandidate(
                candidate_id=str(cd.get("candidate_id") or ""),
                label=str(cd.get("label") or ""),
                accuracy=float(cd.get("accuracy") or 0.0),
                composite_fitness=float(cd.get("composite_fitness") or 0.0),
                scored_samples=int(cd.get("scored_samples") or 0),
                expected_samples=int(cd.get("expected_samples") or 0),
                is_winner=bool(changes) and changes == winner_label,
                evaluators={k: float(v) for k, v in (cd.get("evaluators") or {}).items()},
                changes_description=changes,
            )
        )
    return RoundSummary(
        round=rr.round,
        accuracy=float(rr.accuracy),
        composite_fitness=float(rr.composite_fitness),
        candidates=candidates,
    )


__all__ = ["build_round_summary"]
