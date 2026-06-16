"""Typed View dataclasses + the PhaseEvent→View builder + markdown rendering.

This is the **application's emit contract**: ``run_observers`` builds typed
views from ``PhaseEvent``s (``from_phase_event`` needs ``optimizer_model`` +
the scoring formula evaluators, both same-layer), Pydantic serializes them
byte-identically to disk + SSE, and the disk-side writers render them to
``log.md`` / ``summary.md`` markdown. Because producing these views *is* an
orchestration job, they live in ``application/`` — ``presentation/`` imports
them UPWARD (the allowed direction).

Genuinely-terminal rendering (ANSI ``to_text`` / ``render_sp_diff`` / the
``live/`` ledger subscriber) stays in ``presentation/views`` — that's display.
"""

from __future__ import annotations

from promptpotter.application.views.ingress import (
    from_phase_event,
    pick_round_winner,
    score_entry_from_dict,
)
from promptpotter.application.views.render import render_sweep_summary, to_markdown
from promptpotter.application.views.view_models import (
    AnyView,
    CandidatesGeneratedView,
    DigestStatusView,
    EscalationEnterView,
    EscalationExitView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    LogMdView,
    PlanEnterView,
    PlanExitView,
    ProbeEnterView,
    ProbeExitView,
    RoundCompleteView,
    RoundDigestView,
    RoundStartView,
    ScoreEntry,
    SpDiffView,
    SweepPayloadRow,
    SweepSummaryView,
    ViewContext,
    WarningEntry,
)

__all__ = [
    "AnyView",
    "CandidatesGeneratedView",
    "DigestStatusView",
    "EscalationEnterView",
    "EscalationExitView",
    "FinalWinnerView",
    "ForkSummaryView",
    "HardSamplesView",
    "InitEnterView",
    "InitExitView",
    "L2RefineEnterView",
    "L2RefineExitView",
    "LogMdView",
    "PlanEnterView",
    "PlanExitView",
    "ProbeEnterView",
    "ProbeExitView",
    "RoundCompleteView",
    "RoundDigestView",
    "RoundStartView",
    "ScoreEntry",
    "SpDiffView",
    "SweepPayloadRow",
    "SweepSummaryView",
    "ViewContext",
    "WarningEntry",
    "from_phase_event",
    "pick_round_winner",
    "render_sweep_summary",
    "score_entry_from_dict",
    "to_markdown",
]
