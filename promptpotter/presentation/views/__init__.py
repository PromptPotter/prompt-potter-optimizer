"""Shared post-hoc renderers — consumed by CLI, notebook, and (future) webapp.

Pure functions only. No disk I/O, no logging, no global state. Callers load
the data (trial files, dashboard.json, in-memory ``campaign_rounds``) and
pass it in. Both the CLI ``show-*`` commands and the notebook ``show_*``
analytics functions import from here so there is a single canonical render
per concept.
"""

from __future__ import annotations

from .dashboard import render_dashboard, render_status
from .formatting import generate_supplemental, render_pipeline_overrides, render_table
from .rounds import (
    render_campaign_summary,
    render_flip_tracking,
    render_lineage,
    render_progress,
)

__all__ = [
    "generate_supplemental",
    "render_campaign_summary",
    "render_dashboard",
    "render_flip_tracking",
    "render_lineage",
    "render_pipeline_overrides",
    "render_progress",
    "render_status",
    "render_table",
]
