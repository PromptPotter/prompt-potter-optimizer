"""IPython kernel-reload helper — call from a notebook to pick up code edits."""

from __future__ import annotations

import importlib
import sys

_RELOAD_MODULES = (
    # Service layer — safe to reload (no Pydantic model classes)
    "promptpotter.shared.hashing",
    "promptpotter.application.campaign.config",
    "promptpotter.application.campaign.utils",
    "promptpotter.application.campaign.data",
    "promptpotter.application.campaign.runner",
    "promptpotter.application.campaign.campaign_setup",
    "promptpotter.application.optimization.elimination",
    "promptpotter.application.optimization.layer_escalation",
    "promptpotter.application.optimization.nodes.layer_transitions",
    "promptpotter.application.optimization.nodes.l1_critique",
    "promptpotter.application.optimization.nodes.round_execution",
    "promptpotter.application.optimization.nodes.generate",
    "promptpotter.application.optimization.nodes.score",
    "promptpotter.application.optimization.nodes.formatting",
    "promptpotter.application.scoring.stale_data",
    "promptpotter.application.scoring.sample_measurement",
    "promptpotter.infrastructure.store.dataset_run_store",
    "promptpotter.services.dataset_scoring",
    # Display layer — safe to reload (no model classes)
    "promptpotter.presentation.views.display_primitives",
    "promptpotter.presentation.views.phase_events",
    "promptpotter.presentation.views.sp_diff",
    "promptpotter.presentation.views.preflight",
    "promptpotter.presentation.views.completion",
    "promptpotter.presentation.views.campaign_list",
    "promptpotter.presentation.views.notebook_display",
    "promptpotter.presentation.views.notebook_run",
    "promptpotter.application.campaign.phase_views",
    "promptpotter.application.optimization.sp_diff_view",
    # NOTE: Do NOT reload promptpotter.domain.* or dataclass modules —
    # Pydantic/dataclass classes break when reloaded (existing
    # instances fail type checks). For model/dataclass changes,
    # restart the kernel.
)


def dev_reload() -> None:
    """Force-reload promptpotter modules so code edits take effect without kernel restart."""
    for mod in _RELOAD_MODULES:
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
