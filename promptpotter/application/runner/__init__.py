"""Optimization loop orchestrator — L1 generate → score → escalate.

The original ``runner.py`` (~736 lines) is split into:

* :mod:`identity` — pure cycle-id helpers (``cycle_config_identity``,
  ``build_origin_cycle_id``).
* :mod:`round` — round-boundary helpers (``persist_round``,
  ``close_round``, ``post_round``, ``escalate_or_stop``,
  ``count_positive_yield_axes``).
* :mod:`sweep` — generation-only round used by sweep + diag modes.
* :mod:`loop` — the round loop itself (``run_round_loop``).
* :mod:`entry` — the entry point + final teardown (``run_optimization``,
  ``_finalize_run``).

The public surface is re-exported here; every existing import keeps
working (``cycle_config_identity`` is imported by
``application/bootstrap/scoring_context.py``,
``application/run_observers.py``, several CLI command modules, and the
notebook view layer).
"""

from __future__ import annotations

from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.identity import (
    build_origin_cycle_id,
    cycle_config_identity,
)

__all__ = [
    "build_origin_cycle_id",
    "cycle_config_identity",
    "run_optimization",
]
