"""Per-round display summary for ``dashboard.json::rounds[]``.

Pure projection over a :class:`RoundResult` — emits the strict subset of
``ScoredCandidate`` fields the webapp's chart, lineage tree, and trend
sparkline consume. Deep audit (per-sample rows, full evaluator output,
prompt content) stays in ``.runtime/cache/rounds/round_NNNN.json`` and
is fetched on demand by the deep-inspection consumers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.l4.verdict import CandidateInfo, cell_fitness, compute_outer_verdict
from promptpotter.domain.results import (
    RoundResult,
    RoundSummary,
    RoundSummaryCandidate,
    is_round_winner,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

# The ``ScoredCandidate`` fields each display model copies verbatim — its own field list
# minus the derived ``is_winner``. Deriving from ``model_fields`` keeps the copy set in
# lockstep with the model definition (add a field there, it flows here automatically).
_SUMMARY_INCLUDE = set(RoundSummaryCandidate.model_fields) - {"is_winner"}
_CANDIDATE_INFO_INCLUDE = set(CandidateInfo.model_fields) - {"is_winner"}


def origin_cells_from_disk(cycle_dir: Path) -> dict[str, float]:
    """The round-0 (origin) per-cell composite off the cached ``rounds/round_0000.json`` —
    the un-edited-meta-prompt control every later round's outer verdict pairs against. The
    sole reader of this file for this purpose; ``application/meta_champion.py``
    reuses it instead of keeping its own copy."""
    doc = read_json_tolerant(CycleLayout(Path(cycle_dir)).round_file(0))
    if not isinstance(doc, dict):
        return {}
    acr = doc.get("all_candidate_results") or {}
    if not acr:
        return {}
    origin_id = next(iter(acr))  # round 0 is the single origin arm
    return cell_fitness(acr.get(origin_id) or [])


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


def build_round_summary(rr: RoundResult, origin_cells: dict[str, float]) -> RoundSummary:
    """One :class:`RoundSummary` from a closed-round :class:`RoundResult`.

    Winner detection rides the shared ``is_round_winner`` rule (also used by
    ``RoundResult.scoreboard``): the candidate whose ``changes_description``
    matches the round's elected label is the winner. This builder is the sole
    writer of the persisted ``is_winner`` flag on the summary row.

    ``health`` is copied straight from ``rr.health`` (stamped once at round close
    by ``compute_round_health``) — the projection renders the served verdict, it
    does not recompute it (R-36).

    *origin_cells* is the cached round-0 origin's per-cell composite (``{}`` off an
    L4 round, or when summarizing round 0 itself — the origin is the control, never a
    verdict subject).
    """
    # Both display models are strict name-subsets of ``ScoredCandidate`` plus a derived
    # ``is_winner`` — so each is a ``model_dump(include=…)`` projection, not a hand-copy.
    # The include-set is the target model's OWN field list minus ``is_winner``: a field
    # added to the target can't be silently forgotten here (it just flows), and a field
    # the source lacks fails loud at construction. One field-list, spelled at the model.
    candidates = [
        RoundSummaryCandidate(
            **c.model_dump(include=_SUMMARY_INCLUDE),
            is_winner=is_round_winner(c.candidate_id, rr.winner_id),
        )
        for c in rr.candidate_scores
    ]
    selection = _measurement_order(rr.all_candidate_results)
    verdict = (
        None
        if rr.round == 0
        else compute_outer_verdict(
            rr.all_candidate_results,
            [
                CandidateInfo(
                    **c.model_dump(include=_CANDIDATE_INFO_INCLUDE),
                    is_winner=is_round_winner(c.candidate_id, rr.winner_id),
                )
                for c in rr.candidate_scores
            ],
            origin_cells,
        )
    )
    return RoundSummary(
        round=rr.round,
        accuracy=float(rr.accuracy),
        composite_fitness=float(rr.composite_fitness),
        cumulative_theta=rr.cumulative_theta,
        calibration_model=rr.calibration_model,
        improved=None if rr.round == 0 else rr.improved,
        electable_count=None if rr.round == 0 else rr.electable_count,
        candidates=candidates,
        selection=selection,
        health=rr.health,
        outer_verdict=verdict,
    )


__all__ = ["build_round_summary", "origin_cells_from_disk"]
