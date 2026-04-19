"""Potter trace dataset loader.

Reads archived ``campaigns/{cycle_id}/trials/trial_NNNN.json`` files and emits one
row per ``(trial_N → trial_N+1)`` transition.  Each row is the raw material
for self-optimization: the potter-state context at round N, the prompt change
the potter actually made, and the accuracy delta that resulted.

Rows conform to the dataset-row contract
(``{"query", "ground_truth", ...}``) so they flow through the existing
``load_dataset()`` / ``build_dataset_run_data()`` pipeline unchanged.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores


def _infer_escalation_layer(prev_fields: dict, next_fields: dict) -> str:
    """Classify the transition by which piece of optimizer state changed.

    Deterministic, inside-this-file rule (no import from optimization/):
      - ``plan`` changed              → L3
      - ``optimizer_params`` changed
        or ``l2_directive`` set fresh → L2
      - otherwise                     → L1
    """
    if prev_fields.get("plan", "") != next_fields.get("plan", ""):
        return "L3"
    if prev_fields.get("optimizer_params", {}) != next_fields.get("optimizer_params", {}):
        return "L2"
    prev_directive = prev_fields.get("l2_directive", "")
    next_directive = next_fields.get("l2_directive", "")
    if next_directive and next_directive != prev_directive:
        return "L2"
    return "L1"


def _rehydrate(fields: dict) -> OptSearchPoint:
    return OptSearchPoint.model_validate(fields)


def _build_row(
    cycle_id: str,
    prev_trial: dict[str, Any],
    next_trial: dict[str, Any],
) -> dict[str, Any] | None:
    prev_fields = prev_trial.get("prompt_fields") or {}
    next_fields = next_trial.get("prompt_fields") or {}
    if not prev_fields or not next_fields:
        return None

    prev_osp = _rehydrate(prev_fields)
    next_osp = _rehydrate(next_fields)

    prev_round = int(prev_trial.get("round", 0))
    score_delta = float(next_trial.get("accuracy", 0.0)) - float(prev_trial.get("accuracy", 0.0))

    round_context = {
        "opt_search_point": prev_fields,
        "critique_text": prev_fields.get("critique_text", ""),
        "l2_directive": prev_fields.get("l2_directive", ""),
        "optimizer_params": prev_fields.get("optimizer_params", {}),
        "prev_accuracy": float(prev_trial.get("accuracy", 0.0)),
    }

    return {
        "query": f"{cycle_id}:round_{prev_round}",
        "ground_truth": next_fields.get("changes_description") or next_trial.get("label", ""),
        "round_context": round_context,
        "score_delta": score_delta,
        "prev_prompt": prev_osp.render(),
        "next_prompt": next_osp.render(),
        "escalation_layer": _infer_escalation_layer(prev_fields, next_fields),
    }


def load_potter_traces(
    store: Stores,
    backend_id: str,
    cycle_ids: list[str] | None = None,
) -> list[dict]:
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
    campaigns = store.campaigns.load_many(backend_id, cycle_ids)
    rows: list[dict] = []
    for campaign in campaigns:
        cycle_id = campaign["campaign_id"]
        trial_summaries = sorted(
            campaign.get("trials", []),
            key=lambda t: int(t.get("round", 0)),
        )
        if len(trial_summaries) < 2:
            continue

        trials: list[dict[str, Any]] = []
        for summary in trial_summaries:
            detail = store.campaigns.load_trial(
                backend_id,
                cycle_id,
                int(summary.get("round", 0)),
            )
            if detail is not None:
                trials.append(detail)

        for prev_trial, next_trial in pairwise(trials):
            row = _build_row(cycle_id, prev_trial, next_trial)
            if row is not None:
                rows.append(row)

    return rows
