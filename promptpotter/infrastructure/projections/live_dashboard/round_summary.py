"""Per-round display summary for ``dashboard.json::rounds[]``.

Pure projection over a :class:`RoundResult` — emits the strict subset of
``ScoredCandidate`` fields the webapp's chart, lineage tree, and trend
sparkline consume. Deep audit (per-sample rows, full evaluator output,
prompt content) stays in ``.runtime/cache/rounds/round_NNNN.json`` and
is fetched on demand by the deep-inspection consumers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.domain.outer_verdict import CandidateInfo, cell_fitness, compute_outer_verdict
from promptpotter.domain.results import (
    RoundResult,
    RoundSummary,
    RoundSummaryCandidate,
    is_round_winner,
)


def origin_cells_from_disk(cycle_dir: Path) -> dict[str, float]:
    """The round-0 (origin) per-cell composite off the cached ``rounds/round_0000.json`` —
    the un-edited-meta-prompt control every later round's outer verdict pairs against. The
    sole reader of this file for this purpose; ``application/meta_champion/reducer.py``
    reuses it instead of keeping its own copy."""
    origin_file = Path(cycle_dir) / "rounds" / "round_0000.json"
    if not origin_file.is_file():
        return {}
    try:
        doc = json.loads(origin_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
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
    ``_build_scoreboard`` in ``application/optimization/cycle.py``): the
    candidate whose ``changes_description`` matches the round's elected label
    is the winner. This builder is the sole writer of the persisted
    ``is_winner`` flag on the summary row.

    ``health`` is copied straight from ``rr.health`` (stamped once at round close
    by ``compute_round_health``) — the projection renders the served verdict, it
    does not recompute it (R-36).

    *origin_cells* is the cached round-0 origin's per-cell composite (``{}`` off an
    L4 round, or when summarizing round 0 itself — the origin is the control, never a
    verdict subject).
    """
    winner_label = rr.label
    candidates: list[RoundSummaryCandidate] = []
    for c in rr.candidate_scores:
        candidates.append(
            RoundSummaryCandidate(
                candidate_id=c.candidate_id,
                label=c.label,
                accuracy=c.accuracy,
                composite_fitness=c.composite_fitness,
                scored_samples=c.scored_samples,
                expected_samples=c.expected_samples,
                is_winner=is_round_winner(c.changes_description, winner_label),
                evaluators=dict(c.evaluators),
                changes_description=c.changes_description,
                partial_reason=c.partial_reason,
                theta=c.theta,
                theta_se=c.theta_se,
                composite_ci_lo=c.composite_ci_lo,
                composite_ci_hi=c.composite_ci_hi,
            )
        )
    selection = _measurement_order(rr.all_candidate_results or {})
    verdict = (
        None
        if rr.round == 0
        else compute_outer_verdict(
            rr.all_candidate_results or {},
            [
                CandidateInfo(
                    candidate_id=c.candidate_id,
                    label=c.label,
                    changes_description=c.changes_description,
                    composite_fitness=c.composite_fitness,
                    is_winner=is_round_winner(c.changes_description, winner_label),
                )
                for c in rr.candidate_scores
            ],
            winner_label,
            origin_cells,
        )
    )
    return RoundSummary(
        round=rr.round,
        accuracy=float(rr.accuracy),
        composite_fitness=float(rr.composite_fitness),
        cumulative_accuracy=float(rr.cumulative_accuracy),
        cumulative_theta=rr.cumulative_theta,
        candidates=candidates,
        selection=selection,
        health=rr.health,
        outer_verdict=verdict,
    )


__all__ = ["build_round_summary", "origin_cells_from_disk"]
