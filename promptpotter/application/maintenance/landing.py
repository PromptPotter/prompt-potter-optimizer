"""A landing's one write to the data root, under the code it lands: every migration staged, every
file proven as it would then stand, and all of it written only where nothing refuses. Nothing may
roll the code back once it has written: the old code would run on what it wrote. `land-data` asks it
by name, and every process that reads the data root asks it first (`land_unlanded`)."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.maintenance.migrate import MIGRATIONS
from promptpotter.application.maintenance.reset import (
    Drop,
    ResetPlan,
    live_refusal,
    plan_reset,
    reset_lines,
    resetting,
)
from promptpotter.application.optimizer_manifest import installed_manifest
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import CODE_REVISION
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.data_root import (
    CHECKIN_SHADOW,
    LANDED_STAMP,
    LOAD_ERRORS,
    OPTIMIZER_SHADOW,
    Fate,
    Found,
    Staged,
    classify,
    unloadable_why,
)
from promptpotter.infrastructure.store.io import (
    iter_files,
    read_text_optional,
    unlink_robust,
    write_bytes,
    write_text,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ConflictError, PotterError

_SHADOWS = (OPTIMIZER_SHADOW, CHECKIN_SHADOW)


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str


class Landing(NamedTuple):
    projects_root: Path
    staged: tuple[Staged, ...]
    # Asked with `--reset`: every account's run data and every file no row names.
    reset: ResetPlan | None
    # Each cache and run file that does not load, which the landing drops unasked.
    drops: tuple[Unloadable, ...]
    proven: int
    # What refuses the landing: a file no row names, and a survivor that does not load.
    unknown: tuple[Path, ...]
    unloadable: tuple[Unloadable, ...]
    # Why a drop may not go now: a run is live where it would drop.
    in_use: str | None

    @property
    def refusal(self) -> str | None:
        if self.unloadable:
            return (
                "a survivor does not load, and no migration maps it: the landed code owes one "
                "(`maintenance/migrate.py::MIGRATIONS`)"
            )
        if self.reset is not None and self.reset.refusal is not None:
            return self.reset.refusal
        if self.unknown:
            return (
                "a file no data-root row names is in the way: move it out of the data root, or "
                "`--reset` drops it with every account's run data"
            )
        return self.in_use

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


def _why(path: Path, found: Found, staged: Mapping[Path, Staged]) -> str | None:
    """Its shape, and an install shadow through the reader every run takes: a knob or roster fault
    its YAML types past refuses here, never first at the API's boot."""
    held = staged.get(path)
    if (why := unloadable_why(path, found, staged=None if held is None else held.data)) is not None:
        return why
    if found.lock or found.kind not in _SHADOWS:
        return None
    try:
        installed_manifest(path)
    except (PotterError, *LOAD_ERRORS) as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    return None


def _live_cycle(path: Path, data_root: Path) -> Path | None:
    """The cycle holding *path* whose producer is live, if any."""
    for parent in path.parents:
        if parent == data_root:
            return None
        if producer_lock.held(CycleLayout(parent).producer_lock):
            return parent
    return None


def _in_use(
    projects_root: Path, drops: list[Unloadable], staged: Mapping[Path, bytes]
) -> str | None:
    """`reset`'s guard over the run files a landing drops, and the producer of each one's cycle."""
    run = [one for one in drops if one.found.kind.fate is Fate.RUN]
    if not run:
        return None
    data_root = projects_root.parent
    for one in run:
        if (cycle := _live_cycle(one.path, data_root)) is not None:
            return f"{cycle} has a live producer — pause or stop it first"
    held = tuple(Drop(one.path, 0, one.found.kind) for one in run)
    return live_refusal(projects_root, (data_root,), held, staged)


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
            why_at[path] = _why(path, found, staged)
    # A survivor that does not load refuses the landing whole; a reset is planned over survivors
    # that load, read as staged.
    survivor_refuses = any(
        why is not None
        and (found := found_at[path]) is not None
        and found.kind.fate is Fate.SURVIVOR
        for path, why in why_at.items()
    )
    as_staged = {p: one.data for p, one in staged.items()}
    swept = (
        plan_reset(projects_root, tenant_id=None, staged=as_staged)
        if reset and not survivor_refuses
        else None
    )
    gone = set() if swept is None else {drop.path for drop in swept.drops}
    drops: list[Unloadable] = []
    unknown: list[Path] = []
    unloadable: list[Unloadable] = []
    proven = 0
    for path, found in found_at.items():
        if path in gone:
            continue
        if found is None:
            unknown.append(path)
            continue
        if path not in why_at:
            why_at[path] = _why(path, found, staged)
        if (why := why_at[path]) is None:
            proven += 1
        elif found.kind.fate is Fate.SURVIVOR:
            unloadable.append(Unloadable(path, found, why))
        else:
            drops.append(Unloadable(path, found, why))
    in_use = None if survivor_refuses else _in_use(projects_root, drops, as_staged)
    return Landing(
        projects_root,
        tuple(staged.values()),
        swept,
        tuple(drops),
        proven,
        tuple(unknown),
        tuple(unloadable),
        in_use,
    )


def apply_landing(landing: Landing) -> None:
    """A refused landing raises before a write, and so does a reset's own refusal, asked again.
    A landing written stamps the data root with this code."""
    if landing.refused:
        raise ConflictError("refusing to land: run `land-data --dry-run` for what refuses it")
    with nullcontext() if landing.reset is None else resetting(landing.reset):
        for one in landing.staged:
            write_bytes(one.path, one.data)
        for drop in landing.drops:
            unlink_robust(drop.path)
    write_text(landing.projects_root.parent / LANDED_STAMP, CODE_REVISION)


def land_unlanded() -> Landing | None:
    """The landing `land-data` writes, wherever this code meets the process's data root another code
    landed last, its registries completed first as every reader of a ledger line needs them;
    ``None`` where this code landed it. A refusal raises, having written nothing."""
    complete_registries(every_treatment=False)
    projects_root = default_projects_root()
    if read_text_optional(projects_root.parent / LANDED_STAMP).strip() == CODE_REVISION:
        return None
    landing = plan_landing(projects_root, reset=False)
    if landing.refused:
        raise ConflictError(
            f"This data root was last landed by other code, and does not land under this one: "
            f"{landing.refusal}. `python -m promptpotter land-data --dry-run` lists what "
            "refuses it; nothing was written.",
            code="data_root_unlanded",
        )
    apply_landing(landing)
    return landing


def _unloadable(one: Unloadable) -> str:
    return f"{one.found.kind.fate.value} {one.found.kind.name} {one.path}: {one.why}"


def landing_lines(landing: Landing, *, written: bool) -> list[str]:
    lines = [f"migrate {one.said}" for one in landing.staged]
    lines += [f"drop {_unloadable(one)}" for one in landing.drops]
    if landing.reset is not None:
        lines.append(reset_lines(landing.reset))
    lines += [f"unknown {path}" for path in landing.unknown]
    lines += [f"unloadable {_unloadable(one)}" for one in landing.unloadable]
    if landing.refusal is None:
        lines.append(f"{'landed' if written else 'would land'}: proven {landing.proven:,} file(s)")
    else:
        lines.append(f"refused, nothing written: {landing.refusal}")
    return lines


def landing_data(landing: Landing, *, written: bool) -> dict[str, object]:
    return {
        "written": written,
        "refusal": landing.refusal,
        "proven": landing.proven,
        "migrated": [one.said for one in landing.staged],
        "dropped": [_unloadable(one) for one in landing.drops],
        "reset": None if landing.reset is None else len(landing.reset.drops),
        "unknown": [str(path) for path in landing.unknown],
        "unloadable": [_unloadable(one) for one in landing.unloadable],
    }
