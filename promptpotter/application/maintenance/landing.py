"""A landing's one write to the data root, under the code it lands: every migration staged, every
file proven as it would then stand, and all of it written only where nothing refuses. Nothing may
roll the code back once it has written: the old code would run on what it wrote."""

from __future__ import annotations

import contextlib
import functools
import inspect
import math
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from promptpotter import connectors
from promptpotter.application import health, optimizers
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
from promptpotter.application.optimizer_manifest import (
    bench_choice,
    checkin_manifest,
    select_optimizer,
)
from promptpotter.application.optimizers.nodes import NODE_MEMBERS
from promptpotter.application.runner.inner import spawn
from promptpotter.application.scoring.evaluators import connector_name_refusals
from promptpotter.application.scoring.judges import registry as judge_registry
from promptpotter.config.paths import default_projects_root, jobs_dir, user_data_root
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.job import JOB_ADAPTER
from promptpotter.domain.kinds import KindTable
from promptpotter.domain.optimizer_state import STATE_KINDS
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure import projections
from promptpotter.infrastructure.store.data_root import (
    CHECKIN_SHADOW,
    KINDS,
    LANDED_SCHEMA_FILE,
    LIVE_LOCKS,
    LOAD_ERRORS,
    OPTIMIZER_SHADOW,
    Fate,
    FoldedLines,
    Found,
    Parsed,
    Staged,
    classify,
    drop_unloadable_rows,
    row_schema,
    run_owner,
    run_unit,
    unloadable_rows,
    unloadable_why,
)
from promptpotter.infrastructure.store.io import (
    CAMPAIGNS_DIR,
    INNER_SANDBOXES_DIR,
    iter_files,
    read_text_optional,
    rmtree_robust,
    take_lock,
    unlink_robust,
    write_bytes,
    write_lock_name,
    write_text,
)
from promptpotter.infrastructure.store.layout import CampaignLayout
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.infrastructure.store.stores import (
    DEFINITION_SWAP_DIR,
    complete_definition_swap,
    tenant_stores,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, NextAct, PotterError
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.import_walk import import_closure
from promptpotter.shared.named_rows import walked


def complete_registries() -> None:
    """Every registry whose members import back up the stack, completed as this module imports:
    every entry imports it to land, so no process reads a ledger or a member before. The recursion's
    connector is the application's own built-in, registered here; the roster gains the readers no
    run imports, every projection and the health probes' standing."""
    connectors.CONNECTORS.complete((spawn.__name__, spawn.CONNECTOR))
    for name, connector in connectors.CONNECTORS.table.items():
        if shadowed := connector_name_refusals(connector.cell_terms, connector.reported_keys):
            where = f"connector {name!r} [{connectors.CONNECTORS.origins[name]}]"
            raise RuntimeError(f"{where}: {'; '.join(shadowed)}.")
    judge_registry.JUDGES.complete()
    optimizers.MANIFESTS.complete()
    for member in NODE_MEMBERS.table.values():
        member.complete()
    STATE_KINDS.complete()
    walked(projections)
    LedgerIndex.readers(*health.LEDGER_FOLDS)
    LedgerIndex.check_every_arm_read()
    for connector in connectors.CONNECTORS.table.values():
        if connector.completion_check is not None:
            connector.completion_check()


def check_treatment(name: str) -> None:
    """An optimizer as every launch reads it, an install shadow included; its source digest raises
    on a prompt-shaping helper nothing hashes."""
    select_optimizer(bench_choice(name)).treatment(frozenset(Presumption), PackShelf(account=None))


def check_every_treatment() -> None:
    """Every optimizer, and the check-in as every check-in and mint reads it, an install shadow
    included: a process serving any campaign reads them all at entry."""
    for name in optimizers.MANIFESTS.table:
        check_treatment(name)
    checkin_manifest()


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str

    @property
    def said(self) -> str:
        return f"{self.found.kind.fate.value} {self.found.kind.name} {self.path}: {self.why}"


class RowDrops(NamedTuple):
    """A line cache's rows its reader refuses, counted by why (`data_root.py::Kind.drops_rows`)."""

    path: Path
    found: Found
    why: Counter[str]

    @property
    def said(self) -> str:
        kind = self.found.kind
        whys = "; ".join(f"{n} {why}" for why, n in self.why.most_common())
        return f"{self.why.total()} row(s) of {kind.fate.value} {kind.name} {self.path}: {whys}"


class LandingRefusal(NamedTuple):
    """What refuses a landing, the paths that do and its remedy."""

    why: str
    paths: tuple[Path, ...]
    remedy: str = ""


# A refusal names its first few paths; its act, a dry run, lists every one.
_NAMED = 3


class Landing(NamedTuple):
    staged: tuple[Staged, ...]
    # Asked with `--reset`: every account's run data, whether it loads or not.
    reset: ResetPlan | None
    # What the landing drops unasked: each cache file that does not load, each line cache's rows
    # that do not, and each run that does not, its campaign or draft whole where the file is one of
    # its records (`data_root.run_unit`).
    caches: tuple[Unloadable, ...]
    rows: tuple[RowDrops, ...]
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
    # A definition swap committed and not yet laid (`stores.py::complete_definition_swap`).
    swaps: tuple[Path, ...]
    # The package version that last landed the root, where it is newer than this code's.
    newer: str | None

    @property
    def refused(self) -> LandingRefusal | None:
        if self.newer is not None:
            return LandingRefusal(f"v{self.newer} landed it, and this code is v{APP_VERSION}", ())
        survivors = [one for one in self.unloadable if one.found.kind.fate is Fate.SURVIVOR]
        if migrated := tuple(
            one.path for one in survivors if one.found.kind.shape is not JOB_ADAPTER
        ):
            return LandingRefusal(
                "a survivor does not load, and no migration maps it",
                migrated,
                "the landed code owes one (`maintenance/migrate.py::MIGRATIONS`)",
            )
        if survivors:
            # A job file is owed no migration: the code that wrote it is the code its run ends on.
            return LandingRefusal(
                "a job file does not load",
                tuple(one.path for one in survivors),
                "let its run end on the code that wrote it, then move the file out of the jobs dir",
            )
        if self.reset is not None and self.reset.refusal is not None:
            return LandingRefusal(self.reset.refusal, ())
        if self.unknown:
            return LandingRefusal(
                "a file no row names is in the way",
                self.unknown,
                "move it out of the data root; one this code writes owes a row in "
                "`data_root.py::KINDS`",
            )
        if self.unloadable:
            return LandingRefusal(
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
            *(f"complete the definition swap {swap}" for swap in self.swaps),
            *(f"drop {one.said}" for one in self.caches),
            *(f"drop {one.said}" for one in self.rows),
            *(f"drop {one.said}" for one in self.runs),
            *(f"delete {one.why}, its spend banked" for one in self.torn),
            *(f"kept {one.why}" for one in self.kept),
            *(() if self.reset is None else (f"reset {len(self.reset.drops):,} run file(s)",)),
        )

    @property
    def tally(self) -> str:
        """Its lines counted by what each did to which kind: one line, however large the landing."""
        dropped = Counter(one.found.kind.name for one in (*self.caches, *self.runs))
        rows: Counter[tuple[str, str]] = Counter()
        for one in self.rows:
            rows.update({(one.found.kind.name, why): n for why, n in one.why.items()})
        return ", ".join(
            (
                *((f"migrated {len(self.staged)} file(s)",) if self.staged else ()),
                *(f"dropped {n} {name}" for name, n in sorted(dropped.items())),
                *(f"dropped {n} {name} row(s), {why}" for (name, why), n in rows.most_common()),
                *((f"deleted {len(self.torn)} torn mint(s)",) if self.torn else ()),
                *((f"kept {len(self.kept)} taken or minted since the plan",) if self.kept else ()),
                *(() if self.reset is None else (f"reset {len(self.reset.drops):,} run file(s)",)),
            )
        )


def _staged(projects_root: Path) -> dict[Path, Staged]:
    """Each migration, in `MIGRATIONS` order, reads the root as the ones before it staged it: a
    release jump lands every reshape of a survivor since the last release, composed."""
    staged: dict[Path, Staged] = {}
    for name, migration in MIGRATIONS.items():
        for one in migration(projects_root, {path: held.data for path, held in staged.items()}):
            earlier = staged.get(one.path)
            said = f"{name}: {one.said}"
            staged[one.path] = one._replace(
                said=said if earlier is None else f"{earlier.said}; {said}"
            )
    return staged


def plan_landing(projects_root: Path, *, reset: bool) -> Landing:
    """Writes nothing."""
    data_root = projects_root.parent
    staged = _staged(projects_root)
    found_at: dict[Path, Found | None] = {}
    why_at: dict[Path, str | None] = {}
    rows: list[RowDrops] = []
    for path in sorted({path for path, _st in iter_files(data_root)} | staged.keys()):
        found = found_at[path] = classify(PurePosixPath(path.relative_to(data_root).as_posix()))
        if found is not None and found.kind.drops_rows and not found.lock:
            if whys := unloadable_rows(path, found):
                rows.append(RowDrops(path, found, whys))
            why_at[path] = None
        elif found is not None and found.kind.fate is not Fate.RUN:
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
    # A run's directory is walked for its locks once, never once per file of it that fails.
    holding = functools.cache(lambda unit: holder_in_reach(jobs, (unit,), _locks(data_root, unit)))
    for path, why in why_at.items():
        found = found_at[path]
        if why is None or found is None or found.kind.fate is not Fate.RUN:
            continue
        unit = data_root / run_unit(
            PurePosixPath(path.relative_to(data_root).as_posix()), found.kind
        )
        if holding(unit) is not None:
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
            and owner.parts[-2] == CAMPAIGNS_DIR
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
    # By ancestry, never by pairing each file with each dropped unit: a landing that drops every
    # run over a real root otherwise holds every other entry for minutes.
    dropped = {*runs, *(one.path for one in torn)}
    for path, found in found_at.items():
        if path in gone or path in dropped or not dropped.isdisjoint(path.parents):
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
        tuple(one for one in rows if one.path not in gone and dropped.isdisjoint(one.path.parents)),
        tuple(runs.values()),
        tuple(torn),
        proven,
        tuple(unknown),
        tuple(unloadable),
        kept=(),
        swaps=tuple(sorted({p.parent for p in found_at if p.parent.name == DEFINITION_SWAP_DIR})),
        newer=_newer(projects_root),
    )


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _newer(projects_root: Path) -> str | None:
    """The version that landed the root, where this code's is older: its landing would drop what
    the newer code wrote. Read off the raw stamp, never `LandedStamp`, whose shape that newer
    release may have moved."""
    try:
        landed = _LandedVersion.model_validate_json(read_text_optional(_stamp(projects_root)))
    except ValidationError:
        return None
    return landed.version if _release(landed.version) > _release(APP_VERSION) else None


class _LandedVersion(FrozenModel):
    """Lax on purpose (``extra="ignore"``): the version alone, whatever else a newer stamp grew."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    version: str = Field(pattern=r"^\d+(\.\d+)*$")


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
            check_treatment(path.parent.name)
    except (PotterError, *LOAD_ERRORS) as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    return None


def _locks(data_root: Path, unit: Path) -> list[Path]:
    """The live locks under the run *unit* as it stands now: a writer holding one owns the run's
    files, never the landing's to drop."""
    return [
        path
        for path, _st in (iter_files(unit) if unit.is_dir() else ())
        if (found := classify(PurePosixPath(path.relative_to(data_root).as_posix()))) is not None
        and found.kind in LIVE_LOCKS
    ]


def _refusal(data_root: Path, refused: LandingRefusal) -> ConflictError:
    named = [path.relative_to(data_root).as_posix() for path in refused.paths[:_NAMED]]
    more = len(refused.paths) - len(named)
    said = f" ({', '.join(named)}{f', and {more} more' if more else ''})" if named else ""
    remedy = f": {refused.remedy}" if refused.remedy else ""
    return ConflictError(
        f"refusing to land the data root {data_root}, having written nothing: {refused.why}"
        f"{said}{remedy}.",
        code="data_root_unlanded",
        details={"paths": named, "count": len(refused.paths)},
        next_acts=(NextAct(field="dry_run", value="every path it refuses, listed"),),
    )


class LandedStamp(FrozenModel):
    """The root's last landing: the record schema it landed under (``None`` where it kept what
    moved since its plan, so the next entry lands it again), the package version that landed it
    (older code refuses to land the root again), when, and what it wrote, dropped and kept
    (`Landing.said`)."""

    key: str | None
    version: str
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
            # A staged file is a survivor's (`data_root.py::Kind.durable`).
            write_bytes(one.path, one.data, durable=True)
        for swap in landing.swaps:
            complete_definition_swap(swap)
        for cache in landing.caches:
            unlink_robust(cache.path)
        rows = [
            one._replace(why=whys)
            for one in landing.rows
            if (whys := drop_unloadable_rows(one.path, one.found.kind))
        ]
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
                stores.campaigns.delete_campaign(mint.path.name, keep_results=False)
            torn.append(mint)
    applied = landing._replace(
        rows=tuple(rows), runs=tuple(runs), torn=tuple(torn), kept=tuple(kept)
    )
    stamp = LandedStamp(
        key=None if kept else landing_key(), version=APP_VERSION, at=utcnow_iso(), said=applied.said
    )
    write_text(_stamp(projects_root), stamp.model_dump_json())
    return applied


def _ran(schema: object, modules: set[str]) -> None:
    """The module of every function and class a core schema calls: its validators and models."""
    if isinstance(schema, Mapping):
        for value in schema.values():
            _ran(value, modules)
    elif isinstance(schema, list | tuple):
        for value in schema:
            _ran(value, modules)
    elif callable(schema):
        modules.add(inspect.unwrap(getattr(schema, "func", schema)).__module__)


def loading_modules() -> dict[str, str]:
    """Every package module whose code a load runs, less its prose: the import closure of each row's reader and
    the validators and models its shape builds, and of every migration. A load dispatches by name
    through a `KindTable` alone (a ledger line's record kind, a state kind a record nests), so
    every table's every kind is walked: the importlib loads that registered them, never."""
    modules: set[str] = set()
    for kind in KINDS:
        if isinstance(kind.shape, TypeAdapter):
            _ran(kind.shape.core_schema, modules)
        elif isinstance(kind.shape, Parsed | FoldedLines):
            # A parsed or a folded row's reader is its shape's first field.
            _ran(kind.shape[0], modules)
            if not isinstance(kind.shape.model, KindTable):
                _ran(TypeAdapter(kind.shape.model).core_schema, modules)
    for table in KindTable.every:
        _ran([TypeAdapter(one).core_schema for one in table.registered().values()], modules)
    _ran(list(MIGRATIONS.values()), modules)
    return import_closure(modules)


@functools.cache
def landing_key() -> str:
    """Every row's patterns, fate and shape, and the code of every module a load runs, less its
    prose: a helper a validator calls changes what loads as surely as a field does."""
    rows = {kind.name: row_schema(kind) for kind in KINDS}
    return stable_hash({"rows": rows, "code": loading_modules()}, length=16)


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


@contextlib.contextmanager
def holding_landing(projects_root: Path) -> Iterator[None]:
    """The one hold a landing is planned and applied under, at every door that lands: held for the
    whole landing, so an entry finding one in flight says so where its human reads (first-run
    setup's stderr) and waits for it, never a write lock's traceback."""
    gate = _stamp(projects_root).with_name(write_lock_name(LANDED_SCHEMA_FILE))
    if (lock := take_lock(gate, wait_s=0)) is None:
        print(
            f"waiting on the data root landing in flight over {projects_root.parent} …",
            file=sys.stderr,
            flush=True,
        )
        # Unbounded: the kernel drops an OS lock with its holder, so only a live landing holds it.
        lock = take_lock(gate, wait_s=math.inf)
    assert lock is not None, "an unbounded wait ends holding"
    try:
        yield
    finally:
        lock.release()


def _landed(projects_root: Path) -> bool:
    return (stamp := last_landing(projects_root)) is not None and stamp.key == landing_key()


def land_at_entry(*, every_treatment: bool) -> None:
    """Every process's first step, before it reads the data root (its registries completed as this
    module imported; *every_treatment* reads each one, for a process serving any campaign): the
    root lands under this code's record schema, once per schema. What it wrote and dropped is
    said here as one tally to the operator, for every entry alike; the stamp keeps its lines. A landing that refuses raises, having written nothing."""
    if every_treatment:
        check_every_treatment()
    projects_root = default_projects_root()
    if _landed(projects_root):
        return
    with holding_landing(projects_root):
        if _landed(projects_root):
            return
        landing = apply_landing(projects_root, plan_landing(projects_root, reset=False))
    tell_landed(landing.tally)


complete_registries()
