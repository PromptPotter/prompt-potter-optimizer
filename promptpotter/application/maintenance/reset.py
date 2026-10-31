from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from promptpotter.application.maintenance.survivors import ACCOUNT_SURVIVORS
from promptpotter.config.paths import BLOCK_PACKS_DIR, SHARED_CACHE_DIRS
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import (
    iter_files,
    rmtree_robust,
    unlink_robust,
    write_lock_name,
)
from promptpotter.infrastructure.store.layout import (
    DRAFTS_DIR,
    SandboxOwner,
    draft_dirs,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)
from promptpotter.infrastructure.store.ruler_store import RULER_FILE
from promptpotter.infrastructure.store.tenant_dataset_store import (
    BANKS_DIR,
    MATERIALIZED_DIR,
    UPLOADS_DIR,
)

logger = logging.getLogger(__name__)


# A head-to-head manifest, a ruler row and the workspace ledger (the active pointer beside it) name
# campaigns, so they go with them; a draft is a cycle not yet minted.
_DROP_NAMES = ("campaigns", "head_to_heads", DRAFTS_DIR, RULER_FILE, ".workspace")

# Exhaustive, so a leftover surfaces as unrecognized; `SHARED_CACHE_DIRS` is spliced so a new cache is never destroyed.
_PRESERVE_NAMES = (
    *SHARED_CACHE_DIRS,
    BLOCK_PACKS_DIR,
    "diagnostics",
    "traces",
    "backends",
    "datasets",
    MATERIALIZED_DIR,
    BANKS_DIR,
    UPLOADS_DIR,
    "task-context",
    ".cache",
    *ACCOUNT_SURVIVORS,
)


@dataclass(frozen=True, slots=True)
class TenantReset:
    workspace: WorkspaceDir
    drop: tuple[Path, ...]
    preserve: tuple[Path, ...]
    unrecognized: tuple[Path, ...]


class SandboxDrop(NamedTuple):
    sandbox: Path
    owner: SandboxOwner


@dataclass(frozen=True, slots=True)
class ResetPlan:
    projects_root: Path
    tenants: tuple[TenantReset, ...]
    sandboxes: tuple[SandboxDrop, ...]
    # Kept: with no owner record they name no tenant (as `jobs/reaper.py::reclaim_orphan_sandboxes`).
    unattributable_sandboxes: tuple[Path, ...]

    @property
    def drops(self) -> list[str]:
        return [
            *(str(p) for tenant in self.tenants for p in tenant.drop),
            *(str(target.sandbox) for target in self.sandboxes),
        ]


def _plan_tenant(workspace: WorkspaceDir) -> TenantReset:
    drop: list[Path] = []
    preserve: list[Path] = []
    unrecognized: list[Path] = []
    entries = sorted(workspace.iterdir()) if workspace.is_dir() else []
    locks = {write_lock_name(entry.name): entry.name for entry in entries}
    for entry in entries:
        # A write lock goes with the file it locks, and is never listed apart from it.
        if (locked := locks.get(entry.name)) is not None:
            if locked in _DROP_NAMES:
                drop.append(entry)
        elif entry.name in _DROP_NAMES:
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


def size_tag(path: Path) -> str:
    try:
        if path.is_file():
            return f"  ({path.stat().st_size:,} B)"
        if path.is_dir():
            sizes = [st.st_size for _p, st in iter_files(path)]
            return f"  ({len(sizes):,} files / {sum(sizes) / 1_048_576:.1f} MiB)"
    except OSError:
        pass
    return ""


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        rmtree_robust(path)
    elif path.exists() or path.is_symlink():
        unlink_robust(path)


def apply_reset(plan: ResetPlan) -> None:
    """Reads no ledger: a reset follows a reshape this build cannot read, and the account's charges hold its spend."""
    with ExitStack() as held:
        # Every draft's claim before anything goes: one a Start holds refuses the reset whole.
        claims = {
            tenant.workspace: [
                held.enter_context(DraftStore(tenant.workspace).hold(draft.name))
                for draft in draft_dirs(tenant.workspace)
            ]
            for tenant in plan.tenants
        }
        for tenant in plan.tenants:
            drafts = DraftStore(tenant.workspace)
            for claim in claims[tenant.workspace]:
                with claim:
                    _remove(drafts.draft_dir(claim.draft_id))
            for path in tenant.drop:
                _remove(path)
                logger.info("reset: removed %s", path)
    for target in plan.sandboxes:
        _remove(target.sandbox)
        logger.info("reset: removed inner sandbox %s", target.sandbox.name)
