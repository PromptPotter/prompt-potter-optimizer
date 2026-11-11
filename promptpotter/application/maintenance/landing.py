"""A landing's one write to the data root, under the code it lands: every migration staged, every
file proven as it would then stand, and all of it written only where nothing refuses. Nothing may
roll the code back once it has written: the old code would run on what it wrote."""

from __future__ import annotations

import functools
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from promptpotter.application.initialization.wiring import complete_registries, read_treatment
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.migrate import MIGRATIONS
from promptpotter.application.maintenance.reset import ResetPlan, plan_reset, resetting
from promptpotter.application.optimizer_manifest import checkin_manifest
from promptpotter.config.paths import default_projects_root, jobs_dir, user_data_root
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.data_root import (
    CHECKIN_SHADOW,
    KINDS,
    LANDED_SCHEMA_FILE,
    LOAD_ERRORS,
    OPTIMIZER_SHADOW,
    PRODUCER_LOCKS,
    Fate,
    Found,
    Staged,
    classify,
    row_schema,
    run_owner,
    run_unit,
    unloadable_why,
)
from promptpotter.infrastructure.store.io import (
    iter_files,
    read_text_optional,
    rmtree_robust,
    unlink_robust,
    write_bytes,
    write_lock,
    write_text,
)
from promptpotter.infrastructure.store.layout import INNER_SANDBOXES_DIR, CampaignLayout
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PotterError
from promptpotter.shared.hashing import stable_hash


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str


class Landing(NamedTuple):
    staged: tuple[Staged, ...]
    # Asked with `--reset`: every account's run data, whether it loads or not.
    reset: ResetPlan | None
    # What the landing drops unasked: each cache file that does not load, and each run that does
    # not, its campaign or draft whole where the file is one of its records (`data_root.run_unit`).
    caches: tuple[Unloadable, ...]
    runs: tuple[Unloadable, ...]
    # A workspace campaign a mint left before its manifest: no verb lists it, and a draft's billed
    # ledger may sit in it, so it goes as `delete` takes a campaign, its spend banked first.
    torn: tuple[Unloadable, ...]
    proven: int
    # What refuses the landing: a file no row names, a survivor that does not load, a live run's.
    unknown: tuple[Path, ...]
    unloadable: tuple[Unloadable, ...]

    @property
    def refusal(self) -> str | None:
        if any(one.found.kind.fate is Fate.SURVIVOR for one in self.unloadable):
            return (
                "a survivor does not load, and no migration maps it: the landed code owes one "
                "(`maintenance/migrate.py::MIGRATIONS`)"
            )
        if self.reset is not None and self.reset.refusal is not None:
            return self.reset.refusal
        if self.unknown:
            return "a file no row names is in the way: `data_root.py::KINDS` owes it a row"
        if self.unloadable:
            return "a live run's file does not load: pause or stop it first"
        return None

    @property
    def refused(self) -> bool:
        return self.refusal is not None


def _staged(projects_root: Path) -> dict[Path, Staged]:
    staged: dict[Path, Staged] = {}
    for name, migration in MIGRATIONS.items():
        for one in migration(projects_root):
            if one.path in staged:
                raise ValueError(f"two migrations stage {one.path}")
            staged[one.path] = one._replace(said=f"{name}: {one.said}")
    return staged


def plan_landing(projects_root: Path, *, reset: bool) -> Landing:
    """Writes nothing."""
    # A ledger line holds the state kinds each optimizer package registers as it loads.
    complete_registries(every_treatment=False)
    data_root = projects_root.parent
    staged = _staged(projects_root)
    found_at: dict[Path, Found | None] = {}
    why_at: dict[Path, str | None] = {}
    for path in sorted({path for path, _st in iter_files(data_root)} | staged.keys()):
        found = found_at[path] = classify(PurePosixPath(path.relative_to(data_root).as_posix()))
        if found is not None and found.kind.fate is not Fate.RUN:
            held = staged.get(path)
            why_at[path] = unloadable_why(
                path, found, staged=None if held is None else held.data
            ) or _shadow_why(path, found)
    # A survivor that does not load refuses the landing whole; a reset is planned over survivors
    # that load, read as staged.
    survivor_refuses = any(
        why is not None
        and (found := found_at[path]) is not None
        and found.kind.fate is Fate.SURVIVOR
        for path, why in why_at.items()
    )
    swept = (
        plan_reset(projects_root, tenant_id=None, staged={p: one.data for p, one in staged.items()})
        if reset and not survivor_refuses
        else None
    )
    # A file no row names refuses whether or not the reset would drop it.
    gone = set() if swept is None else {drop.path for drop in swept.drops if drop.kind is not None}
    for path, found in found_at.items():
        if path not in gone and found is not None and path not in why_at:
            held = staged.get(path)
            why_at[path] = unloadable_why(path, found, staged=None if held is None else held.data)
    runs: dict[Path, Unloadable] = {}
    unloadable: list[Unloadable] = []
    for path, why in why_at.items():
        found = found_at[path]
        if why is None or found is None or found.kind.fate is not Fate.RUN:
            continue
        unit = data_root / run_unit(
            PurePosixPath(path.relative_to(data_root).as_posix()), found.kind
        )
        if _live(unit, found_at):
            unloadable.append(Unloadable(path, found, why))
        else:
            runs.setdefault(unit, Unloadable(unit, found, f"{path}: {why}"))
    # An inner sandbox's torn mint goes with its sandbox, banked by the outer campaign's delete.
    manifestless: dict[Path, Found] = {}
    for path, found in found_at.items():
        owner = run_owner(PurePosixPath(path.relative_to(data_root).as_posix()))
        if (
            found is not None
            and path not in gone
            and owner is not None
            and owner.parts[0] != INNER_SANDBOXES_DIR
            and owner.parts[-2] == "campaigns"
            and (unit := data_root / owner) not in runs
            and CampaignLayout(unit).manifest not in found_at
        ):
            manifestless.setdefault(unit, found)
    torn: list[Unloadable] = []
    spending = _held_workspaces(projects_root, staged) if manifestless else set()
    for unit, found in manifestless.items():
        why = f"{unit}: a mint torn before its manifest"
        if _live(unit, found_at) or unit.parent.parent.resolve() in spending:
            unloadable.append(Unloadable(unit, found, f"{why}, while a run holds its workspace"))
        else:
            torn.append(Unloadable(unit, found, why))
    caches: list[Unloadable] = []
    unknown: list[Path] = []
    proven = 0
    for path, found in found_at.items():
        if path in gone or any(
            path.is_relative_to(unit) for unit in (*runs, *(one.path for one in torn))
        ):
            continue
        if found is None:
            unknown.append(path)
        elif (why := why_at[path]) is None:
            proven += 1
        elif found.kind.fate is Fate.CACHE:
            caches.append(Unloadable(path, found, why))
        elif found.kind.fate is not Fate.RUN:
            unloadable.append(Unloadable(path, found, why))
    return Landing(
        tuple(staged.values()),
        swept,
        tuple(caches),
        tuple(runs.values()),
        tuple(torn),
        proven,
        tuple(unknown),
        tuple(unloadable),
    )


def _shadow_why(path: Path, found: Found) -> str | None:
    """An install's shadow of a shipped manifest, read by its own reader as the API's boot reads
    it: a knob, roster or prompt its shape admits can still refuse there."""
    if found.kind not in (OPTIMIZER_SHADOW, CHECKIN_SHADOW) or not path.is_relative_to(
        user_data_root()
    ):
        return None
    try:
        if found.kind is CHECKIN_SHADOW:
            checkin_manifest()
        else:
            read_treatment(path.parent.name)
    except (PotterError, *LOAD_ERRORS) as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    return None


def _held_workspaces(projects_root: Path, staged: dict[Path, Staged]) -> set[Path]:
    """Where a job holds spend: a mint in flight there has no manifest yet, and no producer lock."""
    jobs = JobRegistry.as_staged(
        jobs_dir(projects_root), {p: one.data for p, one in staged.items()}
    )
    return {Path(job.workspace).resolve() for job in jobs.list_holding_spend()}


def _live(unit: Path, found_at: dict[Path, Found | None]) -> bool:
    """A producer holds a cycle of the run: its files are its own, never the landing's to drop."""
    return any(
        found is not None
        and found.kind in PRODUCER_LOCKS
        and path.is_relative_to(unit)
        and producer_lock.held(path)
        for path, found in found_at.items()
    )


def apply_landing(projects_root: Path, landing: Landing) -> None:
    """A refused landing raises before a write, and so does a reset's own refusal, asked again.
    The root is stamped with the record schema it landed under last."""
    if landing.refused:
        raise ConflictError(
            "refusing to land: run `land-data --dry-run` for what refuses it",
            code="data_root_unlanded",
        )
    with nullcontext() if landing.reset is None else resetting(landing.reset):
        for one in landing.staged:
            write_bytes(one.path, one.data)
        for cache in landing.caches:
            unlink_robust(cache.path)
        for run in landing.runs:
            if run.path.is_dir():
                rmtree_robust(run.path)
            else:
                unlink_robust(run.path)
        for mint in landing.torn:
            CampaignStore(WorkspaceDir(mint.path.parent.parent)).delete_campaign(
                mint.path.name, keep_results=False, changed_at=utcnow_iso()
            )
    write_text(_stamp(projects_root), landing_key())


@functools.cache
def landing_key() -> str:
    """Every row's patterns, fate and shape, and every migration: a reshape of any lands again."""
    rows = {kind.name: row_schema(kind) for kind in KINDS}
    return stable_hash({"rows": rows, "migrations": sorted(MIGRATIONS)}, length=16)


def _stamp(projects_root: Path) -> Path:
    return jobs_dir(projects_root) / LANDED_SCHEMA_FILE


def land_at_entry(*, every_treatment: bool) -> None:
    """Every process's first step, before it reads the data root: its registries complete, then
    the root lands under this code's record schema, once per schema. A landing that refuses stops
    the process, having written nothing."""
    complete_registries(every_treatment=every_treatment)
    projects_root = default_projects_root()
    stamp = _stamp(projects_root)
    if read_text_optional(stamp).strip() == landing_key():
        return
    with write_lock(stamp):
        if read_text_optional(stamp).strip() == landing_key():
            return
        apply_landing(projects_root, plan_landing(projects_root, reset=False))
