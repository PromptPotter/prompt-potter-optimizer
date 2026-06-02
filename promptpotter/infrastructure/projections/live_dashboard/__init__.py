"""LiveDashboardView — operator-facing ``dashboard.json`` writer.

Per-cycle: each cycle (root, fork, sweep, diag) owns its own
``cycles/{cycle_id}/dashboard.json`` (`view.py::for_session`); a fork seeds its
prior trajectory from the parent's file but writes its own thereafter — the root
does not aggregate forks. The package keeps the writer thin: the class owns its own
round-state mutations and block builders inside :mod:`view`; the two
remaining submodules carve off concerns that don't belong inside the
ledger-subscriber loop:

* :mod:`view` — :class:`LiveDashboardView` (the ledger subscriber, the
  per-round candidate dict, the in-flight + flush block builders, the
  persist loop).
* :mod:`factory` — :func:`resolve_resume_state`, the disk-reconciliation
  helper behind ``LiveDashboardView.for_session``.
* :mod:`round_summary` — :func:`build_round_summary` for
  ``dash.rounds[]`` (the read-side shape consumed by the webapp's
  FitnessChart / TrendChart).
"""

from __future__ import annotations

from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView

__all__ = ["LiveDashboardView"]
