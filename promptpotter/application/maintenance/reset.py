from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import rmtree_robust, unlink_robust
from promptpotter.infrastructure.store.layout import (
    SHARED_CACHE_DIRS,
    SandboxOwner,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    clear_active_pointer,
)

logger = logging.getLogger(__name__)

__all__ = ["ResetPlan", "SandboxDrop", "TenantReset", "apply_reset", "plan_reset"]


# A head-to-head manifest names campaigns, so it goes with them.
_DROP_NAMES = ("campaigns", "head_to_heads")

# Exhaustive, so a leftover surfaces as unrecognized; `SHARED_CACHE_DIRS` is spliced so a new cache is never destroyed.
_PRESERVE_NAMES = (
    *SHARED_CACHE_DIRS,
    "diagnostics",
    "traces",
    "backends",
    "datasets",
    "benchmark-rows",
    "task-context",
    ".workspace",
    ".cache",
    "user.json",
)


@dataclass(frozen=True, slots=True)
class TenantReset:
    workspace: WorkspaceDir
    drop: tuple[Path, ...]
    preserve: tuple[Path, ...]
    unrecognized: tuple[Path, ...]
    holds_pointer: bool

    @property
    def pointer_label(self) -> str:
        return f"projects/{self.workspace.name}/.workspace/active_session.json"


class SandboxDrop(NamedTuple):
    sandbox: Path
    owner: SandboxOwner


@dataclass(frozen=True, slots=True)
class ResetPlan:
    projects_root: Path
    tenants: tuple[TenantReset, ...]
    sandboxes: tuple[SandboxDrop, ...]
    # Kept: no owner record names no workspace to bank into (as `jobs/reaper.py::reclaim_orphan_sandboxes`).
    unattributable_sandboxes: tuple[Path, ...]

    @property
    def drops(self) -> list[str]:
        return [
            *(str(p) for tenant in self.tenants for p in tenant.drop),
            *(tenant.pointer_label for tenant in self.tenants if tenant.holds_pointer),
            *(str(target.sandbox) for target in self.sandboxes),
        ]


def _plan_tenant(workspace: WorkspaceDir) -> TenantReset:
    drop: list[Path] = []
    preserve: list[Path] = []
    unrecognized: list[Path] = []
    for entry in sorted(workspace.iterdir()) if workspace.is_dir() else ():
        if entry.name in _DROP_NAMES:
            if entry.exists():
                drop.append(entry)
        elif entry.name in _PRESERVE_NAMES:
            preserve.append(entry)
        else:
            unrecognized.append(entry)
    return TenantReset(
        workspace=workspace,
        drop=tuple(drop),
        preserve=tuple(preserve),
        unrecognized=tuple(unrecognized),
        holds_pointer=active_pointer_exists(workspace),
    )


def plan_reset(projects_root: Path, *, tenant_id: str | None) -> ResetPlan:
    """A named tenant is the caller's resolved identity, never a literal `default`: the first web sign-in renames it."""
    if tenant_id is not None:
        workspaces = [tenant_workspace(projects_root, tenant_id)]
    elif projects_root.is_dir():
        workspaces = [WorkspaceDir(p) for p in sorted(projects_root.iterdir()) if p.is_dir()]
    else:
        workspaces = []
    tenant_names = {ws.name for ws in workspaces}

    sandboxes: list[SandboxDrop] = []
    unattributable: list[Path] = []
    inner_dir = inner_sandboxes_dir(projects_root)
    for sandbox in sorted(inner_dir.iterdir()) if inner_dir.is_dir() else ():
        if not sandbox.is_dir():
            continue
        owner = read_sandbox_owner(sandbox)
        if owner is None:
            unattributable.append(sandbox)
        elif owner.tenant_id in tenant_names:
            sandboxes.append(SandboxDrop(sandbox, owner))
    return ResetPlan(
        projects_root=projects_root,
        tenants=tuple(_plan_tenant(ws) for ws in workspaces),
        sandboxes=tuple(sandboxes),
        unattributable_sandboxes=tuple(unattributable),
    )


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        rmtree_robust(path)
    elif path.exists() or path.is_symlink():
        unlink_robust(path)


def apply_reset(plan: ResetPlan) -> None:
    for tenant in plan.tenants:
        # Money first: every ledger lives inside `campaigns/`, so banking after the drop banks nothing.
        CampaignStore(tenant.workspace).bank_all_before_removal()
        for path in tenant.drop:
            _remove(path)
            logger.info("reset: removed %s", path)
    for tenant in plan.tenants:
        if tenant.holds_pointer:
            clear_active_pointer(tenant.workspace)
            logger.info("reset: cleared active-session pointer for %s", tenant.workspace.name)
    # After the campaign trees: outer totals, then the sandbox residue not already forwarded, sum exactly once.
    for target in plan.sandboxes:
        store = CampaignStore(tenant_workspace(plan.projects_root, target.owner.tenant_id))
        store.delete_inner_sandbox(target.sandbox, campaign_id=target.owner.campaign_id)
        logger.info("reset: removed inner sandbox %s", target.sandbox.name)
