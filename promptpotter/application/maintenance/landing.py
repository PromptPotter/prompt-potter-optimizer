"""A landing's one write to the data root, under the code it lands: every migration staged, every
file proven as it would then stand, and all of it written only where nothing refuses. Nothing may
roll the code back once it has written: the old code would run on what it wrote."""

from __future__ import annotations

import functools
import logging
from collections import Counter
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from pydantic import ValidationError

from promptpotter.application.initialization.wiring import complete_registries, read_treatment
from promptpotter.application.jobs.launcher.admission import destroying
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.migrate import MIGRATIONS
from promptpotter.application.maintenance.reset import (
    ResetPlan,
    holder_in_reach,
    live_jobs,
    plan_reset,
    resetting,
)
from promptpotter.application.notices import tell_landed
from promptpotter.application.optimizer_manifest import checkin_manifest
from promptpotter.config.paths import default_projects_root, jobs_dir, user_data_root
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.strict_model import FrozenModel
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
from promptpotter.infrastructure.store.stores import tenant_stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PotterError
from promptpotter.shared.hashing import stable_hash

logger = logging.getLogger(__name__)


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str

    @property
    def said(self) -> str:
        return f"{self.found.kind.fate.value} {self.found.kind.name} {self.path}: {self.why}"


class Refused(NamedTuple):
    """What refuses a landing, the paths that do and its remedy."""

    why: str
    paths: tuple[Path, ...]
    remedy: str = ""


# A refusal names its first few paths; `land-data --dry-run` lists every one.
_NAMED = 3


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
    # What the act found a run or a pass took since the plan, or a mint it planned torn that wrote
    # its manifest since, so it kept (`apply_landing`): the stamp then names no key, and the next
    # entry lands the root again.
    kept: tuple[Unloadable, ...]

    @property
    def refused(self) -> Refused | None:
        if survivors := tuple(
            one.path for one in self.unloadable if one.found.kind.fate is Fate.SURVIVOR
        ):
            return Refused(
                "a survivor does not load, and no migration maps it",
                survivors,
                "the landed code owes one (`maintenance/migrate.py::MIGRATIONS`)",
            )
        if self.reset is not None and self.reset.refusal is not None:
            return Refused(self.reset.refusal, ())
        if self.unknown:
            return Refused(
                "a file no row names is in the way",
                self.unknown,
                "move it out of the data root; one this code writes owes a row in "
                "`data_root.py::KINDS`",
            )
        if self.unloadable:
            return Refused(
                "a live run's file does not load",
                tuple(one.path for one in self.unloadable),
                "pause or stop it first",
            )
        return None

    @property
    def refusal(self) -> str | None:
        refused = self.refused
        if refused is None:
            return None
        return f"{refused.why}: {refused.remedy}" if refused.remedy else refused.why

    @property
    def said(self) -> tuple[str, ...]:
        """What applying it writes, drops and keeps, a line each: `land_at_entry` logs the applied
        landing's, and the stamp keeps them."""
        return (
            *(f"migrate {one.said}" for one in self.staged),
            *(f"drop {one.said}" for one in (*self.caches, *self.runs)),
            *(f"delete {one.why}, its spend banked" for one in self.torn),
            *(f"kept {one.why}" for one in self.kept),
            *(() if self.reset is None else (f"reset {len(self.reset.drops):,} run file(s)",)),
        )

    @property
    def tally(self) -> str:
        """Its lines counted by what each did to which kind: one line, however large the landing."""
        dropped = Counter(one.found.kind.name for one in (*self.caches, *self.runs))
        return ", ".join(
            (
                *((f"migrated {len(self.staged)} file(s)",) if self.staged else ()),
                *(f"dropped {n} {name}" for name, n in sorted(dropped.items())),
                *((f"deleted {len(self.torn)} torn mint(s)",) if self.torn else ()),
                *((f"kept {len(self.kept)} taken or minted since the plan",) if self.kept else ()),
                *(() if self.reset is None else (f"reset {len(self.reset.drops):,} run file(s)",)),
            )
        )


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
    # A job file that does not load refuses the landing as a survivor: no run is asked about then.
    jobs = (
        ()
        if survivor_refuses
        else live_jobs(
            JobRegistry.as_staged(
                jobs_dir(projects_root), {p: one.data for p, one in staged.items()}
            )
        )
    )
    runs: dict[Path, Unloadable] = {}
    unloadable: list[Unloadable] = []
    for path, why in why_at.items():
        found = found_at[path]
        if why is None or found is None or found.kind.fate is not Fate.RUN:
            continue
        unit = data_root / run_unit(
            PurePosixPath(path.relative_to(data_root).as_posix()), found.kind
        )
        if holder_in_reach(jobs, (unit,), _locks(data_root, unit)) is not None:
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
    for unit, found in manifestless.items():
        why = f"{unit}: a mint torn before its manifest"
        # Any run in its workspace may be that mint in flight, whatever cycle it names.
        if (
            holder := holder_in_reach(jobs, (unit.parent.parent,), _locks(data_root, unit))
        ) is not None:
            unloadable.append(Unloadable(unit, found, f"{why}, while {holder} holds its workspace"))
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
        kept=(),
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


def _locks(data_root: Path, unit: Path) -> list[Path]:
    """The producer locks under the run *unit* as it stands now: a producer holding one owns the
    run's files, never the landing's to drop."""
    return [
        path
        for path, _st in (iter_files(unit) if unit.is_dir() else ())
        if (found := classify(PurePosixPath(path.relative_to(data_root).as_posix()))) is not None
        and found.kind in PRODUCER_LOCKS
    ]


def _refusal(data_root: Path, refused: Refused) -> ConflictError:
    named = [path.relative_to(data_root).as_posix() for path in refused.paths[:_NAMED]]
    more = len(refused.paths) - len(named)
    said = f" ({', '.join(named)}{f', and {more} more' if more else ''})" if named else ""
    remedy = f": {refused.remedy}" if refused.remedy else ""
    return ConflictError(
        f"refusing to land the data root {data_root}, having written nothing: {refused.why}"
        f"{said}{remedy}. `land-data --dry-run` lists all that refuses it.",
        code="data_root_unlanded",
        details={"paths": named, "count": len(refused.paths)},
    )


class LandedStamp(FrozenModel):
    """The root's last landing: the record schema it landed under (``None`` where it kept what
    moved since its plan, so the next entry lands it again), when, and what it wrote, dropped and
    kept (`Landing.said`)."""

    key: str | None
    at: str
    said: tuple[str, ...]


def apply_landing(projects_root: Path, landing: Landing) -> Landing:
    """A refused landing raises before a write, and so does a reset's own refusal, asked again.
    Every wipe asks again at its act, under the admission gate: a run's files a run or a pass took
    since the plan are kept, and so is a torn mint that wrote its manifest since. Returns the landing as
    applied, and stamps the root with what it did (`Landing.said`)."""
    if (refused := landing.refused) is not None:
        raise _refusal(projects_root.parent, refused)
    data_root = projects_root.parent
    # ONE registry: its gate is reentrant per instance, and every guard below retakes it.
    registry = JobRegistry.as_staged(
        jobs_dir(projects_root), {one.path: one.data for one in landing.staged}
    )
    runs: list[Unloadable] = []
    torn: list[Unloadable] = []
    kept: list[Unloadable] = []
    with registry.admission_gate() if landing.reset is None else resetting(landing.reset, registry):
        for one in landing.staged:
            write_bytes(one.path, one.data)
        for cache in landing.caches:
            unlink_robust(cache.path)
        jobs = live_jobs(registry)
        for run in landing.runs:
            if (
                held := holder_in_reach(jobs, (run.path,), _locks(data_root, run.path))
            ) is not None:
                kept.append(run._replace(why=f"{run.path}: {held} holds it"))
                continue
            if run.path.is_dir():
                rmtree_robust(run.path)
            else:
                unlink_robust(run.path)
            runs.append(run)
        for mint in landing.torn:
            # Every hold is admitted on its campaign read under the gate (a handoff re-claims under
            # its parent's live claim), so only a mint that wrote its manifest since the plan can be
            # held: it is no torn one. The plan's own question, so an unreadable manifest never raises.
            if CampaignLayout(mint.path).manifest.is_file():
                kept.append(mint._replace(why=f"{mint.path}: minted since the plan"))
                continue
            stores = tenant_stores(projects_root, WorkspaceDir(mint.path.parent.parent))
            with destroying(stores, registry, mint.path.name):
                stores.campaigns.delete_campaign(
                    mint.path.name, keep_results=False, changed_at=utcnow_iso()
                )
            torn.append(mint)
    applied = landing._replace(runs=tuple(runs), torn=tuple(torn), kept=tuple(kept))
    stamp = LandedStamp(key=None if kept else landing_key(), at=utcnow_iso(), said=applied.said)
    write_text(_stamp(projects_root), stamp.model_dump_json())
    return applied


@functools.cache
def landing_key() -> str:
    """Every row's patterns, fate and shape, and every migration: a reshape of any lands again."""
    rows = {kind.name: row_schema(kind) for kind in KINDS}
    return stable_hash({"rows": rows, "migrations": sorted(MIGRATIONS)}, length=16)


def _stamp(projects_root: Path) -> Path:
    return jobs_dir(projects_root) / LANDED_SCHEMA_FILE


def last_landing(projects_root: Path) -> LandedStamp | None:
    """``None`` where the root never landed, or under a stamp this build cannot read, which lands
    it again."""
    raw = read_text_optional(_stamp(projects_root))
    try:
        return LandedStamp.model_validate_json(raw) if raw.strip() else None
    except ValidationError:
        return None


def holding_landing(projects_root: Path) -> AbstractContextManager[None]:
    """The one hold a landing is planned and applied under, at every door that lands."""
    return write_lock(_stamp(projects_root))


def _landed(projects_root: Path) -> bool:
    return (stamp := last_landing(projects_root)) is not None and stamp.key == landing_key()


def land_at_entry(*, every_treatment: bool) -> None:
    """Every process's first step, before it reads the data root: its registries complete, then
    the root lands under this code's record schema, once per schema. What it wrote and dropped is
    said here, a line each on the process's log and one tally to the operator, for every entry
    alike; the stamp keeps the lines. A landing that refuses raises, having written nothing."""
    complete_registries(every_treatment=every_treatment)
    projects_root = default_projects_root()
    if _landed(projects_root):
        return
    with holding_landing(projects_root):
        if _landed(projects_root):
            return
        landing = apply_landing(projects_root, plan_landing(projects_root, reset=False))
    for one in landing.said:
        logger.warning("landed: %s", one)
    tell_landed(landing.tally)
