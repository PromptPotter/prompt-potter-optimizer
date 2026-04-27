"""Shared post-hoc renderers — consumed by CLI, notebook, and (future) webapp.

Pure functions only. No disk I/O, no logging, no global state. Callers load
the data (trial files, dashboard.json, in-memory ``campaign_rounds``) and
pass it in. Both the CLI ``show-*`` commands and the notebook ``show_*``
analytics functions import from here so there is a single canonical render
per concept.

Only symbols actually imported via this package path are re-exported here.
Internal-only renderers are imported directly from their submodule.
"""

from __future__ import annotations

from .campaign_list import render_experiment_dashboard
from .dashboard import render_status
from .formatting import generate_supplemental
from .hard_sample_heatmap import render_hard_sample_heatmap
from .live_render import render_progress_table
from .log_md import render_log_md
from .preflight import render_preflight
from .rounds import render_campaign_summary, render_flip_tracking, render_lineage
from .scoring_set import collect_scoring_set_events, render_scoring_set

__all__ = [
    "collect_scoring_set_events",
    "generate_supplemental",
    "render_campaign_summary",
    "render_experiment_dashboard",
    "render_flip_tracking",
    "render_hard_sample_heatmap",
    "render_lineage",
    "render_log_md",
    "render_preflight",
    "render_progress_table",
    "render_scoring_set",
    "render_status",
]
