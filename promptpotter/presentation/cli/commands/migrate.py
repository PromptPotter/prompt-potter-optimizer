"""Migrate v2 (backend-axis) layout to v3 (tenant / cycle / library).

Walks ``.promptpotter/projects/{backend_id}/…`` trees and rewrites them to
``.promptpotter/projects/{tenant_id}/{campaigns,library}/…`` per the M9
Track 6 spec. Hand-rolled MLflow data is dropped (the SDK regenerates it on
next run). Langfuse traces / prompts whose cycle cannot be unambiguously
determined land in ``.promptpotter/migration-orphans/`` rather than being
force-routed.

This command does not instantiate ``Stores`` and therefore bypasses the
schema-version guard — migrate is *how you reach v3*.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from promptpotter.presentation.cli.result import CommandResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

# session.json is intentionally absent — Wave B merged it into index.json.
SESSION_FILE_RENAMES: dict[str, str] = {
    "campaign_state.json": "dashboard.json",
    "campaign_control.json": "control.json",
    "campaign_output.log": "output.log",
    "campaign_log.md": "log.md",
    "notebook_journal.md": "journal.md",
    "claude_notes.md": "notes.md",
    "recon_results.json": "recon.json",
}

# Tenant-global single files under {backend_id}/. First-wins; additional
# copies land in migration-orphans suffixed by backend_id.
BACKEND_SHARED_FILES: tuple[str, ...] = (
    "dataset_runs.json",
    "search_memory.json",
    "prompt_aliases.json",
    "restructure_cache.json",
)

# Tenant-global directories under {backend_id}/. Contents merge into a
# single tenant dir; collisions land in migration-orphans.
BACKEND_SHARED_DIRS: dict[str, str] = {
    "dataset_runs": "library/dataset_runs",
    "adaptive_recon_plans": "library/recon_plans",
}

# Backend-local artefacts that wrap under library/backends/{backend_id}/.
BACKEND_LOCAL_CHILDREN: tuple[str, ...] = (
    "backend.json",
    "connector_profile.json",
    "sync",
    "executions",
    "datasets",
)


@dataclass
class Move:
    src: Path
    dst: Path | None  # None means drop
    kind: str  # "rename" | "move" | "merge-dir" | "drop" | "split-events" | "pointer"
    note: str = ""


@dataclass
class MigrationPlan:
    dotdir: Path  # the .promptpotter/ root
    projects_root: Path
    tenant_id: str
    tenant_root: Path  # target projects/{tenant_id}/
    moves: list[Move] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version_path: Path = field(init=False)
    pointer_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.schema_version_path = self.dotdir / "schema_version"
        self.pointer_path = self.dotdir / "active_session.json"


def _is_v2_backend_dir(path: Path) -> bool:
    """A v2 backend dir has a ``sessions/`` subdir (v3 tenants have ``campaigns/``)."""
    return path.is_dir() and (path / "sessions").is_dir()


def _existing_schema_version(dotdir: Path) -> int | None:
    marker = dotdir / "schema_version"
    if not marker.exists():
        return None
    try:
        return int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _add_session_moves(plan: MigrationPlan, backend_dir: Path) -> None:
    sessions_dir = backend_dir / "sessions"
    if not sessions_dir.is_dir():
        return
    for sess_dir in sorted(sessions_dir.iterdir()):
        if not sess_dir.is_dir():
            continue
        cid = sess_dir.name
        cid_root = plan.tenant_root / "campaigns" / cid

        session_json = sess_dir / "session.json"
        if session_json.exists():
            plan.moves.append(
                Move(session_json, None, "drop", "obsolete — merged into index.json in Wave B")
            )

        for old_name, new_name in SESSION_FILE_RENAMES.items():
            src = sess_dir / old_name
            if src.exists():
                plan.moves.append(Move(src, cid_root / new_name, "rename"))

        lf_state = sess_dir / "langfuse_state.json"
        if lf_state.exists():
            plan.moves.append(Move(lf_state, cid_root / "langfuse" / "state.json", "rename"))

        # Any other files in the session dir (e.g. unknown future artifacts)
        # move verbatim so nothing is silently dropped.
        known = set(SESSION_FILE_RENAMES) | {"session.json", "langfuse_state.json"}
        for child in sess_dir.iterdir():
            if child.name in known:
                continue
            dst = cid_root / child.name
            plan.moves.append(Move(child, dst, "move"))


def _add_campaign_moves(plan: MigrationPlan, backend_dir: Path) -> None:
    camps_dir = backend_dir / "campaigns"
    if not camps_dir.is_dir():
        return
    for child in sorted(camps_dir.iterdir()):
        if child.is_file() and child.suffix == ".json":
            # campaigns/{cid}.json → campaigns/{cid}/index.json
            cid = child.stem
            dst = plan.tenant_root / "campaigns" / cid / "index.json"
            plan.moves.append(Move(child, dst, "rename"))
        elif child.is_dir():
            # campaigns/{cid}/trial_*.json → campaigns/{cid}/trial_*.json (tenant-rooted)
            cid = child.name
            cid_root = plan.tenant_root / "campaigns" / cid
            for trial in sorted(child.iterdir()):
                plan.moves.append(Move(trial, cid_root / trial.name, "move"))


def _add_library_moves(plan: MigrationPlan, backend_dir: Path, backend_id: str) -> None:
    # Tenant-global directories — merge contents across backends.
    for name, rel in BACKEND_SHARED_DIRS.items():
        src = backend_dir / name
        if src.is_dir():
            plan.moves.append(Move(src, plan.tenant_root / rel, "merge-dir"))

    # Tenant-global singleton files — first-wins; extras to migration-orphans.
    for name in BACKEND_SHARED_FILES:
        src = backend_dir / name
        if src.is_file():
            plan.moves.append(
                Move(src, plan.tenant_root / "library" / name, "rename", note=backend_id)
            )

    # Backend-local children wrap under library/backends/{backend_id}/.
    for name in BACKEND_LOCAL_CHILDREN:
        src = backend_dir / name
        if src.exists():
            dst = plan.tenant_root / "library" / "backends" / backend_id / name
            plan.moves.append(Move(src, dst, "move"))


def _add_obs_moves(plan: MigrationPlan, backend_dir: Path) -> None:
    obs_dir = backend_dir / "obs"
    if not obs_dir.is_dir():
        return

    exp_dir = obs_dir / "experiments"
    if exp_dir.exists():
        plan.moves.append(
            Move(exp_dir, None, "drop", "MLflow SDK regenerates on next run (schema-incompatible)")
        )

    events_path = obs_dir / "langfuse" / "events.jsonl"
    if events_path.is_file():
        plan.moves.append(Move(events_path, None, "split-events"))

    # Langfuse shadow & prompts — cycle cannot be inferred from filename, so
    # park under migration-orphans and let the user inspect if needed.
    orphans = plan.dotdir / "migration-orphans"
    for sub in ("traces", "observations", "scores", "datasets"):
        src = obs_dir / "langfuse" / sub
        if src.exists():
            plan.moves.append(Move(src, orphans / "langfuse" / sub, "merge-dir"))

    prompts_src = obs_dir / "prompts"
    if prompts_src.exists():
        plan.moves.append(Move(prompts_src, orphans / "prompts", "merge-dir"))


def build_plan(dotdir: Path, tenant_id: str) -> MigrationPlan:
    projects_root = dotdir / "projects"
    tenant_root = projects_root / tenant_id
    plan = MigrationPlan(
        dotdir=dotdir, projects_root=projects_root, tenant_id=tenant_id, tenant_root=tenant_root
    )

    if not projects_root.is_dir():
        plan.warnings.append(f"no projects/ directory at {projects_root}")
        return plan

    # Reject if the target tenant dir already exists and is non-empty — that
    # usually means a partial migration or a v3 install; refuse to merge blindly.
    if tenant_root.exists() and any(tenant_root.iterdir()):
        plan.warnings.append(
            f"tenant dir already exists and is non-empty: {tenant_root}. "
            "Remove it or pick a different --tenant before migrating."
        )
        return plan

    if tenant_id in {p.name for p in projects_root.iterdir() if _is_v2_backend_dir(p)}:
        plan.warnings.append(
            f"A v2 backend directory is named '{tenant_id}' — rename --tenant to avoid collision."
        )
        return plan

    v2_backends = sorted(p for p in projects_root.iterdir() if _is_v2_backend_dir(p))
    if not v2_backends:
        plan.warnings.append("no v2 backend dirs found under projects/ — nothing to migrate")
        return plan

    for backend_dir in v2_backends:
        _add_session_moves(plan, backend_dir)
        _add_campaign_moves(plan, backend_dir)
        _add_library_moves(plan, backend_dir, backend_dir.name)
        _add_obs_moves(plan, backend_dir)

    return plan


def _render_plan(plan: MigrationPlan) -> str:
    if plan.warnings:
        return "No migration performed:\n  " + "\n  ".join(plan.warnings)
    lines = [
        f"Migration plan: {plan.projects_root} → tenant '{plan.tenant_id}'",
        f"  {len(plan.moves)} operations",
    ]
    kinds: dict[str, int] = {}
    for mv in plan.moves:
        kinds[mv.kind] = kinds.get(mv.kind, 0) + 1
    for kind, count in sorted(kinds.items()):
        lines.append(f"    {kind}: {count}")
    sample = plan.moves[:20]
    if sample:
        lines.append("  sample (first 20):")
        for mv in sample:
            if mv.dst is None:
                lines.append(f"    {mv.kind}: {mv.src}  ({mv.note})")
            else:
                lines.append(f"    {mv.kind}: {mv.src} → {mv.dst}")
    return "\n".join(lines)


def _merge_directory(src: Path, dst: Path, orphans_root: Path, tag: str) -> list[str]:
    """Merge the contents of ``src`` into ``dst``; collisions go to orphans/.

    Returns a list of collision notes.
    """
    notes: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if target.exists():
            orphan_dst = orphans_root / tag / child.name
            orphan_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(orphan_dst))
            notes.append(f"collision: {target} already exists → {orphan_dst}")
        else:
            shutil.move(str(child), str(target))
    # src should now be empty; remove it.
    if src.exists() and not any(src.iterdir()):
        src.rmdir()
    return notes


def _split_events(src: Path, tenant_root: Path, orphans_root: Path) -> tuple[int, int]:
    """Split events.jsonl by ``campaign_id`` field; orphans to migration-orphans."""
    routed = 0
    orphaned = 0
    per_cid: dict[str, list[str]] = {}
    orphan_lines: list[str] = []
    with src.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                orphan_lines.append(line)
                orphaned += 1
                continue
            cid = obj.get("campaign_id")
            if isinstance(cid, str) and cid:
                per_cid.setdefault(cid, []).append(line)
                routed += 1
            else:
                orphan_lines.append(line)
                orphaned += 1
    for cid, lines in per_cid.items():
        dst = tenant_root / "campaigns" / cid / "events.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    if orphan_lines:
        dst = orphans_root / "events.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as f:
            for ln in orphan_lines:
                f.write(ln + "\n")
    src.unlink()
    return routed, orphaned


def execute_plan(plan: MigrationPlan) -> list[str]:
    """Run the moves. Returns per-operation notes (collisions, drops, …)."""
    notes: list[str] = []
    orphans_root = plan.dotdir / "migration-orphans"
    seen_singletons: set[Path] = set()

    for mv in plan.moves:
        if mv.kind == "drop":
            if mv.src.is_dir():
                shutil.rmtree(mv.src)
            else:
                mv.src.unlink()
            notes.append(f"dropped: {mv.src}  ({mv.note})")
            continue

        if mv.kind == "split-events":
            routed, orphaned = _split_events(mv.src, plan.tenant_root, orphans_root)
            notes.append(f"events.jsonl split: {routed} routed, {orphaned} orphaned")
            continue

        if mv.dst is None:
            # Shouldn't happen for non-drop/non-split entries, but guard.
            notes.append(f"skipped: {mv.kind} with no destination for {mv.src}")
            continue

        if mv.kind == "merge-dir":
            _merge_directory(mv.src, mv.dst, orphans_root, f"merge/{mv.dst.name}")
            continue

        # rename / move — collision detection for singleton library files.
        if mv.kind == "rename" and mv.note:  # singleton, tagged by backend_id
            if mv.dst in seen_singletons or mv.dst.exists():
                orphan_dst = orphans_root / mv.note / mv.src.name
                orphan_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(mv.src), str(orphan_dst))
                notes.append(f"singleton collision: {mv.src} kept at {orphan_dst} (first-wins)")
                continue
            seen_singletons.add(mv.dst)

        mv.dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mv.src), str(mv.dst))

    # Clean up now-empty v2 backend dirs.
    for child in plan.projects_root.iterdir():
        if child.resolve() == plan.tenant_root.resolve():
            continue
        if not child.is_dir():
            continue
        # Try to remove empty subtrees, but don't fail if files remain.
        try:
            _prune_empty(child)
        except OSError as exc:
            notes.append(f"could not prune {child}: {exc}")

    _rewrite_pointer(plan)
    plan.schema_version_path.write_text(f"{SCHEMA_VERSION}\n", encoding="utf-8")
    notes.append(f"schema_version written: {plan.schema_version_path}")
    return notes


def _prune_empty(root: Path) -> None:
    """Remove empty subdirectories bottom-up. Leaves non-empty trees alone."""
    for child in sorted(root.iterdir(), key=lambda p: -len(p.parts)):
        if child.is_dir():
            with contextlib.suppress(OSError):
                _prune_empty(child)
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def _rewrite_pointer(plan: MigrationPlan) -> None:
    """Rewrite ``active_session.json`` from ``{backend_id, session_id}`` to v3 shape."""
    if not plan.pointer_path.exists():
        return
    try:
        data = json.loads(plan.pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    session_id = data.get("session_id") or data.get("cycle_id")
    if not session_id:
        return
    plan.pointer_path.write_text(
        json.dumps({"tenant_id": plan.tenant_id, "cycle_id": session_id}),
        encoding="utf-8",
    )


async def cmd_migrate(args: argparse.Namespace) -> CommandResult:
    """CLI entry for the migrate verb."""
    if args.from_path:
        dotdir = Path(args.from_path)
    else:
        repo_root = Path(__file__).resolve().parents[4]
        dotdir = repo_root / ".promptpotter"

    tenant_id = getattr(args, "tenant", "default") or "default"

    existing = _existing_schema_version(dotdir)
    if existing is not None and existing >= SCHEMA_VERSION:
        return CommandResult(
            data={"status": "noop", "schema_version": existing},
            human=f"Already at schema_version={existing}; nothing to migrate.",
        )

    plan = build_plan(dotdir, tenant_id)

    if args.dry_run:
        return CommandResult(
            data={
                "dry_run": True,
                "warnings": plan.warnings,
                "n_moves": len(plan.moves),
                "moves": [
                    {"kind": m.kind, "src": str(m.src), "dst": str(m.dst) if m.dst else None}
                    for m in plan.moves
                ],
            },
            human=_render_plan(plan),
        )

    if plan.warnings and not plan.moves:
        return CommandResult(
            data={"status": "noop", "warnings": plan.warnings},
            human=_render_plan(plan),
        )

    notes = execute_plan(plan)
    return CommandResult(
        data={
            "status": "migrated",
            "tenant_id": tenant_id,
            "n_moves": len(plan.moves),
            "notes": notes,
            "schema_version": SCHEMA_VERSION,
        },
        human=(
            f"Migrated {len(plan.moves)} items to {plan.tenant_root}.\n"
            f"Schema version: {SCHEMA_VERSION}\n" + ("\n".join(f"  {n}" for n in notes[:30]))
        ),
    )
