"""Never default the ``WorkspaceDir`` key: an inner cycle would retarget the OPERATOR's pointer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import validate_path_component, write_bytes, write_lock
from promptpotter.infrastructure.store.layout import WORKSPACE_STATE_DIR
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.errors import FileUnreadableError

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)


def active_pointer_path(workspace: WorkspaceDir) -> Path:
    return workspace / WORKSPACE_STATE_DIR / "active_session.json"


def save_active_pointer(workspace: WorkspaceDir, hop: CycleHop) -> None:
    with write_lock(active_pointer_path(workspace)):
        _point(workspace, hop)


def clear_active_pointer(workspace: WorkspaceDir) -> None:
    with write_lock(active_pointer_path(workspace)):
        _point(workspace, None)


def _point(workspace: WorkspaceDir, hop: CycleHop | None) -> None:
    path = active_pointer_path(workspace)
    if hop is None:
        path.unlink(missing_ok=True)
        return
    validate_path_component(hop.campaign_id)
    validate_path_component(hop.cycle_id)
    write_bytes(path, hop.model_dump_json().encode("utf-8"))


def _swap_pointer(workspace: WorkspaceDir, was: CycleHop | None, now: CycleHop | None) -> bool:
    """The pointer moved from *was* to *now* under its write lock, only where it still reads *was*:
    a launch's write since stands."""
    with write_lock(active_pointer_path(workspace)):
        if read_active_pointer(workspace) != was:
            return False
        _point(workspace, now)
    return True


def read_active_pointer(workspace: WorkspaceDir) -> CycleHop | None:
    return read_file(active_pointer_path(workspace), CycleHop.model_validate_json)


def active_pointer_exists(workspace: WorkspaceDir) -> bool:
    return active_pointer_path(workspace).exists()


def cleanup_stub_fork_if_empty(*, campaign_store: CampaignStore, hop: CycleHop) -> tuple[bool, str]:
    """An active stub hands the pointer to the parent its mint names; one cut from another tree
    names no cycle here, so the pointer is cleared."""
    workspace = campaign_store.workspace
    index = campaign_store.load(hop, moment=None)
    parent_cycle_id = None if index is None else index.parent_cycle_id
    parent = (
        None
        if parent_cycle_id is None
        else CycleHop(campaign_id=hop.campaign_id, cycle_id=parent_cycle_id)
    )
    was_active = _swap_pointer(workspace, hop, parent)
    try:
        deleted, reason = campaign_store.try_delete_stub_cycle(hop)
    except (OSError, FileUnreadableError) as exc:
        logger.warning("Stub cleanup raised for %s: %s", hop.cycle_id, exc)
        if was_active:
            _swap_pointer(workspace, parent, hop)
        return False, str(exc)
    if not deleted and was_active:
        _swap_pointer(workspace, parent, hop)
        logger.info(
            "Stub cleanup skipped for %s (%s); active pointer restored", hop.cycle_id, reason
        )
    elif deleted:
        logger.info("Stub fork cleaned up: %s (parent=%s)", hop.cycle_id, parent_cycle_id)
    return deleted, reason
