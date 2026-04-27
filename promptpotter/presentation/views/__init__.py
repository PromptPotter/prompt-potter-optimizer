"""Shared post-hoc renderers — consumed by CLI, notebook, and (future) webapp.

Pure functions only. No disk I/O, no logging, no global state. Callers load
the data (trial files, dashboard.json, in-memory ``campaign_rounds``) and
pass it in. Both the CLI ``show-*`` commands and the notebook ``show_*``
analytics functions import from here so there is a single canonical render
per concept.
"""

from __future__ import annotations

from .campaign_list import (
    render_campaign_detail,
    render_campaign_overview,
    render_config_diff,
    render_experiment_dashboard,
    render_resume_hint,
)
from .completion import render_completion
from .dashboard import render_dashboard, render_status
from .formatting import (
    generate_supplemental,
    render_markdown_box,
    render_pipeline_overrides,
    render_table,
)
from .hard_sample_heatmap import render_hard_sample_heatmap
from .live_render import render_patience_status, render_progress_table, render_round_stats
from .log_md import render_log_md
from .phase_events import render_phase_event, render_sp_diff
from .preflight import render_preflight
from .rounds import (
    render_campaign_summary,
    render_flip_tracking,
    render_lineage,
)
from .scoring_set import collect_scoring_set_events, render_scoring_set

__all__ = [
    "collect_scoring_set_events",
    "generate_supplemental",
    "render_campaign_detail",
    "render_campaign_overview",
    "render_campaign_summary",
    "render_completion",
    "render_config_diff",
    "render_dashboard",
    "render_experiment_dashboard",
    "render_flip_tracking",
    "render_hard_sample_heatmap",
    "render_lineage",
    "render_log_md",
    "render_markdown_box",
    "render_patience_status",
    "render_phase_event",
    "render_pipeline_overrides",
    "render_preflight",
    "render_progress_table",
    "render_resume_hint",
    "render_round_stats",
    "render_scoring_set",
    "render_sp_diff",
    "render_status",
    "render_table",
]
