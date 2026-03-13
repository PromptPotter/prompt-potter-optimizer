"""Scan winner selection — compose best per-axis values from OAT scan results."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from api.models.search_point import SearchPoint, _PROMPT_STATE_FIELDS

logger = logging.getLogger(__name__)


def select_scan_winner(
    scan_df: pd.DataFrame,
    axis_profiles: list[dict],
    baseline: SearchPoint,
    scan_variants: dict[str, list],
) -> SearchPoint:
    """Pick best variant per sensitive axis from OAT scan results.

    Composes the best-performing value for each axis that showed positive
    improvement (best_delta > 0) into a single SearchPoint. No backend
    calls required — purely offline composition from existing scan data.

    Returns:
        SearchPoint with best values composed in.
    """
    prompt_changes: dict[str, Any] = {}
    param_changes: dict[str, Any] = {}

    improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]

    for profile in improving:
        axis_name = profile["axis"]

        axis_rows = scan_df[scan_df["axis"] == axis_name]
        if axis_rows.empty:
            continue

        best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
        value_idx = int(best_row["value_idx"])

        values = scan_variants.get(axis_name, [])
        if value_idx >= len(values):
            logger.warning(
                "select_scan_winner: value_idx %d out of range for %s (len=%d)",
                value_idx, axis_name, len(values),
            )
            continue

        value = values[value_idx]

        if axis_name in _PROMPT_STATE_FIELDS:
            prompt_changes[axis_name] = value
        else:
            param_changes[axis_name] = value

    best = baseline
    if prompt_changes:
        best = best.derive(
            **prompt_changes,
            changes_description="scan_winner",
        )
    if param_changes:
        best = best.derive(
            pipeline_params={**(best.pipeline_params or {}), **param_changes},
        )

    logger.info(
        "select_scan_winner: %d prompt changes, %d param changes from %d improving axes",
        len(prompt_changes), len(param_changes), len(improving),
    )

    return best
