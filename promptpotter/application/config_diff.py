"""Resume-time config diff classifier.

Split out of :mod:`promptpotter.application.config` (a schema + pipeline-setup
module) because this is a self-contained engine: a scope table + a diff walk +
an import-time completeness guard, used only on resume to decide whether a
config edit forks the data trace or just changes policy. One-way import —
``config_diff`` depends on ``config`` (for ``CampaignConfig``); never the reverse.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

from promptpotter.application.config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = ["DiffScope", "classify_config_diff"]


class DiffScope(StrEnum):
    """Resume-time diff classification.

    - ``NONE``: identical configs.
    - ``POLICY_ONLY``: decision knobs differ (PoBB ε/n_min, patience, thresholds, n_variants).
      Past measurements + candidates still valid; new policy governs unevaluated rounds.
    - ``DATA_AFFECTING``: a field that shapes the data trace differs (JobSearchPoint inputs,
      scoring, optimizer LLM). Cached measurements may not apply — resume runs divergence detection.
    """

    NONE = "none"
    POLICY_ONLY = "policy_only"
    DATA_AFFECTING = "data_affecting"


# Subtree entries stop the diff walk at that depth. Unknown paths fall back to DATA_AFFECTING (safe).
_FIELD_SCOPES: dict[tuple[str, ...], Literal["policy", "data"]] = {
    # Top-level
    ("dataset_name",): "data",
    ("sp_budget_ttest",): "policy",
    ("exclude_nodes",): "data",
    ("pipeline_overrides",): "data",
    ("optimizer_narrowing",): "data",
    ("scoring",): "data",
    ("headline_metric",): "policy",  # display-only — picks which number is shown, never the data
    ("dataset_split",): "policy",  # display-only metadata — no data fork
    # OptimizationConfig
    ("optimization", "max_rounds"): "policy",
    ("optimization", "lives"): "policy",  # subtree — round-budget policy, twin of max_rounds
    ("optimization", "l1_patience"): "policy",
    ("optimization", "n_variants"): "policy",
    ("optimization", "optimizer_set"): "policy",  # which meta-prompt set the optimizer runs (L4)
    (
        "optimization",
        "replicate_survivors",
    ): "policy",  # opt-in replication depth — search/spend, not data
    ("optimization", "improvement_threshold"): "policy",
    ("optimization", "l2_patience"): "policy",
    ("optimization", "l3_patience"): "policy",
    ("optimization", "degradation_threshold"): "policy",
    ("optimization", "elimination_n_min"): "policy",
    ("optimization", "pobb_epsilon"): "policy",
    ("optimization", "pobb_lock_in"): "policy",
    ("optimization", "pobb_lock_in_n_min"): "policy",
    ("optimization", "spend_budget_usd"): "policy",
    ("optimization", "token_budget"): "policy",
    ("optimization", "origin_gate"): "policy",
    ("optimization", "forbidden_axes_strict"): "policy",
    ("optimization", "rebase_capability"): "policy",
    ("optimization", "terminate_capability"): "policy",
    ("optimization", "exploration"): "policy",  # entire subtree
    ("optimization", "mechanisms"): "policy",  # entire subtree — every toggle, now and future
}


def _diff_paths(
    active: Any,
    frozen: Any,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    """Diff paths between *active* and *frozen*; stops at any `_FIELD_SCOPES` entry (subtree-as-unit)."""
    if prefix in _FIELD_SCOPES:
        return [prefix] if active != frozen else []
    if isinstance(active, dict) or isinstance(frozen, dict):
        a = active if isinstance(active, dict) else {}
        f = frozen if isinstance(frozen, dict) else {}
        out: list[tuple[str, ...]] = []
        for key in set(a.keys()) | set(f.keys()):
            out.extend(_diff_paths(a.get(key), f.get(key), (*prefix, key)))
        return out
    return [prefix] if active != frozen else []


def classify_config_diff(
    config: CampaignConfig, frozen: dict[str, Any]
) -> tuple[DiffScope, list[str]]:
    """Classify *config* vs *frozen*; returns `(scope, dotted_paths)`. Unknown paths warn + classify DATA."""
    active = config.model_dump(mode="json")
    diffs = _diff_paths(active, frozen)
    if not diffs:
        return DiffScope.NONE, []
    has_data = False
    diff_strs: list[str] = []
    for path in diffs:
        scope = _FIELD_SCOPES.get(path)
        if scope is None:
            logger.warning(
                "classify_config_diff: unclassified config path %r — "
                "treating as DATA_AFFECTING. Add an entry to _FIELD_SCOPES "
                "in promptpotter/application/config_diff.py to silence this.",
                ".".join(path),
            )
            has_data = True
        elif scope == "data":
            has_data = True
        diff_strs.append(".".join(path))
    if has_data:
        return DiffScope.DATA_AFFECTING, diff_strs
    return DiffScope.POLICY_ONLY, diff_strs


def _config_leaves(
    model_cls: type[BaseModel], prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Leaf field paths of *model_cls*; a path in ``_FIELD_SCOPES`` is a leaf (subtree-as-unit)."""
    out: list[tuple[str, ...]] = []
    for name, fld in model_cls.model_fields.items():
        path = (*prefix, name)
        ann = fld.annotation
        if path in _FIELD_SCOPES:
            out.append(path)
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            out.extend(_config_leaves(ann, path))
        else:
            out.append(path)
    return out


# Fail import if a new CampaignConfig knob has no scope: the unclassified
# fallback silently treats it as DATA_AFFECTING, breaking the operator's
# "don't fork for policy changes" contract. Every leaf must be in _FIELD_SCOPES.
_unclassified = [".".join(p) for p in _config_leaves(CampaignConfig) if p not in _FIELD_SCOPES]
if _unclassified:
    raise RuntimeError(
        f"CampaignConfig leaves missing from _FIELD_SCOPES: {_unclassified}. "
        "Classify each as 'policy' or 'data' in promptpotter/application/config_diff.py."
    )
del _unclassified
