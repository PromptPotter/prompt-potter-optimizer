"""Live ledger subscriber — CLI and notebook share one ``LiveDisplay``.

Single ingress: the display consumes ``CycleRecord``s from the per-cycle
``CycleEventLog`` via ``on_record``. The package splits across four
concerns:

* :mod:`display` — :class:`LiveDisplay` (the ledger subscriber).
* :mod:`sample` — per-sample HIT/MISS line (:func:`fmt_query_result`).
* :mod:`candidate` — per-candidate header + summary classification
  (:func:`fmt_individual_header`, :class:`IndividualSummary`).
* :mod:`phase` — round-summary renderers fired on ``L1_SCORE:exit``.

Post-hoc reads happen by opening ``campaigns/<cycle_id>/log.md``.
"""

from __future__ import annotations

from promptpotter.presentation.views.live.candidate import (
    IndividualSummary,
    fmt_individual_header,
    fmt_pp_override,
    individual_summary_from_dict,
)
from promptpotter.presentation.views.live.display import LiveDisplay
from promptpotter.presentation.views.live.phase import (
    fmt_elapsed,
    render_patience_status,
    render_progress_table,
    render_round_stats,
)
from promptpotter.presentation.views.live.sample import fmt_query_result

__all__ = [
    "IndividualSummary",
    "LiveDisplay",
    "fmt_elapsed",
    "fmt_individual_header",
    "fmt_pp_override",
    "fmt_query_result",
    "individual_summary_from_dict",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
]
