from __future__ import annotations

import dataclasses
import json
import pathlib
import types
from collections import Counter
from collections.abc import Callable, Iterator
from functools import partial, reduce
from typing import Annotated, Any, Literal, NamedTuple, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from promptpotter.application.datasets.authored import campaign_file_block
from promptpotter.application.jobs.registry import Job
from promptpotter.application.maintenance.archive_maintenance import (
    archive_writers,
    iter_cycle_ledgers,
    workspace_trees,
)
from promptpotter.config.paths import BLOCK_PACKS_DIR, default_projects_root, jobs_dir
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.campaign_config import CampaignConfig, merge_config_layers
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.diagnostic_run import DiagnosticRunRecord
from promptpotter.domain.optimizer_state import StatePart
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.prompt_block import BlockPack
from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.sample import ArchiveConfig, FiledAnswer
from promptpotter.domain.scoring import GradedCell
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.projections.cycle_index import (
    read_cycle_index,
    write_cycle_index,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.archive_queries import read_sample_fold
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import definition_files
from promptpotter.infrastructure.store.io import (
    HeldLock,
    probe_lock,
    read_json_optional,
    rewrite_lines,
    take_lock,
    unlink_robust,
    write_json,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    archive_homes,
    inner_sandboxes_dir,
    read_sandbox_owner,
)
from promptpotter.infrastructure.store.measurement_archive import (
    ArchiveFile,
    CellClaim,
    CellRewrite,
    MeasurementArchive,
    MovedFields,
    answer_line,
    indexed_entry,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import LedgerUnreadableError, ManifestUnreadableError


def _whole(_path: pathlib.Path) -> dict[str, Any]:
    return {}


def _definition_ancestors(path: pathlib.Path) -> dict[str, Any]:
    """A definition layer is a partial: it validates laid over the layers above it."""
    above = definition_files(path.parent)[:-1]
    return reduce(merge_config_layers, (campaign_file_block(p) for p in above), {})


class _Surface(NamedTuple):
    title: str
    workspace_globs: tuple[str, ...]
    # Empty means the whole document IS the record.
    key_path: tuple[str, ...]
    model_cls: type[BaseModel]
    # What the pruned record is laid over before it validates.
    laid_over: Callable[[pathlib.Path], dict[str, Any]] = _whole


# No round close: pruning cannot restore a rename.
_SURFACES: tuple[_Surface, ...] = (
    _Surface(
        title="Campaign manifests (campaigns/*/campaign.json)",
        workspace_globs=("*/campaigns/*/campaign.json",),
        key_path=(),
        model_cls=Campaign,
    ),
    _Surface(
        # The tenant tier only: install content is source, edited as source and never by a verb.
        title="Your dataset definitions (datasets/**/dataset.yaml::campaign)",
        workspace_globs=("*/datasets/**/dataset.yaml",),
        key_path=("campaign",),
        model_cls=CampaignConfig,
        laid_over=_definition_ancestors,
    ),
    _Surface(
        title="User records (user.json)",
        workspace_globs=("*/user.json",),
        key_path=(),
        model_cls=User,
    ),
    _Surface(
        title="Diagnostic runs (diagnostics/runs/*.json)",
        workspace_globs=("*/diagnostics/runs/*.json",),
        key_path=(),
        model_cls=DiagnosticRunRecord,
    ),
    _Surface(
        # The account tier only: the install's packs are placed by hand, so fixed by hand.
        title=f"Your block packs ({BLOCK_PACKS_DIR}/*/pack.json)",
        workspace_globs=(f"*/{BLOCK_PACKS_DIR}/*/pack.json",),
        key_path=(),
        model_cls=BlockPack,
    ),
)


def _model_arms(ann: object) -> list[type[BaseModel]]:
    if get_origin(ann) is Annotated:
        return _model_arms(get_args(ann)[0])
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return [ann]
    if get_origin(ann) in (Union, types.UnionType):
        return [m for arg in get_args(ann) for m in _model_arms(arg)]
    return []


def _tags_match(arm: type[BaseModel], value: dict[str, object]) -> bool:
    return all(
        value[key] in get_args(field.annotation)
        for key, field in arm.model_fields.items()
        if get_origin(field.annotation) is Literal and key in value
    )


def _nested_model(ann: object, value: dict[str, object]) -> type[BaseModel] | None:
    """The arm the VALUE is: a union pruned against its first arm strips every other arm's keys."""
    arms = [arm for arm in _model_arms(ann) if _tags_match(arm, value)]
    if len(arms) == 1:
        return arms[0]
    # Ambiguous: only an arm declaring every key may prune, and then it drops nothing.
    return next((arm for arm in arms if value.keys() <= arm.model_fields.keys()), None)


def _holds_parts(ann: object) -> bool:
    if get_origin(ann) is not dict:
        return False
    held = get_args(ann)[1]
    base = get_args(held)[0] if get_origin(held) is Annotated else held
    return isinstance(base, type) and issubclass(base, StatePart)


def _prune_parts(
    raw: dict[str, Any], prefix: tuple[str, ...]
) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Each part by its own kind's model; a kind no installed package registers raises, never pruned away."""
    pruned: dict[str, Any] = {}
    dropped: list[tuple[str, Any]] = []
    for kind, value in raw.items():
        pruned[kind], gone = _prune_to_schema(value, StatePart.of_kind(kind), (*prefix, kind))
        dropped.extend(gone)
    return pruned, dropped


def _member(ann: object) -> object | None:
    """What a list's item or a mapping's value is declared as, through `Annotated` and `| None`."""
    origin = get_origin(ann)
    if origin is Annotated:
        return _member(get_args(ann)[0])
    if origin in (Union, types.UnionType):
        return next((m for arg in get_args(ann) if (m := _member(arg)) is not None), None)
    args: tuple[object, ...] = get_args(ann)
    if origin in (list, frozenset, set) or (origin is tuple and args[1:] == (...,)):
        return args[0]
    return args[1] if origin is dict else None


def _prune_value(
    ann: object, value: Any, path: tuple[str, ...]
) -> tuple[Any, list[tuple[str, Any]]]:
    """A model's undeclared keys go; a list's or a mapping's members are each pruned at their holder's path."""
    if isinstance(value, dict) and _holds_parts(ann):
        return _prune_parts(value, path)
    if isinstance(value, dict) and (nested := _nested_model(ann, value)) is not None:
        return _prune_to_schema(value, nested, path)
    member = _member(ann)
    if member is None or not isinstance(value, list | dict):
        return value, []
    keyed = value.items() if isinstance(value, dict) else enumerate(value)
    held = {k: _prune_value(member, v, path) for k, v in keyed}
    gone = [d for _, sub in held.values() for d in sub]
    if isinstance(value, dict):
        return {k: v for k, (v, _) in held.items()}, gone
    return [v for v, _ in held.values()], gone


def _prune_to_schema(
    raw: dict[str, Any], model_cls: type[BaseModel], prefix: tuple[str, ...] = ()
) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    pruned: dict[str, Any] = {}
    dropped: list[tuple[str, Any]] = []
    for key, value in raw.items():
        path = (*prefix, key)
        field = model_cls.model_fields.get(key)
        if field is None:
            dropped.append((".".join(path), value))
            continue
        pruned[key], gone = _prune_value(field.annotation, value, path)
        dropped.extend(gone)
    return pruned, dropped


class _Tally:
    def __init__(self) -> None:
        self.rewritten = self.unchanged = self.empty = self.skipped = self.failed = 0
        self.removed = 0
        self.gone: Counter[str] = Counter()

    def report(self, title: str, *, apply: bool) -> None:
        print(f"\n{title}")
        print(f"  {'pruned' if apply else 'would be pruned':>18}: {self.rewritten}")
        print(f"  {'already current':>18}: {self.unchanged}")
        for label, n in (
            ("no config", self.empty),
            ("skipped", self.skipped),
            ("still invalid", self.failed),
            ("removed" if apply else "would be removed", self.removed),
        ):
            if n:
                print(f"  {label:>18}: {n}")


def _process(path: pathlib.Path, surface: _Surface, *, apply: bool, tally: _Tally) -> None:
    is_yaml = path.suffix == ".yaml"
    try:
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text) if is_yaml else json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        tally.skipped += 1
        print(f"  SKIP  {path}: {type(exc).__name__}: {exc}")
        return

    holder: Any = doc
    for key in surface.key_path[:-1]:
        holder = holder.get(key)
        if not isinstance(holder, dict):
            tally.empty += 1
            return
    raw = holder.get(surface.key_path[-1]) if surface.key_path else doc
    if not raw:
        tally.empty += 1
        return

    pruned, dropped = _prune_to_schema(raw, surface.model_cls)
    try:
        surface.model_cls.model_validate(merge_config_layers(surface.laid_over(path), pruned))
    except ValidationError as exc:
        tally.failed += 1
        print(f"  FAIL  {path}: still invalid after pruning — {exc.error_count()} error(s)")
        for err in exc.errors():
            print(f"          {'.'.join(map(str, err['loc']))}: {err['type']}")
        return

    if not dropped:
        tally.unchanged += 1
        return

    for dotted, value in dropped:
        tally.gone[f"{dotted} = {value!r}"] += 1
    tally.rewritten += 1
    if apply:
        if surface.key_path:
            holder[surface.key_path[-1]] = pruned
        else:
            doc = pruned
        if is_yaml:
            write_yaml(path, doc)
        else:
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def prune_stored_documents(*, apply: bool) -> dict[str, int]:
    root = default_projects_root()
    print(f"Workspace: {root}")
    if not root.is_dir():
        print("  absent — nothing to prune.")
        return {"rewritten": 0, "failed": 0, "skipped": 0}

    # A sandbox is a projects root of its own, and its manifests load through the same models.
    roots = [root, *sorted(inner_sandboxes_dir(root).glob("*"))]
    tallies = [_Tally() for _ in _SURFACES]
    for surface, tally in zip(_SURFACES, tallies, strict=True):
        paths = {p for r in roots for g in surface.workspace_globs for p in r.glob(g)}
        for path in sorted(paths):
            _process(path, surface, apply=apply, tally=tally)

    for surface, tally in zip(_SURFACES, tallies, strict=True):
        tally.report(surface.title, apply=apply)

    gone: Counter[str] = Counter()
    for tally in tallies:
        gone += tally.gone
    if gone:
        print("\nDropped — knobs the engine no longer has, and the value each file held:")
        for entry, n in gone.most_common():
            print(f"  {n:4d}x  {entry}")

    rewritten = sum(t.rewritten for t in tallies)
    if not apply and rewritten:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "rewritten": rewritten,
        "failed": sum(t.failed for t in tallies),
        "skipped": sum(t.skipped for t in tallies),
    }


_LEDGER_ARMS: dict[str, type[BaseModel]] = {
    str(arm.model_fields["record_type"].default): arm for arm in get_args(get_args(CycleRecord)[0])
}


def _prune_record(rec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    # A line carrying a key its arm no longer declares refuses its whole cycle (`ledger_record`).
    arm = _LEDGER_ARMS.get(str(rec.get("record_type")))
    if arm is None:
        return rec, []
    pruned, dropped = _prune_to_schema(rec, arm)
    if not dropped:
        return rec, []
    try:
        arm.model_validate(pruned)
    except ValidationError:
        # A prune that leaves the line unreadable deleted a key the reader needs: keep the line.
        return rec, []
    return pruned, [dotted for dotted, _ in dropped]


def _compact_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    kind = rec.get("record_type")
    if kind == "sample_scored" and isinstance(result := rec.get("result"), dict):
        # The WRITER's own projection: a second stripper drifts, and each drift deletes a field.
        cell = GradedCell.from_wire(result)
        # Only the grade keys the record holds: a rewrite narrows a payload, it grades nothing.
        banked = {k: v for k, v in cell.grade.wire().items() if k in result}
        lean = {**cell.facts.ledger_wire(), **banked}
        return None if lean == result else rec | {"result": lean}
    return None


def _compacted(
    held: list[str], *, apply: bool
) -> tuple[list[str] | None, tuple[int, int, int, Counter[str]]]:
    before = after = rewritten = 0
    lines: list[str] = []
    gone: Counter[str] = Counter()
    for line in held:
        before += len(line)
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # An unparseable line is still a line: keep the offsets honest.
            lines.append(line)
            after += len(line)
            continue
        # Prune BEFORE projecting: a stale key on the record refuses the line to its reader.
        rec, dropped = _prune_record(rec)
        gone.update(dropped)
        out = _compact_record(rec)
        if out is None and not dropped:
            lines.append(line)
            after += len(line)
            continue
        out = out if out is not None else rec
        rewritten += 1
        new_line = json.dumps(out, separators=(",", ":"), default=str) + "\n"
        lines.append(new_line)
        after += len(new_line)
    return (lines if apply and rewritten else None), (before, after, rewritten, gone)


def _compact_one(path: pathlib.Path, *, apply: bool, gone: Counter[str]) -> tuple[int, int, int]:
    before, after, rewritten, dropped = rewrite_lines(
        path, lambda held: _compacted(held, apply=apply)
    )
    gone.update(dropped)
    return before, after, rewritten


def compact_cycle_ledgers(*, apply: bool) -> dict[str, int]:
    total_before = total_after = touched = 0
    skipped: Counter[RunPhase] = Counter()
    gone: Counter[str] = Counter()
    rows: list[tuple[int, str]] = []
    for ledger_path in iter_cycle_ledgers(default_projects_root()):
        cycle_dir = ledger_path.parent.parent
        run = derive_run_state(cycle_dir)
        # A live producer holds `_next_offset`; a paused cycle MUST be taken — resume skips stale lines.
        if not run.resumable:
            skipped[run.run_phase] += 1
            continue
        before, after, rewritten = _compact_one(ledger_path, apply=apply, gone=gone)
        total_before += before
        total_after += after
        if rewritten:
            touched += 1
            rows.append((before - after, cycle_dir.name))

    mb = 1024 * 1024
    verb = "compacted" if apply else "would compact"
    print(f"\nLedger compaction — {verb} {touched} cycle ledger(s)")
    for phase, n in sorted(skipped.items()):
        print(f"  {n:>6} skipped — {phase}")
    print(f"  {'before':>12}: {total_before / mb:8.2f} MB")
    print(f"  {'after':>12}: {total_after / mb:8.2f} MB")
    if total_before:
        pct = 100 * (total_before - total_after) / total_before
        print(f"  {'saved':>12}: {(total_before - total_after) / mb:8.2f} MB  ({pct:.1f}%)")
    for saved, name in sorted(rows, reverse=True)[:10]:
        print(f"  {saved / mb:8.2f} MB  {name}")
    if gone:
        print("\nDropped — record keys the engine no longer declares, each of which refused")
        print("its cycle to every reader:")
        for key, n in gone.most_common():
            print(f"  {n:4d}x  {key}")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "cycles": touched,
        "skipped_producing": sum(skipped.values()),
        "bytes_saved": total_before - total_after,
        "record_keys_dropped": sum(gone.values()),
    }


def reproject_cycle_indexes(*, apply: bool) -> dict[str, int]:
    cycle_dirs = sorted(p.parent.parent for p in iter_cycle_ledgers(default_projects_root()))
    touched = failed = 0
    for cycle_dir in cycle_dirs:
        try:
            index = read_cycle_index(cycle_dir, moment=None)
            if index is None:
                continue
            on_disk = read_json_optional(CycleLayout(cycle_dir).manifest)
            if on_disk == index.model_dump(mode="json"):
                continue
            if apply:
                write_cycle_index(cycle_dir)
            touched += 1
        except (OSError, ValueError, LedgerUnreadableError):
            failed += 1

    verb = "re-projected" if apply else "would re-project"
    print(f"\nCycle indexes — {verb} {touched} of {len(cycle_dirs)} cycle(s) from their ledgers")
    if failed:
        print(f"  {failed:>6} unreadable — a ledger chain or an index that does not read")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {"cycle_indexes": len(cycle_dirs), "cycle_indexes_reprojected": touched}


def prune_finished_jobs(*, apply: bool) -> dict[str, int]:
    """A job released before today has no reader (the quota and the refusals count today's only),
    and the registry validates every file it lists, so one left in an old shape 500s admission."""
    today = utcnow_iso()[:10]
    declared = {field.name for field in dataclasses.fields(Job)}
    finished = pruned = invalid = 0
    for path in sorted(jobs_dir(default_projects_root()).glob("*.json")):
        raw = read_json_optional(path)
        if raw and raw.get("released_at") and str(raw.get("created_at", ""))[:10] < today:
            finished += 1
            if apply:
                path.unlink()
            continue
        # A key the job no longer declares is dropped: its fact moved to a store of its own.
        kept = raw if not isinstance(raw, dict) else {k: v for k, v in raw.items() if k in declared}
        try:
            TypeAdapter(Job).validate_python(kept)
        except ValidationError as exc:
            invalid += 1
            print(f"  FAIL  {path}: {exc.error_count()} error(s) — this job blocks every admission")
            continue
        if kept != raw:
            pruned += 1
            if apply:
                write_json(path, kept)
    verb = "deleted" if apply else "would delete"
    print(
        f"\nJob records — {verb} {finished} released before today; "
        f"{'pruned' if apply else 'would prune'} {pruned} to the job's keys; {invalid} still invalid"
    )
    return {"jobs_finished": finished, "jobs_pruned": pruned, "jobs_invalid": invalid}


class _Pruned(NamedTuple):
    # ``None``: its model still refuses the pruned record, so nothing can key it.
    record: dict[str, Any] | None
    dropped: list[tuple[str, Any]]


def _pruned(raw: object, model_cls: type[BaseModel]) -> _Pruned:
    if not isinstance(raw, dict):
        return _Pruned(None, [])
    pruned, dropped = _prune_to_schema(raw, model_cls)
    try:
        model_cls.model_validate(pruned)
    except ValidationError:
        return _Pruned(None, dropped)
    return _Pruned(pruned, dropped)


class _ArchivePass:
    """One home in `_ARCHIVE_WALKS` order: a later kind's dependents name what an earlier one removed."""

    def __init__(self, archive: MeasurementArchive, *, apply: bool) -> None:
        self.archive = archive
        self.apply = apply
        self.tallies = {kind: _Tally() for kind in ArchiveFile}
        # The configurations a kept answer names: a config or fold row outside it describes nothing.
        self.named: set[str] = set()
        self.removed_configs: set[str] = set()
        self._configs: dict[str, _Pruned | None] = {}

    def config(self, key: str) -> _Pruned | None:
        """``None``: no file describes *key*, so its answers stand undescribed, as `reindex` counts them."""
        if key not in self._configs:
            try:
                raw = read_json_optional(self.archive.path(ArchiveFile.CONFIG, key))
                held = None if raw is None else _pruned(raw, ArchiveConfig)
            except ValueError:
                held = _Pruned(None, [])
            self._configs[key] = held
        return self._configs[key]

    def refused(self, config_key: str) -> bool:
        return (held := self.config(config_key)) is not None and held.record is None


def _walk_cold(run: _ArchivePass) -> None:
    archive, tally = run.archive, run.tallies[ArchiveFile.COLD]
    for key in archive.keys(ArchiveFile.COLD):
        try:
            archive.read_cold(key)
            held = archive.cell_path(key).exists()
        except (OSError, EOFError, ValueError, KeyError):
            held = False
        if held:
            tally.unchanged += 1
            continue
        tally.removed += 1
        if run.apply:
            unlink_robust(archive.cold_path(key))


class _CellsKept(NamedTuple):
    named: frozenset[str]
    current: int
    pruned: int
    removed: int


def _filed(line: str) -> tuple[dict[str, Any], FiledAnswer] | None:
    """The row and the answer its reader takes from it; ``None`` where no address can key it."""
    try:
        row = json.loads(line)
        answer = FiledAnswer.from_wire(row) if isinstance(row, dict) else None
    except (ValueError, TypeError, KeyError):
        return None
    return None if answer is None or answer.cell.answer is None else (row, answer)


def _restamped_cells(
    lines: list[str],
    cold: list[MovedFields],
    *,
    refused: Callable[[str], bool],
    apply: bool,
) -> tuple[CellRewrite | None, _CellsKept]:
    out: list[str] = []
    named: set[str] = set()
    kept: set[str] = set()
    pruned = removed = 0
    for line in lines:
        filed = _filed(line)
        if filed is None or refused(filed[1].config_key):
            removed += 1
            continue
        row, answer = filed
        named.add(answer.config_key)
        kept.add(answer.answer)
        if row.keys() <= answer.wire().keys():
            out.append(line)
        else:
            pruned += 1
            out.append(answer_line(answer))
    outcome = _CellsKept(frozenset(named), len(out) - pruned, pruned, removed)
    if not apply or not (pruned or removed):
        return None, outcome
    return CellRewrite(out, [m for m in cold if m.answer in kept]), outcome


def _walk_cells(run: _ArchivePass) -> None:
    archive, tally = run.archive, run.tallies[ArchiveFile.CELLS]
    transform = partial(_restamped_cells, refused=run.refused, apply=run.apply)
    for key in archive.keys(ArchiveFile.CELLS):
        kept = archive.rewrite_cell_file(key, transform)
        run.named |= kept.named
        tally.unchanged += kept.current
        tally.rewritten += kept.pruned
        tally.removed += kept.removed
        path = archive.cell_path(key)
        if run.apply and not (kept.current or kept.pruned) and path.stat().st_size == 0:
            unlink_robust(path)


def _walk_configs(run: _ArchivePass) -> None:
    archive, tally = run.archive, run.tallies[ArchiveFile.CONFIG]
    for key in archive.keys(ArchiveFile.CONFIG):
        path = archive.path(ArchiveFile.CONFIG, key)
        held = run.config(key)
        if held is None or held.record is None or key not in run.named:
            tally.removed += 1
            run.removed_configs.add(key)
            if run.apply:
                unlink_robust(path)
        elif held.dropped:
            tally.rewritten += 1
            tally.gone.update(f"{dotted} = {value!r}" for dotted, value in held.dropped)
            if run.apply:
                write_json(path, held.record)
        else:
            tally.unchanged += 1


def _walk_index(run: _ArchivePass) -> None:
    archive, tally = run.archive, run.tallies[ArchiveFile.INDEX]
    for row in iter_jsonl(archive.path(ArchiveFile.INDEX)):
        try:
            entry = indexed_entry(row)
        except ValidationError:
            tally.removed += 1
            continue
        if entry.config_key in run.removed_configs:
            tally.removed += 1
        else:
            tally.unchanged += 1
    if run.apply and (tally.removed or run.tallies[ArchiveFile.CELLS].removed):
        archive.reindex()


def _walk_sample_folds(run: _ArchivePass) -> None:
    archive, tally = run.archive, run.tallies[ArchiveFile.SAMPLE_FOLD]
    for key in archive.keys(ArchiveFile.SAMPLE_FOLD):
        path = archive.path(ArchiveFile.SAMPLE_FOLD, key)
        try:
            read_sample_fold(path)
        except (OSError, ValueError, TypeError, KeyError):
            # A cache: `SampleIndex.refresh` refolds a missing fold from the cells.
            tally.removed += 1
            if run.apply:
                unlink_robust(path)
            continue
        tally.unchanged += 1


def _dead_claims(run: _ArchivePass, kind: ArchiveFile) -> Iterator[tuple[str, HeldLock | None]]:
    """Each claim no walk holds, its lock taken under ``--apply``: a live holder's is never touched."""
    tally = run.tallies[kind]
    for key in run.archive.keys(kind):
        lock_path = run.archive.path(ArchiveFile.CLAIM_LOCK, key)
        lock: HeldLock | None = None
        if run.apply:
            lock = take_lock(lock_path, wait_s=0)
            held = lock is None
        else:
            with probe_lock(lock_path) as probed:
                held = probed
        if held:
            tally.skipped += 1
            continue
        tally.removed += 1
        yield key, lock


def _walk_claim_rows(run: _ArchivePass) -> None:
    for key, lock in _dead_claims(run, ArchiveFile.CLAIM_ROW):
        if lock is not None:
            try:
                unlink_robust(run.archive.path(ArchiveFile.CLAIM_ROW, key))
            finally:
                lock.release()


def _walk_claim_locks(run: _ArchivePass) -> None:
    for key, lock in _dead_claims(run, ArchiveFile.CLAIM_LOCK):
        if lock is not None:
            CellClaim(lock, run.archive.path(ArchiveFile.CLAIM_ROW, key)).release()


class _ArchiveWalk(NamedTuple):
    title: str
    walk: Callable[[_ArchivePass], None]


# Dependency order: cold before the cells that rewrite it, every answer before the configs it names.
_ARCHIVE_WALKS: dict[ArchiveFile, _ArchiveWalk] = {
    ArchiveFile.COLD: _ArchiveWalk("cold payloads — undecodable or with no cell file", _walk_cold),
    ArchiveFile.CELLS: _ArchiveWalk("filed answers (rows)", _walk_cells),
    ArchiveFile.CONFIG: _ArchiveWalk("configurations — refused or naming no answer", _walk_configs),
    ArchiveFile.INDEX: _ArchiveWalk(
        "index rows — rebuilt from the cells when one goes", _walk_index
    ),
    ArchiveFile.SAMPLE_FOLD: _ArchiveWalk(
        "sample folds — unreadable, refolded from the cells", _walk_sample_folds
    ),
    ArchiveFile.CLAIM_ROW: _ArchiveWalk("claim rows a dead walk left", _walk_claim_rows),
    ArchiveFile.CLAIM_LOCK: _ArchiveWalk("claim locks a dead walk left", _walk_claim_locks),
}
assert _ARCHIVE_WALKS.keys() == set(ArchiveFile), (
    "a file kind the archive writes and no re-stamp walks keeps its old shape through every deploy"
)


def _remove_unnamed(archive: MeasurementArchive, *, apply: bool) -> int:
    """Each file no `ArchiveFile` names is read by nothing, so it goes, named, with its emptied folders."""
    unnamed = archive.unwalked()
    if unnamed:
        print(f"\nMeasurement archive {archive.base_dir}: files no archive kind names")
    for path in unnamed:
        print(f"  {'removed' if apply else 'would remove'}: {path}")
        if apply:
            unlink_robust(path)
    kept = {archive.folder(kind) for kind in ArchiveFile}
    for folder in {path.parent for path in unnamed} if apply else ():
        while folder not in kept and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    return len(unnamed)


def restamp_measurement_archives(*, apply: bool) -> dict[str, int]:
    """A record its model refuses after pruning cannot be keyed: it goes with what it alone describes."""
    homes = archive_homes(default_projects_root())
    writers = archive_writers(default_projects_root())
    if homes and writers:
        print(f"\nMeasurement archives — {writers} cycle(s) can still append, so none was walked")
        return {
            "archive_records_pruned": 0,
            "archive_records_removed": 0,
            "archive_writers": writers,
        }
    pruned = removed = 0
    gone: Counter[str] = Counter()
    for home in homes:
        run = _ArchivePass(MeasurementArchive.at(home), apply=apply)
        removed += _remove_unnamed(run.archive, apply=apply)
        for walk in _ARCHIVE_WALKS.values():
            walk.walk(run)
        for kind, walk in _ARCHIVE_WALKS.items():
            tally = run.tallies[kind]
            if tally.rewritten or tally.unchanged or tally.removed or tally.skipped:
                tally.report(f"Measurement archive {home}: {walk.title}", apply=apply)
            pruned += tally.rewritten
            removed += tally.removed
            gone += tally.gone
    if gone:
        print("\nDropped — archive keys the engine no longer declares, and the value each held:")
        for entry, n in gone.most_common():
            print(f"  {n:4d}x  {entry}")
    if not apply and (pruned or removed):
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "archive_records_pruned": pruned,
        "archive_records_removed": removed,
        "archive_writers": 0,
    }


_REMEDY = "python -m promptpotter restamp --apply removes it while no cycle appends"


def _archive_refusals(archive: MeasurementArchive) -> Iterator[str]:
    """Each file the store's own reader refuses; a claim is read only by its live holder."""
    for path in archive.unwalked():
        yield f"{path} — no archive kind names this file; {_REMEDY}"
    folds = partial(archive.path, ArchiveFile.SAMPLE_FOLD)
    readers: tuple[tuple[ArchiveFile, Callable[[str], object]], ...] = (
        (ArchiveFile.INDEX, lambda _key: archive.list_all()),
        (ArchiveFile.CONFIG, archive.config),
        (ArchiveFile.CELLS, archive.answers),
        (ArchiveFile.COLD, archive.read_cold),
        (ArchiveFile.SAMPLE_FOLD, lambda key: read_sample_fold(folds(key))),
    )
    for kind, read in readers:
        for key in archive.keys(kind):
            try:
                read(key)
            except (OSError, EOFError, ValueError, TypeError, KeyError) as exc:
                cause = str(exc).partition("\n")[0]
                yield f"{archive.path(kind, key)} — {type(exc).__name__}: {cause}; {_REMEDY}"


class _CampaignOf(NamedTuple):
    tenant_id: str
    campaign_id: str


def _campaign_of(tree: pathlib.Path, path: pathlib.Path) -> _CampaignOf | None:
    """A sandbox's file is its owner's campaign, whose delete takes the sandbox; a draft's is none."""
    if tree != default_projects_root():
        owner = read_sandbox_owner(tree)
        return None if owner is None else _CampaignOf(owner.tenant_id, owner.campaign_id)
    parts = path.relative_to(tree).parts
    return _CampaignOf(parts[0], parts[2]) if len(parts) > 2 and parts[1] == "campaigns" else None


def check_readable() -> dict[str, int]:
    """Read-only, after every rewrite: a campaign or archive file still refusing its reader is named."""
    refused: dict[_CampaignOf, None] = {}
    ledgers: list[str] = []
    manifests: list[str] = []
    checked = 0
    for tree in workspace_trees(default_projects_root()):
        for ledger in sorted(tree.glob("**/.runtime/ledger.jsonl")):
            checked += 1
            try:
                for _ in CycleEventLog(ledger).iter():
                    pass
            except LedgerUnreadableError as refusal:
                ledgers.append(str(refusal))
                if (campaign := _campaign_of(tree, ledger)) is not None:
                    refused[campaign] = None
        for workspace in sorted(p for p in tree.iterdir() if p.is_dir()):
            store = CampaignStore(WorkspaceDir(workspace))
            for campaign_id in store.list_campaign_ids():
                try:
                    store.load_campaign(campaign_id)
                except (ManifestUnreadableError, ValueError, OSError) as exc:
                    manifests.append(str(exc))
                    if campaign := _campaign_of(tree, store.campaign_root_dir(campaign_id)):
                        refused[campaign] = None

    homes = archive_homes(default_projects_root())
    archive_refused = [
        cause for home in homes for cause in _archive_refusals(MeasurementArchive.at(home))
    ]
    # An account's shelf holds the install's packs too: each install refusal is named once.
    install = PackShelf(account=None).refusals()
    root = default_projects_root()
    accounts = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    packs_refused = [f"{r.name} (install) — {r.reason}" for r in install] + [
        f"{r.name} (account {account.name}) — {r.reason}"
        for account in accounts
        for r in PackShelf(account=account / BLOCK_PACKS_DIR).refusals()
        if r not in install
    ]

    print(f"\nCycle ledgers — {checked} checked, {checked - len(ledgers)} read")
    for cause in (*ledgers, *manifests):
        print(f"  {cause}")
    if refused:
        print(f"\nUnreadable campaigns — {len(refused)}; deleting each is the remedy:")
    # `deploy-linux/update.sh` lifts these lines into its health verdict by their prefix.
    for tenant_id, campaign_id in refused:
        print(
            f"  UNREADABLE campaign {campaign_id} (tenant {tenant_id}) — "
            f"python -m promptpotter --tenant {tenant_id} delete {campaign_id}"
        )
    print(f"\nMeasurement archives — {len(homes)} home(s), {len(archive_refused)} file(s) refused")
    for cause in archive_refused:
        print(f"  UNREADABLE archive {cause}")
    print(f"\nBlock packs — {len(packs_refused)} refused, each serving nothing until fixed")
    for cause in packs_refused:
        print(f"  REFUSED pack {cause}")
    return {
        "ledgers_checked": checked,
        "ledgers_refused": len(ledgers),
        "manifests_refused": len(manifests),
        "campaigns_unreadable": len(refused),
        "archive_refused": len(archive_refused),
        "packs_refused": len(packs_refused),
    }
