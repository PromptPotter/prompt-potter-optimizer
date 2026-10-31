from __future__ import annotations

import contextlib
import logging
import os
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.data_root import (
    Fate,
    Found,
    Kind,
    classify,
    unloadable_why,
)
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import iter_files, unlink_robust
from promptpotter.infrastructure.store.layout import (
    draft_dirs,
    in_inner_sandbox,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Drop:
    path: Path
    size: int
    # ``None``: no row names the file.
    kind: Kind | None
    # Set on a cache file that no longer loads.
    unloadable: str | None = None


@dataclass(frozen=True, slots=True)
class ResetPlan:
    data_root: Path
    roots: tuple[Path, ...]
    # Whose draft claims the reset takes before anything goes.
    workspaces: tuple[WorkspaceDir, ...]
    drops: tuple[Drop, ...]
    kept: int


def _owned_sandboxes(projects_root: Path, tenant_id: str) -> list[Path]:
    """A sandbox naming no owner is no tenant's: only a reset of every tenant takes it."""
    inner = inner_sandboxes_dir(projects_root)
    return [
        sandbox
        for sandbox in (sorted(inner.iterdir()) if inner.is_dir() else ())
        if sandbox.is_dir()
        and (owner := read_sandbox_owner(sandbox)) is not None
        and owner.tenant_id == tenant_id
    ]


def _classify(path: Path, data_root: Path) -> Found | None:
    return classify(PurePosixPath(path.relative_to(data_root).as_posix()))


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str


class Proof(NamedTuple):
    held: int
    unknown: tuple[Path, ...]
    unloadable: tuple[Unloadable, ...]


def prove(data_root: Path) -> Proof:
    """Every file under the data root loads as its row shapes it: a landing's proof."""
    # A ledger line holds the state kinds each optimizer package registers as it loads.
    complete_registries(every_treatment=False)
    held = 0
    unknown: list[Path] = []
    unloadable: list[Unloadable] = []
    for path, _st in iter_files(data_root):
        if (found := _classify(path, data_root)) is None:
            unknown.append(path)
        elif (why := unloadable_why(path, found)) is not None:
            unloadable.append(Unloadable(path, found, why))
        else:
            held += 1
    return Proof(held, tuple(sorted(unknown)), tuple(sorted(unloadable)))


def _drop(path: Path, size: int, data_root: Path) -> Drop | None:
    found = _classify(path, data_root)
    if found is None:
        return Drop(path, size, None)
    if found.kind.fate is Fate.RUN:
        return Drop(path, size, found.kind)
    if found.kind.fate is Fate.CACHE and (why := unloadable_why(path, found)) is not None:
        return Drop(path, size, found.kind, why)
    return None


def plan_reset(projects_root: Path, *, tenant_id: str | None) -> ResetPlan:
    """A named tenant is the caller's resolved identity, never a literal `default`: the first web sign-in renames it."""
    complete_registries(every_treatment=False)
    data_root = projects_root.parent
    if tenant_id is None:
        roots: tuple[Path, ...] = (data_root,)
        workspaces = tuple(
            WorkspaceDir(p)
            for p in (sorted(projects_root.iterdir()) if projects_root.is_dir() else ())
            if p.is_dir()
        )
    else:
        workspace = tenant_workspace(projects_root, tenant_id)
        roots = (workspace, *_owned_sandboxes(projects_root, tenant_id))
        workspaces = (workspace,)
    drops: list[Drop] = []
    kept = 0
    for root in roots:
        for path, st in iter_files(root):
            if (drop := _drop(path, st.st_size, data_root)) is None:
                kept += 1
            else:
                drops.append(drop)
    return ResetPlan(data_root, roots, workspaces, tuple(sorted(drops, key=lambda d: d.path)), kept)


def _prune_empty(root: Path) -> None:
    """Bottom-up; a sandbox goes whole, a tenant's own directory stays."""
    for current, _dirs, _files in os.walk(root, topdown=False):
        if Path(current) != root or in_inner_sandbox(root):
            with contextlib.suppress(OSError):
                os.rmdir(current)


def apply_reset(plan: ResetPlan) -> None:
    """Reads no ledger: a reset follows a reshape this build cannot read, and the account's charges hold its spend."""
    with ExitStack() as held:
        # Every draft's claim before anything goes: one a Start holds refuses the reset whole.
        for workspace in plan.workspaces:
            for draft in draft_dirs(workspace):
                held.enter_context(DraftStore(workspace).hold(draft.name))
        for drop in plan.drops:
            unlink_robust(drop.path)
    for root in plan.roots:
        _prune_empty(root)
    logger.info("reset: removed %d file(s), kept %d", len(plan.drops), plan.kept)
