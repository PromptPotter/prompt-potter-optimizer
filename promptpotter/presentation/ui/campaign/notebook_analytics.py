"""Thin ``show_*`` analytics wrappers — called directly from notebook cells.

Each function delegates to the shared renderer in ``presentation.views`` so
the CLI ``show-results`` command and the notebook emit the same output.
"""

from __future__ import annotations

__all__ = [
    "show_campaign_summary",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_progress",
]


def show_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round.

    Thin wrapper over ``presentation.views.render_progress`` — the same
    renderer the CLI ``show-results`` uses.
    """
    from promptpotter.presentation.views import render_progress

    print(render_progress(campaign_rounds, window=window))


def show_campaign_summary(campaign_rounds: list) -> None:
    """Print campaign comparison table.

    Thin wrapper over ``presentation.views.render_campaign_summary``.
    """
    from promptpotter.presentation.views import render_campaign_summary

    print(render_campaign_summary(campaign_rounds))


def show_flip_tracking(campaign_rounds: list) -> None:
    """Compare first vs last round, show per-query flips.

    Thin wrapper over ``presentation.views.render_flip_tracking``.
    """
    from promptpotter.presentation.views import render_flip_tracking

    out = render_flip_tracking(campaign_rounds)
    print(out if out else "Need at least 2 rounds with results for flip tracking.")


def show_lineage_chain(campaign_rounds: list) -> None:
    """Display OptSearchPoint lineage chain across rounds.

    Thin wrapper over ``presentation.views.render_lineage``.
    """
    from promptpotter.presentation.views import render_lineage

    print(render_lineage(campaign_rounds))
