"""Synthesize sensitivity profiles from grid search data."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from api.services.project_store import ProjectStore
from api.models.hashing import HASH_TRUNCATE
from api.services.prompt_eval import compute_composite_score
from api.services.search.plan_persistence import deserialize_grid_plan
from api.services.search.smart_search import classify_axis
from api.services.search.utils import preview as _preview

logger = logging.getLogger(__name__)


def synthesize_sensitivity_from_grid(
    store: ProjectStore,
    backend_id: str,
    prompt_result_index: dict[str, dict[str, dict]],
    diagnostic_queries: list,
) -> tuple[pd.DataFrame, list[dict]] | None:
    """Derive OAT sensitivity profiles from grid search data.

    Loads all grid plans, resolves each grid point's rendered_prompt_hash,
    checks the ``prompt_result_index`` for coverage on ``diagnostic_queries``,
    and computes per-axis marginal accuracy.

    Returns ``(per_variant_df, axis_profiles)`` in the same format as
    ``sensitivity_scan()``, or ``None`` if insufficient coverage.
    """
    import pandas as pd

    plan_summaries = store.grid_plans.list_all(backend_id)
    if not plan_summaries:
        logger.info("synthesize_sensitivity: no grid plans found")
        return None

    diag_query_strings = {qd.get("query", "") for qd in diagnostic_queries}
    if not diag_query_strings:
        return None

    # Collect (axis_name -> value -> list[accuracy]) across all grid points
    axis_value_accs: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list),
    )
    grid_axes_combined: dict[str, list] = {}
    covered_points = 0
    total_points = 0

    for summary in plan_summaries:
        plan_id = summary["plan_id"]
        plan_data = store.grid_plans.load(backend_id, plan_id)
        if not plan_data:
            continue
        grid_points, state_lookup, _, grid_axes, _, _ = deserialize_grid_plan(
            plan_data,
        )
        # Merge axes for profile building
        for ax_name, ax_values in grid_axes.items():
            if ax_name not in grid_axes_combined:
                grid_axes_combined[ax_name] = ax_values

        axis_names = list(grid_axes.keys())

        for coord_dict, ps_id in grid_points:
            total_points += 1
            ps = state_lookup.get(ps_id)
            if not ps:
                continue
            rendered = ps.render()
            rp_hash = hashlib.sha256(
                rendered.encode(),
            ).hexdigest()[:HASH_TRUNCATE]

            cached_by_query = prompt_result_index.get(rp_hash, {})
            matched = [
                cached_by_query[q] for q in diag_query_strings
                if q in cached_by_query
            ]
            coverage = len(matched) / len(diag_query_strings)
            if coverage < 0.5:
                continue

            covered_points += 1
            acc = compute_composite_score(matched)["accuracy"]

            # Attribute this accuracy to each axis value at this grid point
            for ax_name in axis_names:
                ax_idx = coord_dict.get(ax_name, 0)
                ax_values = grid_axes.get(ax_name, [])
                if ax_idx < len(ax_values):
                    value_key = str(ax_values[ax_idx])
                    axis_value_accs[ax_name][value_key].append(acc)

    if covered_points == 0:
        logger.info(
            "synthesize_sensitivity: 0/%d grid points have "
            "sufficient diagnostic coverage", total_points,
        )
        return None

    logger.info(
        "synthesize_sensitivity: %d/%d grid points covered, "
        "%d axes available",
        covered_points, total_points, len(axis_value_accs),
    )

    # Compute marginal accuracy per axis value and build profiles
    overall_mean = 0.0
    n_total = 0
    for ax_values in axis_value_accs.values():
        for accs in ax_values.values():
            overall_mean += sum(accs)
            n_total += len(accs)
    overall_mean = overall_mean / n_total if n_total else 0.0

    rows: list[dict] = []
    profiles: list[dict] = []

    for ax_name, value_accs in axis_value_accs.items():
        deltas: list[float] = []
        for value_key, accs in value_accs.items():
            mean_acc = sum(accs) / len(accs) if accs else 0.0
            delta = mean_acc - overall_mean
            deltas.append(delta)
            rows.append({
                "axis": ax_name,
                "axis_type": "prompt_field",
                "value_idx": 0,
                "value_preview": _preview(value_key),
                "hits": 0,
                "total": len(diagnostic_queries),
                "accuracy": mean_acc,
                "delta": delta,
            })

        sens_range = max(deltas) - min(deltas) if deltas else 0.0
        card = len(value_accs)
        budget = classify_axis(card, sens_range, len(diagnostic_queries))
        profiles.append({
            "axis": ax_name,
            "axis_type": "prompt_field",
            "cardinality": card,
            "sensitivity_range": round(sens_range, 4),
            "best_delta": round(max(deltas), 4) if deltas else 0.0,
            "worst_delta": round(min(deltas), 4) if deltas else 0.0,
            "exploration_budget": budget,
            "estimated_eval_cost": card * len(diagnostic_queries),
            "source": "grid_synthesis",
        })

    profiles.sort(key=lambda p: -p["sensitivity_range"])
    return pd.DataFrame(rows), profiles
