"""Compact per-candidate phase event builder.

Single helper after the V2 dispatch consolidation: :func:`candidate_summaries`
builds the payload used by the L1 generation phase emit. Everything else
that used to live here (rank tables, evolution rows, trajectory
classification, runtime-failure formatting, escalation reports, axis
digests, warning inventories) has moved to
:mod:`promptpotter.domain.round_diagnostics` (the typed payload) +
:mod:`promptpotter.application.optimization.dispatch_hub` (the
layer-agnostic renderers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.config.settings import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from promptpotter.domain.results import CandidateProposal

__all__ = ["candidate_summaries"]


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
