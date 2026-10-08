"""Drop campaigns + sessions; PRESERVE every paid cache (``layout.py::SHARED_CACHE_DIRS``) and every
config tier. The L4 inner sandboxes are the one drop target OUTSIDE the tenant dir (``layout.py``)."""

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


# Top-level names ``reset`` removes; everything else is preserved by default. A head-to-head
# manifest names campaigns, so it goes with them.
_DROP_NAMES = ("campaigns", "sessions", "head_to_heads")

# Preserved names, EXHAUSTIVE so anything left over surfaces as "unrecognized". `SHARED_CACHE_DIRS`
# is real LLM spend, SPLICED so a cache added there is never destroyed by `reset`.
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
    """One tenant workspace's top level, sorted into what a reset takes and what it leaves."""

    workspace: WorkspaceDir
    drop: tuple[Path, ...]
    preserve: tuple[Path, ...]
    unrecognized: tuple[Path, ...]
    holds_pointer: bool

    @property
    def pointer_label(self) -> str:
        return f"projects/{self.workspace.name}/.workspace/active_session.json"


class SandboxDrop(NamedTuple):
    """One inner sandbox plus the owner it banks INTO."""

    sandbox: Path
    owner: SandboxOwner


@dataclass(frozen=True, slots=True)
class ResetPlan:
    """What a reset would do, read off disk and applied by :func:`apply_reset` unchanged — the
    dry run and the real one are one plan, so a preview cannot describe a different removal."""

    projects_root: Path
    tenants: tuple[TenantReset, ...]
    sandboxes: tuple[SandboxDrop, ...]
    # No usable owner record names no workspace to bank into, so these are KEPT — the posture
    # `jobs/reaper.py::reclaim_orphan_sandboxes` takes over the same tree.
    unattributable_sandboxes: tuple[Path, ...]

    @property
    def drops(self) -> list[str]:
        """Every path the plan removes, in removal order."""
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
    """*tenant_id* ``None`` plans every tenant. A named one is the caller's resolved identity, never
    a literal ``default``: the first web sign-in RENAMES ``projects/default/``."""
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
    """The two ROBUST deleters: a tenant tree nests ``.inner/``-depth paths a plain ``rmtree``
    cannot remove at all."""
    if path.is_dir() and not path.is_symlink():
        rmtree_robust(path)
    elif path.exists() or path.is_symlink():
        unlink_robust(path)


def apply_reset(plan: ResetPlan) -> None:
    for tenant in plan.tenants:
        # Money first — every ledger lives inside `campaigns/`, so banking after the drop banks
        # nothing, and an account's ceiling becomes re-earnable by running a dev verb.
        CampaignStore(tenant.workspace).bank_all_before_removal()
        for path in tenant.drop:
            _remove(path)
            logger.info("reset: removed %s", path)
    for tenant in plan.tenants:
        if tenant.holds_pointer:
            clear_active_pointer(tenant.workspace)
            logger.info("reset: cleared active-session pointer for %s", tenant.workspace.name)
    # After the campaign trees: the sandbox residue is what its cycles have NOT already forwarded
    # onto an outer ledger, so outer totals then residue sum to everything exactly once.
    for target in plan.sandboxes:
        store = CampaignStore(tenant_workspace(plan.projects_root, target.owner.tenant_id))
        store.delete_inner_sandbox(target.sandbox, campaign_id=target.owner.campaign_id)
        logger.info("reset: removed inner sandbox %s", target.sandbox.name)
