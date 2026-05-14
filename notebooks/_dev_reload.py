"""IPython kernel-reload helper — call from a notebook to pick up code edits."""

from __future__ import annotations

import importlib
import sys

_RELOAD_MODULES = (
    # Service layer — safe to reload (no Pydantic model classes)
    "promptpotter.shared.hashing",
    "promptpotter.application.config",
    "promptpotter.application.origin",
    "promptpotter.application.runner",
    "promptpotter.application.bootstrap",
    "promptpotter.infrastructure.tracing",
    "promptpotter.application.optimization.cycle",
    "promptpotter.application.optimization.pobb.elimination",
    "promptpotter.application.optimization.pipeline",
    "promptpotter.application.optimization.l1",
    "promptpotter.application.scoring.formula",
    "promptpotter.application.scoring.sample_measurement",
    # Display layer — safe to reload (no model classes)
    "promptpotter.presentation.views.display",
    "promptpotter.presentation.views.phase_events",
    "promptpotter.presentation.views.phase_views",
    "promptpotter.presentation.views.log_md",
    "promptpotter.presentation.views.round_render",
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
