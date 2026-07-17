"""Potter-trace dataset loader.

Reads each cycle's ``rounds/round_NNNN.json`` and emits one row
per ``(round_N → round_N+1)`` transition: the potter-state context at
round N, the prompt change the potter actually made, and the accuracy
delta that resulted. Rows conform to the dataset-row contract so they
flow through ``load_dataset`` / ``build_dataset_run_data`` unchanged.
Raw material for self-optimization.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import RoundResult

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores


def _infer_escalation_layer(prev: OptSearchPoint, nxt: OptSearchPoint) -> str:
    """Classify the transition by which piece of optimizer state changed.

    Deterministic, inside-this-file rule (no import from optimization/):
      - ``plan`` changed                       → L3
      - ``l1_overrides`` or
        ``task_context`` changed               → L2
      - otherwise                              → L1

    Reads off the rehydrated OSP because ``task_context`` / ``l1_overrides``
    live under ``memory:`` in the serialized shape, not at the top level.
    """
    if prev.plan != nxt.plan:
        return "L3"
    if prev.memory.l1_overrides != nxt.memory.l1_overrides:
        return "L2"
    if prev.memory.task_context != nxt.memory.task_context:
        return "L2"
    return "L1"


def _rehydrate(fields: dict[str, Any]) -> OptSearchPoint:
    return OptSearchPoint.model_validate(fields)


def _build_row(
    cycle_id: str,
    prev_round: RoundResult,
    next_round: RoundResult,
) -> dict[str, Any]:
    prev_fields = prev_round.prompt_fields
    prev_osp = _rehydrate(prev_fields)
    next_osp = _rehydrate(next_round.prompt_fields)

    prev_accuracy = prev_round.accuracy
    score_delta = next_round.accuracy - prev_accuracy

    round_context = {
        "opt_search_point": prev_fields,
        "critique": prev_round.critique or {},
        "task_context": prev_osp.memory.task_context.to_dict(),
        "l1_overrides": dict(prev_osp.memory.l1_overrides),
        "prev_accuracy": prev_accuracy,
    }

    return {
        "query": f"{cycle_id}:round_{prev_round.round}",
        "ground_truth": next_osp.lineage.changes_description or next_round.label,
        "round_context": round_context,
        "score_delta": score_delta,
        "prev_prompt": prev_osp.render(),
        "next_prompt": next_osp.render(),
        "escalation_layer": _infer_escalation_layer(prev_osp, next_osp),
    }


def load_potter_traces(
    store: Stores,
    backend_id: str,
    cycle_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Emit one row per round-to-round transition across archived campaigns.

    Args:
        store: Stores facade (reads ``campaigns/{cycle_id}/`` only).
        backend_id: Which backend's campaigns to scan.
        cycle_ids: Restrict to specific cycle IDs, or None for every campaign
            under the backend.

    Returns:
        List of rows conforming to the dataset-row contract.  Empty list if
        no transitions are recoverable.
    """
    rows: list[dict[str, Any]] = []
    for entry in store.campaigns.enumerate_cycles():
        cycle_id = entry["cycle_id"]
        if cycle_ids is not None and cycle_id not in cycle_ids:
            continue
        if backend_id and entry.get("backend_id") and entry["backend_id"] != backend_id:
            continue
        campaign_id = entry["campaign_id"]
        index = store.campaigns.load(campaign_id, cycle_id)
        if index is None:
            continue
        round_summaries = sorted(
            index.get("rounds", []),
            key=lambda t: int(t.get("round", 0)),
        )
        if len(round_summaries) < 2:
            continue

        rounds: list[RoundResult] = []
        for summary in round_summaries:
            detail = store.campaigns.load_round_file(
                campaign_id,
                cycle_id,
                int(summary.get("round", 0)),
            )
            if detail is not None:
                rounds.append(detail)

        for prev_round, next_round in pairwise(rounds):
            rows.append(_build_row(cycle_id, prev_round, next_round))

    return rows
