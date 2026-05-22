"""LiveDashboardView — operator-facing ``dashboard.json`` writer.

Family-root-bound: one ``dashboard.json`` per cycle family, shared across
all forks. The package splits the writer into the routing class plus the
mutation/builder modules it fans out to:

* :mod:`view` — :class:`LiveDashboardView` (the ledger subscriber + state
  scalars + persist loop).
* :mod:`candidate_block` — free functions for the ``SnapshotRecord``
  mutations: the per-round candidate dict + the backfill log.
* :mod:`factory` — :func:`resolve_resume_state`, the disk-reconciliation
  helper behind ``LiveDashboardView.for_session``.
* :mod:`score` — :func:`build_l1_score_block` for
  ``current_round.nodes.l1_score``.
* :mod:`pobb` — :func:`build_pobb_block` for ``current_round.pobb``.
* :mod:`sample` — :func:`fmt_sample_line` (compact one-liner used by
  the score builder when live).
"""

from __future__ import annotations

from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView

__all__ = ["LiveDashboardView"]
