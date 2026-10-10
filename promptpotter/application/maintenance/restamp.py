from __future__ import annotations

import json
import os
import pathlib
import types
from collections import Counter
from collections.abc import Callable
from typing import Any, NamedTuple, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.maintenance.archive_maintenance import (
    iter_cycle_ledgers,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign, CampaignResult
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.domain.run_records import CycleRecord, RoundClosedRecord, scored_cell
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.cycle_index import (
    read_cycle_index,
    write_cycle_index,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.store.user_store import User

__all__ = [
    "check_round_closes",
    "compact_cycle_ledgers",
    "reproject_cycle_indexes",
    "restamp_campaign_configs",
]


_Rewrite = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _as_frozen(pruned: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    return CampaignConfig.model_validate(pruned).frozen(arm=doc.get("arm") is not None)


def _as_pruned(pruned: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    return pruned


class _Surface(NamedTuple):
    title: str
    verb: str
    workspace_globs: tuple[str, ...]
    # Empty means the whole document IS the record.
    key_path: tuple[str, ...]
    model_cls: type[BaseModel]
    rewrite: _Rewrite
    benchmark_globs: tuple[str, ...] = ()


# Inner record first for a document addressed twice. No round close: pruning cannot restore a rename.
_SURFACES: tuple[_Surface, ...] = (
    _Surface(
        title="Minted snapshots (campaigns/*/campaign.json::config) — rewritten whole",
        verb="re-stamped",
        workspace_globs=("*/campaigns/*/campaign.json",),
        key_path=("config",),
        model_cls=CampaignConfig,
        rewrite=_as_frozen,
    ),
    _Surface(
        title="Campaign manifests (campaigns/*/campaign.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/campaigns/*/campaign.json",),
        key_path=(),
        model_cls=Campaign,
        rewrite=_as_pruned,
    ),
    _Surface(
        title="Campaign results (campaigns/*/result.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/campaigns/*/result.json",),
        key_path=(),
        model_cls=CampaignResult,
        rewrite=_as_pruned,
    ),
    _Surface(
        title="Dataset templates (datasets/*/campaign.yaml::campaign_config) — pruned only",
        verb="pruned",
        workspace_globs=("*/datasets/*/campaign.yaml",),
        key_path=("campaign_config",),
        model_cls=CampaignConfig,
        rewrite=_as_pruned,
        benchmark_globs=("*/campaign.yaml",),
    ),
    _Surface(
        title="Backend records (backends/*/backend.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/backends/*/backend.json",),
        key_path=(),
        model_cls=BackendConnection,
        rewrite=_as_pruned,
    ),
    _Surface(
        title="User records (user.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/user.json",),
        key_path=(),
        model_cls=User,
        rewrite=_as_pruned,
    ),
    _Surface(
        title="Diagnostic runs (diagnostics/runs/*.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/diagnostics/runs/*.json",),
        key_path=(),
        model_cls=DiagnosticRunRecord,
        rewrite=_as_pruned,
    ),
)


def _nested_model(ann: Any) -> type[BaseModel] | None:
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return ann
    if get_origin(ann) in (Union, types.UnionType):
        for arg in get_args(ann):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


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
        # Pruned to the annotated base, what a registered subclass banked would go.
        nested = (
            model_cls.stored_field_model(key, raw) if issubclass(model_cls, StrictModel) else None
        ) or _nested_model(field.annotation)
        if nested is not None and isinstance(value, dict):
            sub, sub_dropped = _prune_to_schema(value, nested, path)
            pruned[key] = sub
            dropped.extend(sub_dropped)
        else:
            pruned[key] = value
    return pruned, dropped


class _Tally:
    def __init__(self) -> None:
        self.rewritten = self.unchanged = self.empty = self.skipped = self.failed = 0
        self.gone: Counter[str] = Counter()

    def report(self, title: str, verb: str) -> None:
        print(f"\n{title}")
        print(f"  {verb:>18}: {self.rewritten}")
        print(f"  {'already current':>18}: {self.unchanged}")
        print(f"  {'no config':>18}: {self.empty}")
        print(f"  {'skipped':>18}: {self.skipped}")
        print(f"  {'still invalid':>18}: {self.failed}")


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
        surface.model_cls.model_validate(pruned)
    except ValidationError as exc:
        tally.failed += 1
        print(f"  FAIL  {path}: still invalid after pruning — {exc.error_count()} error(s)")
        for err in exc.errors():
            print(f"          {'.'.join(map(str, err['loc']))}: {err['type']}")
        return

    new = surface.rewrite(pruned, doc)
    if new == raw:
        tally.unchanged += 1
        return

    for dotted, value in dropped:
        tally.gone[f"{dotted} = {value!r}"] += 1
    tally.rewritten += 1
    if apply:
        if surface.key_path:
            holder[surface.key_path[-1]] = new
        else:
            doc = new
        if is_yaml:
            write_yaml(path, doc)
        else:
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def restamp_campaign_configs(*, apply: bool) -> dict[str, int]:
    root = DEFAULT_PROJECTS_ROOT
    print(f"Workspace: {root}")
    if not root.is_dir():
        print("  absent — nothing to re-stamp.")
        return {"rewritten": 0, "failed": 0, "skipped": 0}

    benchmarks = benchmark_datasets_root()
    tallies = [_Tally() for _ in _SURFACES]
    for surface, tally in zip(_SURFACES, tallies, strict=True):
        paths = [p for g in surface.workspace_globs for p in root.glob(g)]
        paths += [p for g in surface.benchmark_globs for p in benchmarks.glob(g)]
        for path in sorted(set(paths)):
            _process(path, surface, apply=apply, tally=tally)

    for surface, tally in zip(_SURFACES, tallies, strict=True):
        tally.report(surface.title, surface.verb if apply else f"would be {surface.verb}")

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
    # `ledger.py::iter` SKIPS a line carrying a key its arm no longer declares: the whole record is lost.
    arm = _LEDGER_ARMS.get(str(rec.get("record_type")))
    if arm is None:
        return rec, []
    pruned, dropped = _prune_to_schema(rec, arm)
    return (pruned, [dotted for dotted, _ in dropped]) if dropped else (rec, [])


def _compact_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    kind = rec.get("record_type")
    if kind == "sample_scored" and isinstance(result := rec.get("result"), dict):
        # The WRITER's own projection: a second stripper drifts, and each drift deletes a field.
        facts, grade = scored_cell(result)
        # Only the grade keys the record holds: a rewrite narrows a payload, it grades nothing.
        banked = {k: v for k, v in grade.wire().items() if k in result}
        lean = {**facts.ledger_wire(), **banked}
        return None if lean == result else rec | {"result": lean}
    return None


def _compact_one(path: pathlib.Path, *, apply: bool, gone: Counter[str]) -> tuple[int, int, int]:
    before = after = rewritten = 0
    lines: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            before += len(line)
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # An unparseable line is still a line: keep the offsets honest.
                lines.append(line)
                after += len(line)
                continue
            # Prune BEFORE projecting: a stale key on the record makes the reader skip the line.
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
    if apply and rewritten:
        # Never in place: a torn rewrite loses the cycle.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return before, after, rewritten


def compact_cycle_ledgers(*, apply: bool) -> dict[str, int]:
    total_before = total_after = touched = 0
    skipped: Counter[RunPhase] = Counter()
    gone: Counter[str] = Counter()
    rows: list[tuple[int, str]] = []
    for ledger_path in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT):
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
        print("\nDropped — record keys the engine no longer declares, which the reader was")
        print("silently SKIPPING the whole line over:")
        for key, n in gone.most_common():
            print(f"  {n:4d}x  {key}")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "cycles": touched,
        "skipped_checkin": skipped.get(RunPhase.CHECKIN, 0),
        "skipped_producing": sum(n for p, n in skipped.items() if p is not RunPhase.CHECKIN),
        "bytes_saved": total_before - total_after,
        "record_keys_dropped": sum(gone.values()),
    }


def reproject_cycle_indexes(*, apply: bool) -> dict[str, int]:
    cycle_dirs = sorted(p.parent.parent for p in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT))
    touched = failed = 0
    for cycle_dir in cycle_dirs:
        try:
            index = read_cycle_index(cycle_dir)
            if index is None:
                continue
            on_disk = read_json_optional(CycleLayout(cycle_dir).manifest)
            if on_disk == index.model_dump(mode="json"):
                continue
            if apply:
                write_cycle_index(cycle_dir)
            touched += 1
        except (OSError, ValueError):
            failed += 1

    verb = "re-projected" if apply else "would re-project"
    print(f"\nCycle indexes — {verb} {touched} of {len(cycle_dirs)} cycle(s) from their ledgers")
    if failed:
        print(f"  {failed:>6} unreadable — a ledger chain or an index that does not read")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {"cycle_indexes": len(cycle_dirs), "cycle_indexes_reprojected": touched}


def _drift_cause(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join("[]" if isinstance(part, int) else str(part) for part in err["loc"])
    return f"{loc or '<document>'}: {err['type']}"


def check_round_closes() -> dict[str, int]:
    causes: Counter[str] = Counter()
    first: dict[str, pathlib.Path] = {}
    checked = 0
    for ledger in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT):
        for record in iter_jsonl(ledger, record_types=frozenset({"round_closed"})):
            if record.get("record_type") != "round_closed":
                continue
            checked += 1
            try:
                RoundClosedRecord.model_validate(record)
            except ValidationError as exc:
                cause = _drift_cause(exc)
                causes[cause] += 1
                first.setdefault(cause, ledger)

    failed = sum(causes.values())
    print(f"\nRound closes — {checked} checked, {checked - failed} load")
    for cause, n in causes.most_common():
        print(f"  {n:>6} {cause}")
        print(f"         first: {first[cause]}")
    if failed:
        print(
            "  Never rewritten here: pruning cannot restore a renamed field's value, so the fix "
            "is the model or a migration of its own."
        )
    return {"rounds_checked": checked, "rounds_unreadable": failed}
