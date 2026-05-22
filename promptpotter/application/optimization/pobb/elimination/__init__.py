"""Candidate elimination — result classification + mid-round stop rules.

* :mod:`classification` — :func:`classify_result` three-bucket
  (advisory / infra / fatal) verdict + the result-shape helpers
  (:func:`is_deprecated`, :func:`get_ranked_items`,
  :func:`extract_warning_types`, :func:`ranked_item_keys_from_schema`).
* :mod:`checks` — the mid-round stop rules: :class:`DegradationCheck`
  (fatal fast-path + rate-based) and :class:`PoBBCheck` (Bayesian
  Posterior-of-Being-Best, Russo 2016) + :class:`PoBBConfig`. §0.5
  load-bearing — the actual abort-and-continue mechanism.
"""

from __future__ import annotations

from promptpotter.application.optimization.pobb.elimination.checks import (
    DegradationCheck,
    PoBBCheck,
    PoBBConfig,
    PoBBSnapshot,
    build_degradation_checks,
)
from promptpotter.application.optimization.pobb.elimination.classification import (
    ResultClassification,
    classify_result,
    extract_warning_types,
    get_ranked_items,
    is_deprecated,
    ranked_item_keys_from_schema,
)

__all__ = [
    "DegradationCheck",
    "PoBBCheck",
    "PoBBConfig",
    "PoBBSnapshot",
    "ResultClassification",
    "build_degradation_checks",
    "classify_result",
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
]
