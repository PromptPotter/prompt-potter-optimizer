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
    is_round_winner,
)


def _measurement_order(acr: dict[str, list[dict[str, Any]]]) -> list[int]:
    # Factual order samples were measured against this round's candidates.
    # Each candidate's results list preserves the order the adaptive queue
    # mechanism presented samples to it; the longest list survived farthest
    # under elimination and therefore carries the full sequence. Ties on
    # length break on candidate_id for deterministic projection.
    if not acr:
        return []
    longest_cid = max(acr, key=lambda k: (len(acr[k]), k))
    out: list[int] = []
    seen: set[int] = set()
    for r in acr[longest_cid]:
        sid = r.get("sample_id")
        if sid is None:
            continue
        sid_int = int(sid)
        if sid_int in seen:
            continue
        seen.add(sid_int)
        out.append(sid_int)
    return out


def build_round_summary(rr: RoundResult) -> RoundSummary:
    """One :class:`RoundSummary` from a closed-round :class:`RoundResult`.

    Winner detection rides the shared ``is_round_winner`` rule (also used by
    ``_build_scoreboard`` in ``application/optimization/cycle.py``): the
    candidate whose ``changes_description`` matches the round's elected label
    is the winner. This builder is the sole writer of the persisted
    ``is_winner`` flag on the summary row.
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
                is_winner=is_round_winner(changes, winner_label),
                evaluators={k: float(v) for k, v in (cd.get("evaluators") or {}).items()},
                changes_description=changes,
            )
        )
    selection = _measurement_order(rr.all_candidate_results or {})
    return RoundSummary(
        round=rr.round,
        accuracy=float(rr.accuracy),
        composite_fitness=float(rr.composite_fitness),
        candidates=candidates,
        selection=selection,
    )


__all__ = ["build_round_summary"]
