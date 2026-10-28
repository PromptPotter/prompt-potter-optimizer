from __future__ import annotations

import json
import pathlib
import types
from collections import Counter
from collections.abc import Callable
from functools import reduce
from typing import Annotated, Any, Literal, NamedTuple, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from promptpotter.application.datasets.authored import campaign_file_block
from promptpotter.application.jobs.registry import Job
from promptpotter.application.maintenance.archive_maintenance import (
    iter_cycle_ledgers,
    workspace_trees,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, default_jobs_dir
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.campaign_config import CampaignConfig, merge_config_layers
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.diagnostic_run import DiagnosticRunRecord
from promptpotter.domain.optimizer_state import StatePart
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.scoring import GradedCell
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.projections.cycle_index import (
    read_cycle_index,
    write_cycle_index,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import definition_files
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    rewrite_lines,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandboxes_dir,
    read_sandbox_owner,
)
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
        nested = _nested_model(field.annotation, value) if isinstance(value, dict) else None
        if _holds_parts(field.annotation) and isinstance(value, dict):
            pruned[key], gone = _prune_parts(value, path)
            dropped.extend(gone)
        elif nested is not None and isinstance(value, dict):
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

    def report(self, title: str, *, apply: bool) -> None:
        print(f"\n{title}")
        print(f"  {'pruned' if apply else 'would be pruned':>18}: {self.rewritten}")
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
    root = DEFAULT_PROJECTS_ROOT
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
    cycle_dirs = sorted(p.parent.parent for p in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT))
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
    finished = invalid = 0
    for path in sorted(default_jobs_dir().glob("*.json")):
        raw = read_json_optional(path)
        if raw and raw.get("released_at") and str(raw.get("created_at", ""))[:10] < today:
            finished += 1
            if apply:
                path.unlink()
            continue
        try:
            TypeAdapter(Job).validate_python(raw)
        except ValidationError as exc:
            invalid += 1
            print(f"  FAIL  {path}: {exc.error_count()} error(s) — this job blocks every admission")
    verb = "deleted" if apply else "would delete"
    print(f"\nJob records — {verb} {finished} released before today; {invalid} still invalid")
    return {"jobs_finished": finished, "jobs_invalid": invalid}


class _CampaignOf(NamedTuple):
    tenant_id: str
    campaign_id: str


def _campaign_of(tree: pathlib.Path, path: pathlib.Path) -> _CampaignOf | None:
    """A sandbox's file is its owner's campaign, whose delete takes the sandbox; a draft's is none."""
    if tree != DEFAULT_PROJECTS_ROOT:
        owner = read_sandbox_owner(tree)
        return None if owner is None else _CampaignOf(owner.tenant_id, owner.campaign_id)
    parts = path.relative_to(tree).parts
    return _CampaignOf(parts[0], parts[2]) if len(parts) > 2 and parts[1] == "campaigns" else None


def check_readable() -> dict[str, int]:
    """Read-only, after every rewrite: a campaign still refusing its reader is named with its remedy."""
    refused: dict[_CampaignOf, None] = {}
    ledgers: list[str] = []
    manifests: list[str] = []
    checked = 0
    for tree in workspace_trees(DEFAULT_PROJECTS_ROOT):
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
    return {
        "ledgers_checked": checked,
        "ledgers_refused": len(ledgers),
        "manifests_refused": len(manifests),
        "campaigns_unreadable": len(refused),
    }
