"""A landing's one write to the data root, under the code it lands: every migration staged, every
file proven as it would then stand, and all of it written only where nothing refuses. Nothing may
roll the code back once it has written: the old code would run on what it wrote."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.maintenance.migrate import MIGRATIONS
from promptpotter.application.maintenance.reset import ResetPlan, plan_reset, resetting
from promptpotter.infrastructure.store.data_root import (
    Fate,
    Found,
    Staged,
    classify,
    unloadable_why,
)
from promptpotter.infrastructure.store.io import iter_files, unlink_robust, write_bytes
from promptpotter.shared.errors import ConflictError


class Unloadable(NamedTuple):
    path: Path
    found: Found
    why: str


class Landing(NamedTuple):
    staged: tuple[Staged, ...]
    # Asked with `--reset`: every account's run data and every file no row names.
    reset: ResetPlan | None
    # Each cache file that does not load, which the landing drops.
    caches: tuple[Unloadable, ...]
    proven: int
    # What refuses the landing: a file no row names, and a survivor or run file that does not load.
    unknown: tuple[Path, ...]
    unloadable: tuple[Unloadable, ...]

    @property
    def refusal(self) -> str | None:
        if any(one.found.kind.fate is Fate.SURVIVOR for one in self.unloadable):
            return "a survivor does not load, and its reshape ships no migration"
        if self.reset is not None and self.reset.refusal is not None:
            return self.reset.refusal
        if self.reset is None and (self.unknown or self.unloadable):
            return "run data or a file no row names is in the way — `--reset` drops both"
        if self.unknown or self.unloadable:
            return "a file the reset keeps does not load"
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
    swept = plan_reset(projects_root, tenant_id=None) if reset else None
    gone = set() if swept is None else {drop.path for drop in swept.drops}
    caches: list[Unloadable] = []
    unknown: list[Path] = []
    unloadable: list[Unloadable] = []
    proven = 0
    for path in sorted({path for path, _st in iter_files(data_root)} | staged.keys()):
        if path in gone:
            continue
        found = classify(PurePosixPath(path.relative_to(data_root).as_posix()))
        if found is None:
            unknown.append(path)
            continue
        held = staged.get(path)
        why = unloadable_why(path, found, staged=None if held is None else held.data)
        if why is None:
            proven += 1
        elif found.kind.fate is Fate.CACHE:
            caches.append(Unloadable(path, found, why))
        else:
            unloadable.append(Unloadable(path, found, why))
    return Landing(
        tuple(staged.values()), swept, tuple(caches), proven, tuple(unknown), tuple(unloadable)
    )


def apply_landing(landing: Landing) -> None:
    """A refused landing raises before a write, and so does a reset's own refusal, asked again."""
    if landing.refused:
        raise ConflictError("refusing to land: run `land-data --dry-run` for what refuses it")
    with nullcontext() if landing.reset is None else resetting(landing.reset):
        for one in landing.staged:
            write_bytes(one.path, one.data)
        for cache in landing.caches:
            unlink_robust(cache.path)
