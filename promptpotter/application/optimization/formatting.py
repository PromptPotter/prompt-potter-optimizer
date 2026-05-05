"""Pure string + dict builders that aren't owned by the dispatch hub.

Two surviving helpers after the V2 dispatch consolidation:

* :func:`candidate_summaries` — compact per-candidate phase event payload,
  used by the L1 generation phase emit.
* :func:`warning_summary` — `(warned_count, top_warning_type)` tuple used
  by L2's exit-payload telemetry.

Everything else that used to live here (rank tables, evolution rows,
trajectory classification, runtime-failure formatting, escalation
reports, axis digests, warning inventories) has moved to
:mod:`promptpotter.domain.round_diagnostics` (the typed payload) +
:mod:`promptpotter.application.optimization.dispatch_hub` (the
layer-agnostic renderers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.config.settings import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from promptpotter.domain.results import CandidateProposal

__all__ = [
    "candidate_summaries",
    "warning_summary",
]


def candidate_summaries(proposals: list[CandidateProposal]) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = {k: getattr(cp.osp, k) for k in PROMPT_STRING_FIELDS if getattr(cp.osp, k)}
        summary: dict = {
            "idx": i,
            "changes_description": cp.osp.lineage.changes_description or "",
        }
        if cp.pipeline_params_override:
            summary["pipeline_params_override"] = cp.pipeline_params_override
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        summaries.append(summary)
    return summaries


def warning_summary(tracker: dict[str, dict]) -> tuple[int, str]:
    """Return (warned_count, top_warning_type) from the warning inventory."""
    if not tracker:
        return 0, ""
    warned_count = sum(1 for e in tracker.values() if e.get("warnings"))
    all_wtypes: dict[str, int] = {}
    for e in tracker.values():
        for wt, c in e.get("warnings", {}).items():
            all_wtypes[wt] = all_wtypes.get(wt, 0) + c
    top_warning = max(all_wtypes, key=all_wtypes.get) if all_wtypes else ""  # type: ignore[arg-type]
    return warned_count, top_warning
