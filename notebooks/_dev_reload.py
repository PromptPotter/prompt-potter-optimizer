"""IPython kernel-reload helper — call from a notebook to pick up code edits."""

from __future__ import annotations

import importlib
import sys

_RELOAD_MODULES = (
    # Service layer — safe to reload (no Pydantic model classes)
    "promptpotter.shared.hashing",
    "promptpotter.shared.sp_diff_model",
    "promptpotter.application.campaign.config",
    "promptpotter.application.campaign.data",
    "promptpotter.application.campaign.runner",
    "promptpotter.application.campaign.campaign_setup",
    "promptpotter.application.campaign.phase_views",
    "promptpotter.application.optimization.elimination",
    "promptpotter.application.optimization.layer_escalation",
    "promptpotter.application.optimization.nodes.layer_transitions",
    "promptpotter.application.optimization.nodes.dispatch_msg_registry",
    "promptpotter.application.optimization.nodes.l1_execute",
    "promptpotter.application.optimization.nodes.l1_generate",
    "promptpotter.application.optimization.nodes.l1_measure",
    "promptpotter.application.optimization.nodes.l1_score",
    "promptpotter.application.optimization.nodes.formatting",
    "promptpotter.application.scoring.stale_data",
    "promptpotter.application.scoring.sample_measurement",
    # Display layer — safe to reload (no model classes)
    "promptpotter.presentation.views.display_primitives",
    "promptpotter.presentation.views.phase_events",
    "promptpotter.presentation.views.live",
    "promptpotter.presentation.views.notebook_run",
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
