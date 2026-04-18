"""Focused store modules for file-based persistence.

The :class:`Stores` composite and :func:`build_stores` builder are the
canonical entry point; leaf stores are re-exported for direct use in
tests and narrow wiring.
"""

from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.dataset_run_store import DatasetRunStore
from promptpotter.infrastructure.store.stores import (
    BackendStore,
    PlanStore,
    Stores,
    build_stores,
    clear_active_pointer,
    generate_session_id,
    read_active_pointer,
    save_active_pointer,
)

__all__ = [
    "BackendStore",
    "CampaignStore",
    "DatasetRunStore",
    "PlanStore",
    "Stores",
    "build_stores",
    "clear_active_pointer",
    "generate_session_id",
    "read_active_pointer",
    "save_active_pointer",
]
