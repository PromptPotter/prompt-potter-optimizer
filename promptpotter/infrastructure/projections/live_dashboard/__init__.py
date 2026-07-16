"""LiveDashboardView — operator-facing ``dashboard.json`` writer.

Per-cycle: each cycle (root, fork, sweep, diag) owns its own
``cycles/{cycle_id}/dashboard.json`` (`view.py::for_session`); a fork seeds its
prior trajectory from the parent's file but writes its own thereafter — the root
does not aggregate forks. The package keeps the writer thin: the class owns its own
round-state mutations and block builders inside :mod:`view`; the sibling
submodules carve off concerns that don't belong inside the
ledger-subscriber loop:

* :mod:`view` — :class:`LiveDashboardView` (the ledger subscriber, the
  per-round candidate dict, the in-flight + flush block builders, the
  persist loop).
* :mod:`factory` — :func:`resolve_resume_state`, the disk-reconciliation
  helper behind ``LiveDashboardView.for_session``.
* :mod:`round_summary` — :func:`build_round_summary` for
  ``dash.rounds[]`` (the read-side shape consumed by the webapp's
  FitnessChart / TrendChart).
* :mod:`round_buffer` — :class:`RoundBuffer`, the per-round candidate
  buffer behind ``current_round.nodes.l1_score``.
* :mod:`render` — pure, side-effect-free projections from scalar state +
  :class:`RoundBuffer` to the ``dashboard.json`` output shape.
* :mod:`state` — the Pydantic schema (single source of truth) every
  ``_persist()`` validates the payload through.
"""
