"""Snapshot-record mutations — the per-round candidate dict + backfill log.

``LiveDashboardView._handle_snapshot`` is a pure router: each ``SnapshotRecord``
event fans out to one function here. Every function mutates a dict in place
(the round block, the shared :class:`LiveStateCore`, or the dashboard state)
and returns ``None`` — the view persists the merged result. No view instance
state is touched; the dicts are passed explicitly so the seam stays testable
through the existing projection tests.
"""

from __future__ import annotations

from typing import Any

from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    top_n_p_best,
)


def candidate_slot(round_dict: dict[str, Any], idx: int, total: int = 0) -> dict[str, Any]:
    """Lazy-init a candidate slot. Sample/score/p_best callbacks may fire
    before ``candidate_started`` seeds the slot, so all mutators funnel here.

    ``changes_description`` holds the human-readable mutation text; the
    canonical display ``label`` is composed in ``build_l1_score_block`` from
    the slot's round + idx via :func:`candidate_label`.
    """
    slot: dict[str, Any] = round_dict.setdefault("candidates", {}).setdefault(
        idx,
        {
            "idx": idx,
            "total": total,
            "changes_description": "",
            "samples": [],
            "scores": None,
        },
    )
    return slot


def seed_candidate(
    round_dict: dict[str, Any],
    idx: int,
    total: int,
    changes_description: str,
    pp_override: dict[str, Any] | None,
) -> None:
    """Seed a slot so CURRENT shows labelled pending rows before scoring lands."""
    entry = candidate_slot(round_dict, idx, total)
    entry["total"] = total
    entry["changes_description"] = changes_description or ""
    entry["pp_override"] = pp_override


def append_sample(
    round_dict: dict[str, Any],
    ci: int,
    ct: int,
    qi: int,
    qt: int,
    result: dict[str, Any],
) -> None:
    """Append one scored sample to its candidate slot."""
    pd = result.get("pipeline_data") or {}
    query_time = float(pd.get("total_time", 0.0) or 0.0)
    # Tokens may live on result or pd; prefer result, preserve 0 vs None.
    in_tok = result.get("input_tokens")
    out_tok = result.get("output_tokens")
    candidate_slot(round_dict, ci, ct)["samples"].append(
        {
            "qi": qi,
            "qt": qt,
            "sample_id": result.get("sample_id"),
            "hit": bool(result.get("hit")),
            "cached": bool(result.get("cached", False)),
            "query": result.get("query") or "",
            "prediction": result.get("prediction") or "",
            "ground_truth": result.get("ground_truth") or "",
            "time_s": round(query_time, 2),
            "terminated_at": pd.get("terminated_at") or "",
            "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
            "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
        }
    )


def set_candidate_scores(
    round_dict: dict[str, Any],
    idx: int,
    total: int,
    scores: dict[str, Any],
) -> None:
    """Store the score report verbatim — single source of truth shared with
    ``round_result.candidate_scores`` (same dict instance)."""
    candidate_slot(round_dict, idx, total)["scores"] = scores


def update_p_best(
    round_dict: dict[str, Any],
    core: LiveStateCore,
    idx: int,
    total: int,
    current_id: str,
    n_samples: int,
    p_best: dict[str, float],
) -> None:
    """Merge per-sample P(best) into the candidate slot + top-5 leaderboard.

    Stores each candidate's ``p_best``, signed delta vs prior query, and a
    capped trajectory list. Mirrors the snapshot into the shared *core* and
    publishes the round-wide top-5 view at ``round_dict["p_best_top"]``.
    """
    cand = candidate_slot(round_dict, idx, total)
    current = float(p_best.get(current_id, 0.0))
    prev = float(cand.get("p_best", current))
    history: list[float] = list(cand.get("p_best_history") or [])
    history.append(current)
    # Cap history at 64 entries — round size rarely exceeds 40.
    if len(history) > 64:
        history = history[-64:]
    cand["p_best"] = current
    cand["p_best_delta"] = current - prev
    cand["p_best_history"] = history
    cand["p_best_n_samples"] = n_samples

    # Mirror the latest snapshot into the shared core so both renderers
    # see the same round-wide P(best) state.
    apply_p_best_update(core, current_id, n_samples, p_best)

    # Round-wide leaderboard (top-5 by P(best)).
    round_dict["p_best_top"] = [{"id": cid, "p_best": p} for cid, p in top_n_p_best(p_best)]


def append_backfill(
    state: dict[str, Any],
    round_num: int,
    idx: int,
    total: int,
    backfilled: dict[str, list[str]],
) -> None:
    """Append a paired-PoBB backfill event to ``state["backfill_log"]``.

    Webapp + notebook readers see this under ``dashboard.json::backfill_log``.
    Each entry names the round/candidate the backfill ran ahead of and the
    per-prior list of newly-measured sample IDs — so the operator can tell
    when "the leader got measured on the hard samples" vs "everything was
    already cached" (no entry = cached). Capped at 64 entries for size.
    """
    log: list[dict[str, Any]] = list(state.get("backfill_log") or [])
    log.append(
        {
            "round": int(round_num),
            "candidate_idx": int(idx),
            "candidate_total": int(total),
            "backfilled": backfilled,
            "n_priors": len(backfilled),
            "n_measurements": sum(len(v) for v in backfilled.values()),
        }
    )
    if len(log) > 64:
        log = log[-64:]
    state["backfill_log"] = log


__all__ = [
    "append_backfill",
    "append_sample",
    "candidate_slot",
    "seed_candidate",
    "set_candidate_scores",
    "update_p_best",
]
