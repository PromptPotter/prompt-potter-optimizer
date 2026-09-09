"""Re-stamp every on-disk ``StrictModel`` record onto the current model. ``extra="forbid"`` obliges
EVERY on-disk kind, and :data:`_SURFACES` is where that obligation is discharged — as a ROW.

PRUNING never touches a round document: a row repairs by pruning to ``model_fields``, which
cannot restore a renamed field's value, so a repair there would be silently wrong. A migration that
RECOVERS a value from a surviving record may write one — :func:`backfill_inner_facts` does. Which
drift is fatal, and why that is correct, is owned by ``domain/CLAUDE.md`` § Tolerance is scoped by
what a payload is FOR.

It DOES prune a LEDGER record (:func:`_prune_record`, inside the compaction pass), for the opposite
reason: that reader is tolerant by SKIP rather than by default, so a stale key costs the whole
line rather than one value, and nothing raises when it does."""

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

from promptpotter.application.archive_maintenance import (
    archive_writers,
    iter_cycle_ledgers,
    workspace_trees,
)
from promptpotter.application.campaign_config import CampaignConfig, freeze_campaign_config
from promptpotter.application.run_observers import QUERY_PREVIEW_CHARS
from promptpotter.application.views.view_models import (
    L2RefineExitView,
    PlanExitView,
    ViewContext,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS
from promptpotter.domain.phases import CampaignPhase, RunPhase
from promptpotter.domain.results import DiagnosticRunRecord, RoundResult
from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.scoring import ledger_sample_view
from promptpotter.domain.spend import TOKEN_KIND_BUCKET
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import reproject_round_index
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    read_json_tolerant,
    write_json,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout
from promptpotter.infrastructure.store.user_store import User
from promptpotter.shared.errors import graceful

__all__ = [
    "backfill_inner_facts",
    "check_round_documents",
    "compact_cycle_ledgers",
    "rename_round_trend",
    "reproject_cycle_indexes",
    "restamp_campaign_configs",
    "shrink_measurement_runs",
    "stamp_election_bias",
]


def _iter_round_documents() -> list[pathlib.Path]:
    """``**`` descends dot-directories, so the ``.runtime`` filter is what excludes the audit
    twins under ``.runtime/cache/rounds/`` — same basename, and never a ``RoundResult``."""
    return [
        p
        for tree in workspace_trees(DEFAULT_PROJECTS_ROOT)
        for p in sorted(tree.glob(f"**/rounds/{ROUND_GLOB}"))
        if ".runtime" not in p.parts
    ]


# What a row writes back once the record has validated. Takes the pruned mapping rather than
# the validated model so no row has to narrow a type the table already fixed — the alternative
# was a runtime `isinstance` inside otherwise model-agnostic code, which is a table pretending
# to be a parameter.
_Rewrite = Callable[[dict[str, Any]], dict[str, Any]]


def _as_delta(pruned: dict[str, Any]) -> dict[str, Any]:
    """The minted snapshot's rewrite — today's config as the delta from today's defaults."""
    return freeze_campaign_config(CampaignConfig.model_validate(pruned))


def _as_pruned(pruned: dict[str, Any]) -> dict[str, Any]:
    """Write back exactly what validated: stale keys gone, every surviving value untouched."""
    return pruned


class _Surface(NamedTuple):
    """One on-disk model kind: where it lives, what validates it, what happens to it. ``key_path``
    addresses the record inside the document; empty means the whole document IS the record."""

    title: str
    verb: str
    workspace_globs: tuple[str, ...]
    key_path: tuple[str, ...]
    model_cls: type[BaseModel]
    rewrite: _Rewrite
    benchmark_globs: tuple[str, ...] = ()


# THE coverage contract. A model that reaches disk belongs here the day it is written; adding
# one is a row, not a code change. Ordered so a document addressed twice (the campaign manifest
# and the config nested inside it) has its inner record settled first.
# Measurements (`RoundResult`) and the optimizer reuse cache (evictable) are deliberately absent.
# Read the module docstring before adding either: the reason is NOT that `extra="ignore"` makes a
# round document safe — it does not — and `check_round_documents` is what covers it instead.
_SURFACES: tuple[_Surface, ...] = (
    _Surface(
        title="Minted snapshots (campaigns/*/campaign.json::config) — rewritten as a delta",
        verb="re-stamped",
        workspace_globs=("*/campaigns/*/campaign.json",),
        key_path=("config",),
        model_cls=CampaignConfig,
        rewrite=_as_delta,
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
    """The nested ``BaseModel`` an annotation carries, unwrapping ``X | None``. A sub-model reached
    through an OPTIONAL field must still be pruned, or the re-stamp still raises on load."""
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
    """Drop keys ``model_cls`` no longer declares, recursing into nested models. A ``dict[str, X]``
    field is free-form operator data and passes through untouched."""
    pruned: dict[str, Any] = {}
    dropped: list[tuple[str, Any]] = []
    for key, value in raw.items():
        path = (*prefix, key)
        field = model_cls.model_fields.get(key)
        if field is None:
            dropped.append((".".join(path), value))
            continue
        nested = _nested_model(field.annotation)
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
    """Prune, validate and rewrite the record *surface* addresses. Format follows the tree —
    ``.yaml`` templates in and out, ``.json`` records in and out."""
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

    new = surface.rewrite(pruned)
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
    """Scan every surface; report, and rewrite the rows that rewrite. Roots come from
    ``config/paths.py``, so the verb addresses the trees the engine reads from any CWD."""
    root = DEFAULT_PROJECTS_ROOT
    # A root that exists but is the WRONG tree reports a clean bill of health over data nobody
    # asked about, so the subject is named on every path, not only the absent one.
    print(f"Workspace: {root}")
    if not root.is_dir():
        # Nothing to re-stamp is not a failure and not an unreadable file — a fresh
        # install has no workspace yet, and counting that as a skip made the verb report
        # damage it had not found.
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


# --------------------------------------------------------------------------- #
# Ledger compaction — the same verb's job on the append-only stream.
#
# Not a ``_Surface`` row: those prune a ``StrictModel`` by ``model_fields``, and a ledger
# payload is ``dict[str, Any]``. What it prunes to is not authored here either — it calls the
# projections the WRITER now uses, because a second stripper would drift from the first and
# each drift silently deletes a different field.
#
# It is compaction, not deletion. The ledger is the append-only chronology: which round, which
# candidate, in what order, against which rival. The archive is a last-wins fold keyed
# (dataset_name, node_configs, sample_id) and cannot answer any of those — so what comes out
# here is only what the archive and ``rounds/round_NNNN.json`` already hold verbatim.
# --------------------------------------------------------------------------- #

# The keys each projection leaves behind, DERIVED from the writer's own definitions so a field
# added to either view reaches this pass without a second edit.
_ANCHOR_KEYS: frozenset[str] = frozenset(ViewContext().ledger_anchors())
_L2_EXIT_VIEW_KEYS: frozenset[str] = frozenset(L2RefineExitView.__dataclass_fields__)
_PLAN_EXIT_VIEW_KEYS: frozenset[str] = frozenset(PlanExitView.__dataclass_fields__)
_EXIT_VIEW_KEYS: dict[str, frozenset[str]] = {
    CampaignPhase.REFINE_STRATEGY: _L2_EXIT_VIEW_KEYS,
    CampaignPhase.MODIFY_PLAN: _PLAN_EXIT_VIEW_KEYS,
}
# Rewrite only a cycle nothing is appending to. A live producer holds `_next_offset`, and every
# `sequence`/`offset` join (the SSE tail, the family ray) is that line index — renumber under one
# and the stream skips or repeats. PAUSED qualifies on the run-phase contract's own terms ("a
# paused producer has exited", `runtime_flags.derive_run_phase`) and MUST be included, not merely
# may: a paused cycle is the one an operator resumes, and resume is what reads the counters this
# pass migrates. RUNNING / GATE (a fresh producer) and CHECKIN (pre-loop) are the exclusions.
_COMPACTABLE_PHASES: frozenset[RunPhase] = frozenset(
    {RunPhase.TERMINAL, RunPhase.DETACHED, RunPhase.PAUSED}
)


# Every ledger arm by its `record_type` discriminator. DERIVED from the union, so a record kind
# added, renamed or retired reaches this pass without a second edit here.
_LEDGER_ARMS: dict[str, type[BaseModel]] = {
    str(arm.model_fields["record_type"].default): arm for arm in get_args(get_args(CycleRecord)[0])
}


def _prune_record(rec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop the keys no ``CycleRecord`` arm declares any more, and name them.

    This is the one migration a field DELETE on a ledger record needs, and it is needed because
    the reader is tolerant BY SKIP: ``ledger.py::iter`` logs the line and continues, so a deleted
    field raises nowhere — the record is simply gone, and ``read_ruler``, every projection rebuild
    and every fork lookup behave as though it was never written. ``DeltaRuler.anchored_at_round``
    is the case that named it: dropping a reader-less field would have silently un-ruled every
    banked cycle, which reads downstream as "θ that cannot be reproduced".

    A RENAME is still not this — pruning cannot restore the value under its new name, and the
    module docstring says which act may."""
    arm = _LEDGER_ARMS.get(str(rec.get("record_type")))
    if arm is None:
        return rec, []
    pruned, dropped = _prune_to_schema(rec, arm)
    return (pruned, [dotted for dotted, _ in dropped]) if dropped else (rec, [])


def _compact_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """One stored record → what the writer would emit for it today. ``None`` ⇒ already current."""
    payload = rec.get("payload")
    if not isinstance(payload, dict):
        return None

    if rec.get("record_type") == "snapshot":
        event = rec.get("event")
        if event == "sample_scored" and isinstance(payload.get("result"), dict):
            lean = ledger_sample_view(payload["result"])
            return (
                None
                if lean == payload["result"]
                else rec | {"payload": {**payload, "result": lean}}
            )
        if event == "sample_started" and "query_text" in payload:
            trimmed = {k: v for k, v in payload.items() if k != "query_text"}
            trimmed["query_preview"] = str(payload.get("query_text") or "")[:QUERY_PREVIEW_CHARS]
            return rec | {"payload": trimmed}
        if event == "candidate_scored" and isinstance(payload.get("phase_ctx"), dict):
            ctx = payload["phase_ctx"]
            if _ANCHOR_KEYS.issuperset(ctx):
                return None
            return rec | {
                "payload": {**payload, "phase_ctx": {k: ctx.get(k) for k in _ANCHOR_KEYS}}
            }
        return None

    if rec.get("record_type") != "phase":
        return None

    new = {k: v for k, v in payload.items() if k != "data"}
    ctx = new.get("phase_ctx")
    if isinstance(ctx, dict):
        new["phase_ctx"] = {k: ctx.get(k) for k in _ANCHOR_KEYS}
    keep = _EXIT_VIEW_KEYS.get(str(rec.get("phase"))) if rec.get("event") == "exit" else None
    view = new.get("view")
    if keep is not None and isinstance(view, dict):
        # MIGRATE before pruning. On a record written before the counters became view fields
        # they sit in `data`, which this pass drops — lift them across first or `resume` on
        # this cycle dies in `EscalationFSM.from_ledger` on the key that moved. The lift is
        # the whole reason a paused cycle must be compacted rather than left alone.
        old = payload.get("data")
        if isinstance(old, dict):
            view = {**view, **{k: old[k] for k in keep if k not in view and k in old}}
        new["view"] = {k: v for k, v in view.items() if k in keep}
    return None if new == payload else rec | {"payload": new}


def _compact_one(path: pathlib.Path, *, apply: bool, gone: Counter[str]) -> tuple[int, int, int]:
    """``(bytes_before, bytes_after, records_rewritten)``. A tmp + ``os.replace``, never in
    place — ``CycleEventLog.append`` is not crash-atomic, so a torn rewrite loses the cycle."""
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
            # Prune BEFORE projecting: the projections read `payload`, and a stale key sits one
            # level above it on the record itself.
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
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return before, after, rewritten


def compact_cycle_ledgers(*, apply: bool) -> dict[str, int]:
    """Re-project every finished cycle's ``.runtime/ledger.jsonl`` onto today's record shape."""
    total_before = total_after = touched = 0
    skipped: Counter[RunPhase] = Counter()
    gone: Counter[str] = Counter()
    rows: list[tuple[int, str]] = []
    for ledger_path in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT):
        cycle_dir = ledger_path.parent.parent
        manifest = read_json_optional(CycleLayout(cycle_dir).manifest)
        finished = bool(manifest.get("finished_at")) if isinstance(manifest, dict) else False
        phase = derive_run_phase(cycle_dir, is_terminal=finished)
        if phase not in _COMPACTABLE_PHASES:
            skipped[phase] += 1
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
    # Name the phase rather than calling every exclusion "live": a CHECKIN skip clears only when
    # the operator Starts that campaign, a RUNNING one on the next deploy, so one word for both
    # sends the reader hunting a producer that was never there.
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


# --- (4) the cycle index, stale against the round documents it is derived from ---------------


def reproject_cycle_indexes(*, apply: bool) -> dict[str, int]:
    """Re-derive every cycle index's ``rounds[]`` from its own round documents — the maintenance
    half of ``campaign_store/store.py::reproject_round_index``, which states why."""
    by_cycle: dict[pathlib.Path, list[pathlib.Path]] = {}
    for doc in _iter_round_documents():
        by_cycle.setdefault(doc.parent.parent, []).append(doc)

    touched = failed = 0
    for cycle_dir, docs in sorted(by_cycle.items()):
        index_path = CycleLayout(cycle_dir).manifest
        if not index_path.is_file():
            continue
        try:
            touched += reproject_round_index(index_path, docs, apply=apply)
        except (OSError, ValidationError, json.JSONDecodeError):
            failed += 1

    verb = "re-projected" if apply else "would re-project"
    print(
        f"\nCycle indexes — {verb} {touched} of {len(by_cycle)} cycle(s) from their round documents"
    )
    if failed:
        print(f"  {failed:>6} unreadable — see the round-document check below")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {"cycle_indexes": len(by_cycle), "cycle_indexes_reprojected": touched}


# --- (5) the election bias a replayed round reads off its own decision record ----------------


def stamp_election_bias(*, apply: bool) -> dict[str, int]:
    """Write ``parent_bias`` onto every ``round_winner`` decision that predates the field.

    ``_replay_round_winner`` reads it rather than re-deriving it — the bias is a function of the
    round HISTORY a replay does not hold — so the key must be present on every record, and a
    read-time default for the ones lacking it is the back-compat this repo does not do. 0.0 is not
    a guess: an election that ran before the correction subtracted nothing, so it IS the bias that
    round was decided under, and writing it down is what makes the replay reproduce the decision
    instead of re-deciding it."""
    stamped = touched = 0
    for ledger_path in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT):
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        dirty = False
        for line in lines:
            rec = json.loads(line) if line.strip() else None
            ref = rec.get("inputs_ref") if isinstance(rec, dict) else None
            if isinstance(ref, dict) and "coverage_floor" in ref and "parent_bias" not in ref:
                ref["parent_bias"] = 0.0
                line = json.dumps(rec, ensure_ascii=False)
                stamped += 1
                dirty = True
            out.append(line)
        if dirty:
            touched += 1
            if apply:
                ledger_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    verb = "stamped" if apply else "would stamp"
    print(f"\nElection bias — {verb} parent_bias onto {stamped} decision(s) in {touched} ledger(s)")
    if not apply and stamped:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {"elections_stamped": stamped, "election_ledgers": touched}


def stamp_election_objective(*, apply: bool) -> dict[str, int]:
    """Write ``objective`` onto every recorded ``parent_cells`` row that predates it.

    ``elect_round_winner`` fits θ through ``exploration.py::graded_response``, which RAISES on a
    row carrying no ``objective``, so a record lacking it cannot be replayed at all — and `resume`
    surfaces that raise as a DIVERGENCE, offering the fork that abandons the line.

    NOT a guess, and not derived: the round document's ``parent_results`` is the SAME list
    ``winner.py`` projected these cells from, in the same constructor call, so the join on
    ``sample_id`` recovers the exact number the election graded. A cell the document cannot answer
    is LEFT ABSENT — it still raises, which is the honest outcome for a row nothing ever stamped.
    """
    stamped = touched = orphaned = 0
    for ledger_path in iter_cycle_ledgers(DEFAULT_PROJECTS_ROOT):
        rounds_dir = ledger_path.parents[1] / "rounds"
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        dirty = False
        for line in lines:
            parsed = json.loads(line) if line.strip() else None
            rec: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
            data = rec.get("data")
            cells = data.get("parent_cells") if isinstance(data, dict) else None
            if isinstance(cells, list) and any(
                isinstance(c, dict) and "objective" not in c for c in cells
            ):
                doc = read_json_tolerant(rounds_dir / f"round_{int(rec['round']):04d}.json", {})
                graded = {
                    r.get("sample_id"): r
                    for r in (doc.get("parent_results") or [])
                    if isinstance(r, dict)
                }
                filled = 0
                for cell in cells:
                    src = graded.get(cell.get("sample_id")) if isinstance(cell, dict) else None
                    if isinstance(cell, dict) and isinstance(src, dict) and "objective" in src:
                        cell["objective"] = src["objective"]
                        filled += 1
                    else:
                        orphaned += 1
                # A cycle whose documents carry no `parent_results` recovers nothing, and
                # re-serialising its ledger to write the same bytes back is a rewrite that
                # buys an operator no repair and one more chance to lose the file.
                if filled:
                    line = json.dumps(rec, ensure_ascii=False)
                    stamped += filled
                    dirty = True
            out.append(line)
        if dirty:
            touched += 1
            if apply:
                ledger_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    verb = "stamped" if apply else "would stamp"
    print(f"\nElection grade — {verb} objective onto {stamped} cell(s) in {touched} ledger(s)")
    if not apply and stamped:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "election_cells_graded": stamped,
        "election_grade_ledgers": touched,
        "election_cells_ungraded": orphaned,
    }


# --- (6) the L4 seed facts that reached the row as prose and nothing else --------------------


def _inner_cycle_index() -> dict[tuple[str, str, int, str], pathlib.Path]:
    """``{(outer campaign, outer cycle, round, task) -> inner cycle dir}``.

    The pointer between an outer cell and the inner campaign that produced it lives on the INNER
    side — ``index.json::spawned_by``, written by ``runner/inner/spawn.py`` — so building this
    index is the only join back. Sandboxes are a SIBLING tree of the workspace, which
    :func:`workspace_trees` already knows and a ``*``-per-level glob silently misses."""
    index: dict[tuple[str, str, int, str], pathlib.Path] = {}
    for tree in workspace_trees(DEFAULT_PROJECTS_ROOT):
        for cycle_dir in sorted(tree.glob("*/campaigns/*/cycles/*")):
            spawned = (read_json_tolerant(cycle_dir / "index.json", {}) or {}).get("spawned_by")
            if not isinstance(spawned, dict) or not spawned.get("task"):
                continue
            key = (
                str(spawned.get("outer_campaign_id") or ""),
                str(spawned.get("outer_cycle_id") or ""),
                int(spawned.get("round") or 0),
                str(spawned["task"]),
            )
            index[key] = cycle_dir
    return index


def _facts_from_inner_cycle(cycle_dir: pathlib.Path) -> dict[str, Any]:
    """What the seed's OWN campaign still says about itself, at full precision.

    Read from the inner cycle rather than from the outer row's ``reasoning_trace`` sentence, which
    prints its levels at 2dp. Only the fields checked against that sentence cell-by-cell are
    written: ``rounds[].ability.theta`` reproduces the narrated origin and ending exactly on
    every cell on disk, but its PEAK and its length do not — so it is the parent frontier at the
    endpoints and something else in between, and ``inner_peak_lift`` / ``inner_round_budget`` are
    left ABSENT rather than filled from a series that disagrees. ``inner_unworked_s`` is absent for
    a harder reason: only the spawner holding the cell's deadline ever measured it, and no file
    records it. All three fill in on the next live run; none is ever zeroed to look complete.
    """
    dash = read_json_tolerant(cycle_dir / "dashboard.json", {}) or {}
    index = read_json_tolerant(cycle_dir / "index.json", {}) or {}
    levels = [
        a["theta"]
        for r in (dash.get("rounds") or [])
        if isinstance(r, dict) and isinstance(a := r.get("ability"), dict)
    ]
    if len(levels) < 2:
        return {}
    spend = dash.get("spend")
    spend = spend if isinstance(spend, dict) else {}
    # Off the declared bucket roster, so a new spend kind is counted here the day it lands
    # rather than the day someone notices this list is short.
    buckets = [spend.get(name) for name in TOKEN_KIND_BUCKET.values()]
    tokens = sum(
        int(b.get(k) or 0)
        for b in buckets
        if isinstance(b, dict)
        for k in ("input_tokens", "output_tokens")
    )
    facts: dict[str, Any] = {
        "inner_origin_level": float(levels[0]),
        "inner_final_lift": float(levels[-1]) - float(levels[0]),
        "inner_rounds_ran": max(int(index.get("n_rounds") or 0) - 1, 0),
        "inner_stop_reason": str(index.get("stop_reason") or ""),
        "inner_campaign_id": cycle_dir.parent.parent.name,
    }
    if isinstance(spend.get("total_used_usd"), int | float):
        facts["inner_spend_usd"] = float(spend["total_used_usd"])
    if tokens:
        facts["inner_tokens"] = tokens
    return facts


def _backfill_round_document(
    path: pathlib.Path, index: dict[tuple[str, str, int, str], pathlib.Path], *, apply: bool
) -> tuple[int, int]:
    """``(rows filled, rows whose inner campaign is no longer on disk)``."""
    doc = read_json_tolerant(path, {})
    if not isinstance(doc, dict):
        return (0, 0)
    campaign_id, cycle_id = path.parents[3].name, path.parents[1].name
    round_num = int(doc.get("round") or 0)
    filled = orphaned = 0
    for rows in (doc.get("all_candidate_results") or {}).values():
        for row in rows if isinstance(rows, list) else []:
            pd = row.get("pipeline_data") if isinstance(row, dict) else None
            # Idempotent, and a live full-precision value is never overwritten by this.
            if not isinstance(pd, dict) or "inner_final_lift" in pd:
                continue
            if not isinstance(pd.get(OUTER_PROXY_KEYS[0]), int | float):
                continue  # not an inner-campaign cell at all
            cycle_dir = index.get((campaign_id, cycle_id, round_num, str(row.get("query") or "")))
            facts = _facts_from_inner_cycle(cycle_dir) if cycle_dir is not None else {}
            if not facts:
                orphaned += 1
                continue
            pd.update(facts)
            filled += 1
    if filled and apply:
        write_json(path, doc)
    return (filled, orphaned)


def backfill_inner_facts(*, apply: bool) -> dict[str, int]:
    """Lift each seed's own origin, ending, round count, stop reason and spend off the inner
    campaign that produced it and onto the outer row, for cells measured before
    ``InnerCellFacts`` carried them as numbers.

    ROUND DOCUMENTS only, deliberately. The measurement archive is content-addressed —
    ``(dataset_name, node_configs, sample_id)`` — and carries no campaign or cycle, so an archived
    row cannot be joined to the inner campaign that produced it. A cell REPLAYED from the archive
    in a future run therefore arrives without these fields until it is genuinely re-run; absent,
    which every surface already reports honestly, rather than guessed."""
    index = _inner_cycle_index()
    filled = orphaned = 0
    for path in _iter_round_documents():
        with graceful(f"backfill {path}"):
            got, lost = _backfill_round_document(path, index, apply=apply)
            filled += got
            orphaned += lost
    return {"inner_rows_filled": filled, "inner_rows_orphaned": orphaned}


# --- (7) the diagnostics key a rename left behind on the round documents ---------------------


def rename_round_trend(*, apply: bool) -> dict[str, int]:
    """Move ``diagnostics.trajectory`` onto ``diagnostics.trend`` on every banked round document.

    A rename that ships without one of these is the recurring shape, and this field's version of
    it is the quiet kind. ``RoundDiagnostics`` is a stdlib dataclass reached through
    ``RoundResult``, so the old key does not raise on load — it is dropped, and ``trend`` reads
    its DEFAULT. The default is ``"healthy"``. A resumed cycle rebuilds ``cycle.rounds`` from
    these documents and ``dispatch/facade.py`` hands the newest one's diagnostics to the TREND
    panel, so a run that had plateaued or hit a ceiling resumes telling the optimizer it is
    climbing — no error, no log line, and the panel reads exactly as it does when true.

    Pruning cannot do this (it drops the stale key and its value together); this recovers the
    value from the record that still holds it, which is what the module docstring sanctions.
    """
    moved = touched = 0
    for path in _iter_round_documents():
        with graceful(f"rename trend {path}"):
            doc = read_json_tolerant(path, {})
            diag = doc.get("diagnostics") if isinstance(doc, dict) else None
            if not isinstance(diag, dict):
                continue
            dirty = False
            for old, new in (
                ("trajectory", "trend"),
                ("trajectory_description", "trend_description"),
            ):
                if old in diag:
                    # A document carrying BOTH was written by the new code and re-read by the
                    # old; the live spelling is the one to keep.
                    diag.setdefault(new, diag[old])
                    del diag[old]
                    dirty = True
                    moved += 1
            if dirty:
                touched += 1
                if apply:
                    write_json(path, doc)

    verb = "moved" if apply else "would move"
    print(f"\nRound trend — {verb} {moved} key(s) across {touched} round document(s)")
    if not apply and moved:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {"trend_keys_moved": moved, "trend_documents": touched}


# --- (8) the run-level pipeline config re-stored on every measurement row --------------------


def _iter_measurement_runs() -> list[pathlib.Path]:
    """Every archived run's detail log. An inner sandbox isolates campaign state but NOT the
    content-addressed caches, so in practice these all live under the real projects root — the
    sandbox trees are walked anyway rather than asserting that from here."""
    return [
        p
        for tree in workspace_trees(DEFAULT_PROJECTS_ROOT)
        for p in sorted(tree.glob("*/measurements/runs/*.jsonl"))
    ]


def _shrink_one(path: pathlib.Path, *, apply: bool) -> tuple[int, int, int]:
    """``(bytes_before, bytes_after, rows_rewritten)``. Every line is kept, in order: the file is a
    fold log keyed on ``k`` (last-wins per sample, header included), so dropping or reordering one
    changes what ``_fold_detail`` returns. tmp + ``os.replace``, never in place."""
    before = after = rewritten = 0
    lines: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            before += len(line)
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                after += len(line)
                continue
            pd = row.get("pipeline_data") if isinstance(row, dict) else None
            if not isinstance(pd, dict) or "pipeline_params" not in pd:
                lines.append(line)
                after += len(line)
                continue
            del pd["pipeline_params"]
            rewritten += 1
            new_line = json.dumps(row, separators=(",", ":"), default=str) + "\n"
            lines.append(new_line)
            after += len(new_line)
    if apply and rewritten:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return before, after, rewritten


def shrink_measurement_runs(*, apply: bool) -> dict[str, int]:
    """Drop ``pipeline_data.pipeline_params`` from archived measurement rows.

    It is constant across a run, and the archive already keeps it twice at run level — on the
    detail log's own header row and on the index entry (``measurement_archive.py::_summary``),
    which is where its one reader takes it from (``intelligence/indexes/axis.py``). Per sample it
    was the same ~3 KB blob on every row.

    Safe against the cache key by construction: ``content_hash`` is sha256 over the rendered
    prompt, the dataset pairs and the search point's ``pipeline_params`` (``shared/hashing.py``) —
    never the stored row bytes — so no archived cell moves."""
    writers = archive_writers(DEFAULT_PROJECTS_ROOT)
    if writers:
        print(f"\nMeasurement rows — SKIPPED, {writers} cycle(s) can still append to the archive")
        return {"runs_shrunk": 0, "run_bytes_saved": 0, "archive_writers": writers}

    total_before = total_after = touched = rows = 0
    for path in _iter_measurement_runs():
        with graceful(f"shrink {path}"):
            before, after, rewritten = _shrink_one(path, apply=apply)
            total_before += before
            total_after += after
            rows += rewritten
            if rewritten:
                touched += 1

    mb = 1024 * 1024
    verb = "shrank" if apply else "would shrink"
    print(f"\nMeasurement rows — {verb} {rows} row(s) across {touched} run(s)")
    print(f"  {'before':>12}: {total_before / mb:8.2f} MB")
    print(f"  {'after':>12}: {total_after / mb:8.2f} MB")
    if total_before:
        pct = 100 * (total_before - total_after) / total_before
        print(f"  {'saved':>12}: {(total_before - total_after) / mb:8.2f} MB  ({pct:.1f}%)")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "runs_shrunk": touched,
        "run_bytes_saved": total_before - total_after,
        "archive_writers": 0,
    }


# --- (9) the overlap rows a flat list cannot attribute ---------------------------------------


def rekey_overlap_results(*, apply: bool) -> dict[str, int]:
    """Key ``RoundResult.overlap_results`` by the individual each row MEASURED.

    A flat list names nobody, so a document carrying one does not load at all. It can only ever
    have held the round's own winner's cells, and ``OverlapReading.members`` already names that
    arm last — adoption order, C0 first. Rows no document can attribute that way are DROPPED
    rather than guessed onto a member: they are report-only, and the archive still holds them, so
    the next election re-buys them on a cache hit.
    """
    rekeyed = dropped = touched = 0
    for path in _iter_round_documents():
        with graceful(f"rekey overlap {path}"):
            doc = read_json_tolerant(path, {})
            rows = doc.get("overlap_results") if isinstance(doc, dict) else None
            if not isinstance(rows, list):
                continue
            reading = doc.get("overlap")
            members = reading.get("members") if isinstance(reading, dict) else None
            winner = members[-1].get("candidate_id") if members else None
            if rows and winner:
                doc["overlap_results"] = {winner: rows}
                rekeyed += len(rows)
            else:
                doc["overlap_results"] = {}
                dropped += len(rows)
            touched += 1
            if apply:
                write_json(path, doc)

    verb = "rekeyed" if apply else "would rekey"
    print(f"\nOverlap rows — {verb} {rekeyed} row(s) across {touched} round document(s)")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "overlap_rows_rekeyed": rekeyed,
        "overlap_rows_dropped": dropped,
        "overlap_documents": touched,
    }


def _drift_cause(exc: ValidationError) -> str:
    """Indices collapse to ``[]`` so one rename reads as one cause rather than one per row."""
    err = exc.errors()[0]
    loc = ".".join("[]" if isinstance(part, int) else str(part) for part in err["loc"])
    return f"{loc or '<document>'}: {err['type']}"


def check_round_documents() -> dict[str, int]:
    """Report which banked round documents no longer load. The drift this catches is otherwise
    SILENT — ``verify``, ``resume`` and the ``ab`` replay each raise on it, and nothing else does."""
    causes: Counter[str] = Counter()
    first: dict[str, pathlib.Path] = {}
    paths = _iter_round_documents()
    for path in paths:
        # `read_json_optional`, not tolerant: a corrupt round document is a finding, and
        # collapsing it into "absent" is what would hide it.
        try:
            RoundResult.model_validate(read_json_optional(path))
        except (ValidationError, ValueError, OSError) as exc:
            cause = _drift_cause(exc) if isinstance(exc, ValidationError) else f"unreadable: {exc}"
            causes[cause] += 1
            first.setdefault(cause, path)

    failed = sum(causes.values())
    print(f"\nRound documents — {len(paths)} checked, {len(paths) - failed} load")
    for cause, n in causes.most_common():
        print(f"  {n:>6} {cause}")
        print(f"         first: {first[cause]}")
    if failed:
        print(
            "  Never rewritten here: pruning cannot restore a renamed field's value, so the fix "
            "is the model or a migration of its own."
        )
    return {"rounds_checked": len(paths), "rounds_unreadable": failed}
