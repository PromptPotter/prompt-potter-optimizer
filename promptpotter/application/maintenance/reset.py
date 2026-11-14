from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import jobs_dir
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, Job
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.data_root import LIVE_LOCKS, Fate, Kind, classify
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import (
    iter_files,
    probe_lock,
    prune_empty_dirs,
    unlink_robust,
)
from promptpotter.infrastructure.store.layout import (
    campaign_root_dir_for,
    campaigns_root_dir_for,
    draft_claim_path,
    draft_dir_for,
    draft_dirs,
    drafts_root_dir_for,
    in_inner_sandbox,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)
from promptpotter.shared.errors import ConflictError


@dataclass(frozen=True, slots=True)
class Drop:
    path: Path
    size: int
    # ``None``: no row names the file.
    kind: Kind | None


@dataclass(frozen=True, slots=True)
class ResetPlan:
    projects_root: Path
    roots: tuple[Path, ...]
    # Whose draft claims the reset takes before anything goes.
    workspaces: tuple[WorkspaceDir, ...]
    drops: tuple[Drop, ...]
    kept: int
    # Why the reset may not go now; asked again under the claims, since a run may start after the plan.
    refusal: str | None
    # What a landing writes beside the reset, each file read as it would then stand.
    staged: Mapping[Path, bytes]


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


def _drop(path: Path, size: int, data_root: Path) -> Drop | None:
    found = classify(PurePosixPath(path.relative_to(data_root).as_posix()))
    if found is None:
        return Drop(path, size, None)
    return Drop(path, size, found.kind) if found.kind.fate is Fate.RUN else None


def live_jobs(registry: JobRegistry) -> tuple[Job, ...]:
    """Every job whose run may yet write: each that holds spend, and each queued on its claim."""
    return (*registry.list_holding_spend(), *registry.list_queued())


def _reaches(job: Job, root: Path) -> bool:
    """What a live job's run may write under *root*: its workspace's own files (the account's
    log, its ledger) and its campaign, its draft too; a mint in flight names no campaign yet, so
    all of its workspace."""
    workspace = WorkspaceDir(Path(job.workspace).resolve())
    if workspace.is_relative_to(root) or (
        job.hop == UNRESOLVED_HOP and root.is_relative_to(workspace)
    ):
        return True
    if not root.is_relative_to(workspace):
        return False
    owned = [campaign_root_dir_for(workspace, job.campaign_id)]
    if isinstance(job.holder, CampaignLaunch) and job.holder.draft_id is not None:
        owned.append(draft_dir_for(workspace, job.holder.draft_id))
    if any(root.is_relative_to(one) or one.is_relative_to(root) for one in owned):
        return True
    others = (campaigns_root_dir_for(workspace), drafts_root_dir_for(workspace))
    return not any(root.is_relative_to(one) for one in others)


def holder_in_reach(
    jobs: Iterable[Job], roots: Iterable[Path], locks: Iterable[Path]
) -> str | None:
    """THE guard of the wipes that read no ledger (a reset's drops, a landing's run wipe), asked
    at the plan and again at the act under the admission gate: who holds one of *locks*
    (`data_root.py::LIVE_LOCKS`, a process's token), or a live job whose run reaches a root
    (`_reaches`)."""
    for lock in locks:
        if producer_lock.held(lock):
            return f"the live writer holding {lock}"
    resolved = [root.resolve() for root in roots]
    for job in jobs:
        if any(_reaches(job, root) for root in resolved):
            return f"{job.what} ({job.job_id})"
    return None


def _live(roots: tuple[Path, ...], drops: tuple[Drop, ...], registry: JobRegistry) -> str | None:
    # A process serving this home with no job in flight still writes any tenant's tree.
    locks = [drop.path for drop in drops if drop.kind in LIVE_LOCKS]
    locks += producer_lock.other_producers(registry.jobs_dir)
    held = holder_in_reach(live_jobs(registry), roots, locks)
    return None if held is None else f"{held} is live in reach — pause or stop it first"


def _claimed(workspaces: tuple[WorkspaceDir, ...]) -> str | None:
    for workspace in workspaces:
        for draft in draft_dirs(workspace):
            with probe_lock(draft_claim_path(workspace, draft.name)) as held:
                if held:
                    return f"draft {draft.name} is held by a Start or a command on it"
    return None


def plan_reset(
    projects_root: Path, *, tenant_id: str | None, staged: Mapping[Path, bytes]
) -> ResetPlan:
    """A named tenant is the caller's resolved identity, never a literal `default`: the first web
    sign-in renames it. *staged* is what a landing writes beside the reset."""
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
    held = tuple(sorted(drops, key=lambda d: d.path))
    registry = JobRegistry.as_staged(jobs_dir(projects_root), staged)
    return ResetPlan(
        projects_root,
        roots,
        workspaces,
        held,
        kept,
        _claimed(workspaces) or _live(roots, held, registry),
        staged,
    )


@contextmanager
def resetting(plan: ResetPlan, registry: JobRegistry) -> Iterator[None]:
    """The plan's drops go as the block ends, once every draft's claim is held and, under
    *registry*'s admission gate (the jobs read as the plan's staged files leave them), no run is
    live in reach; a refusal or a raise inside the block drops nothing."""
    if plan.refusal is not None:
        raise ConflictError(f"refusing to reset: {plan.refusal}")
    with ExitStack() as held:
        claims: set[Path] = set()
        for workspace in plan.workspaces:
            for draft in draft_dirs(workspace):
                held.enter_context(DraftStore(workspace).hold(draft.name))
                claims.add(draft_claim_path(workspace, draft.name))
        # After the claims, as a Start takes them: its draft's claim, then the gate.
        held.enter_context(registry.admission_gate())
        if (live := _live(plan.roots, plan.drops, registry)) is not None:
            raise ConflictError(f"refusing to reset: {live}")
        yield
        for drop in plan.drops:
            # A held claim goes as its hold retires: Windows refuses to delete a file held open.
            if drop.path not in claims:
                unlink_robust(drop.path)
    for root in plan.roots:
        prune_empty_dirs(root)
        # A sandbox goes whole; a tenant's own directory stays.
        if in_inner_sandbox(root):
            with contextlib.suppress(OSError):
                root.rmdir()


def apply_reset(plan: ResetPlan) -> None:
    """Reads no ledger: a reset follows a reshape this build cannot read, and the account's charges hold its spend."""
    with resetting(plan, JobRegistry.as_staged(jobs_dir(plan.projects_root), plan.staged)):
        return
