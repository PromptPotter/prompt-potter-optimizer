"""Per-round display summary for ``dashboard.json::rounds[]`` — the strict subset the chart, tree and sparkline consume.
Deep audit stays in ``round_NNNN.json``, fetched on demand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.dashboard_rows import RoundSummary, RoundSummaryCandidate
from promptpotter.domain.l4.proxies import PanelPrecision, panel_precision
from promptpotter.domain.results import RoundResult, is_electable, is_round_winner
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

# The ``ScoredCandidate`` fields each display model copies verbatim — its own field list
# minus the derived ``is_winner``. Deriving from ``model_fields`` keeps the copy set in
# lockstep with the model definition (add a field there, it flows here automatically).
_SUMMARY_INCLUDE = set(RoundSummaryCandidate.model_fields) - {"is_winner"}


def origin_rows_from_disk(cycle_dir: Path) -> list[dict[str, Any]]:
    """The round-0 per-cell ROWS — the control the L4 panel's precision read pairs against, and
    the sole reader of that file for it. Round 0 is never re-measured, so the cached rows ARE the
    control; and rows rather than a pre-extracted map, because the read wants two fields off each."""
    doc = read_json_tolerant(CycleLayout(Path(cycle_dir)).round_file(0))
    if not isinstance(doc, dict):
        return []
    acr = doc.get("all_candidate_results") or {}
    if not acr:
        return []
    origin_id = next(iter(acr))  # round 0 is the single origin arm
    rows = acr.get(origin_id) or []
    return list(rows) if isinstance(rows, list) else []


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


def _panel_precision(rr: RoundResult, origin_rows: list[dict[str, Any]]) -> PanelPrecision | None:
    """Read off ONE arm — the round's winner, else its best-scoring electable one."""
    # Every arm ran the same cells, so any measures the same instrument; picking the arm the reader
    # is already looking at keeps the precision attached to the interval it explains. `is_electable`
    # is the election's own admission rule, so a collapsed arm cannot supply the round's reading.
    if rr.round == 0:
        return None
    electable = [
        c
        for c in rr.candidate_scores
        if is_electable(c, rr.all_candidate_results.get(c.candidate_id, []))
    ]
    if not electable:
        return None
    subject = next(
        (c for c in electable if is_round_winner(c.candidate_id, rr.winner_id)),
        max(electable, key=lambda c: c.composite_fitness),
    )
    return panel_precision(rr.all_candidate_results.get(subject.candidate_id, []), origin_rows)


def build_round_summary(rr: RoundResult, origin_rows: list[dict[str, Any]]) -> RoundSummary:
    """One ``RoundSummary`` from a closed round — the sole writer of the persisted ``is_winner`` flag. ``health`` is
    COPIED from ``rr.health``: the projection renders the served verdict and never recomputes it."""
    # Both display models are strict name-subsets of ``ScoredCandidate`` plus the derived flags
    # below — so each is a ``model_dump(include=…)`` projection, not a hand-copy. The include-set
    # is the target model's OWN field list minus those: a field added to the target can't be
    # silently forgotten here (it just flows), and a field the source lacks fails loud at
    # construction. One field-list, spelled at the model.
    candidates = [
        RoundSummaryCandidate(
            **c.model_dump(include=_SUMMARY_INCLUDE),
            is_winner=is_round_winner(c.candidate_id, rr.winner_id),
        )
        for c in rr.candidate_scores
    ]
    selection = _measurement_order(rr.all_candidate_results)
    return RoundSummary(
        round=rr.round,
        accuracy=float(rr.accuracy),
        composite_fitness=float(rr.composite_fitness),
        ability=rr.ability,
        improved=None if rr.round == 0 else rr.improved,
        electable_count=None if rr.round == 0 else rr.electable_count,
        verdict_reason=None if rr.round == 0 else rr.verdict_reason,
        candidates=candidates,
        selection=selection,
        health=rr.health,
        overlap=rr.overlap,
        panel_precision=_panel_precision(rr, origin_rows),
    )


__all__ = ["build_round_summary", "origin_rows_from_disk"]
