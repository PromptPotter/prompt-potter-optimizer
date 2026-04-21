"""Thin print wrappers for presentation.views renderers."""

from __future__ import annotations

from promptpotter.presentation.views import (
    render_campaign_summary,
    render_flip_tracking,
    render_lineage,
    render_progress,
)

__all__ = [
    "show_campaign_summary",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_progress",
]


def show_progress(campaign_rounds: list, window: int = 8) -> None:
    print(render_progress(campaign_rounds, window=window))


def show_campaign_summary(campaign_rounds: list) -> None:
    print(render_campaign_summary(campaign_rounds))


def show_flip_tracking(campaign_rounds: list) -> None:
    out = render_flip_tracking(campaign_rounds)
    print(out if out else "Need at least 2 rounds with results for flip tracking.")


def show_lineage_chain(campaign_rounds: list) -> None:
    print(render_lineage(campaign_rounds))
