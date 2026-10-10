"""Never default the ``WorkspaceDir`` key: an inner cycle would retarget the OPERATOR's pointer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    validate_path_component,
    write_json,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)


def _active_pointer_path(workspace: WorkspaceDir) -> Path:
    return workspace / ".workspace" / "active_session.json"


def save_active_pointer(workspace: WorkspaceDir, hop: CycleHop) -> None:
    validate_path_component(hop.campaign_id)
    validate_path_component(hop.cycle_id)
    write_json(
        _active_pointer_path(workspace),
        {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id},
    )


def clear_active_pointer(workspace: WorkspaceDir) -> None:
    _active_pointer_path(workspace).unlink(missing_ok=True)


def read_active_pointer(workspace: WorkspaceDir) -> tuple[str, str]:
    ptr = read_json_tolerant(_active_pointer_path(workspace))
    if not isinstance(ptr, dict):
        return "", ""
    return ptr.get("campaign_id", ""), ptr.get("cycle_id", "")


def active_pointer_exists(workspace: WorkspaceDir) -> bool:
    return _active_pointer_path(workspace).exists()


def cleanup_stub_fork_if_empty(
    *,
    campaign_store: CampaignStore,
    hop: CycleHop,
    parent_cycle_id: str,
) -> tuple[bool, str]:
    workspace = campaign_store.workspace
    was_active = read_active_pointer(workspace) == (hop.campaign_id, hop.cycle_id)
    if was_active:
        save_active_pointer(
            workspace, CycleHop(campaign_id=hop.campaign_id, cycle_id=parent_cycle_id)
        )
    try:
        deleted, reason = campaign_store.try_delete_stub_cycle(hop)
    except Exception as exc:
        logger.warning("Stub cleanup raised for %s: %s", hop.cycle_id, exc)
        if was_active:
            save_active_pointer(workspace, hop)
        return False, str(exc)
    if not deleted and was_active:
        save_active_pointer(workspace, hop)
        logger.info(
            "Stub cleanup skipped for %s (%s); active pointer restored", hop.cycle_id, reason
        )
    elif deleted:
        logger.info("Stub fork cleaned up: %s (parent=%s)", hop.cycle_id, parent_cycle_id)
    return deleted, reason


__all__ = [
    "active_pointer_exists",
    "cleanup_stub_fork_if_empty",
    "clear_active_pointer",
    "read_active_pointer",
    "save_active_pointer",
]
