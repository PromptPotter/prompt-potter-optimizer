"""LiveDashboardView — operator-facing ``dashboard.json`` writer.

Family-root-bound: one ``dashboard.json`` per cycle family, shared across
all forks. The package splits the original 808-line module into the
routing class plus three pure builders:

* :mod:`view` — :class:`LiveDashboardView` (the ledger subscriber + state
  scalars + persist loop). The phase-to-state map + the disk-side
  ``rounds/`` scan live next to the class that uses them.
* :mod:`score` — :func:`build_l1_score_block` for
  ``current_round.nodes.l1_score``.
* :mod:`pobb` — :func:`build_pobb_block` for ``current_round.pobb``.
* :mod:`sample` — :func:`fmt_sample_line` (compact one-liner used by
  the score builder when live).
"""

from __future__ import annotations

from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView

__all__ = ["LiveDashboardView"]
