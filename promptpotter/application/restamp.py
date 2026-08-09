"""Re-stamp every on-disk ``StrictModel`` record onto the current model. ``extra="forbid"`` obliges
EVERY on-disk kind, and :data:`_SURFACES` is where that obligation is discharged — as a ROW."""

from __future__ import annotations

import collections
import json
import os
import pathlib
import types
from collections import Counter
from collections.abc import Callable
from typing import Any, NamedTuple, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

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
from promptpotter.domain.phases import CampaignPhase, RunPhase
from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.domain.scoring import ledger_sample_view
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.io import read_json_optional, write_yaml
from promptpotter.infrastructure.store.layout import CycleLayout, inner_sandboxes_dir
from promptpotter.infrastructure.store.user_store import User

__all__ = ["compact_cycle_ledgers", "refile_round_decisions", "restamp_campaign_configs"]

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
# Measurements (`RoundResult`, extra="ignore") and the optimizer-call cache (evictable) are
# deliberately absent: a stale key must never make a paid measurement unreadable.
_SURFACES: tuple[_Surface, ...] = (
    _Surface(
        title="Minted snapshots (campaigns/*/campaign.json::config) — rewritten as a delta",
        verb="re-stamped",
        workspace_globs=("*/campaigns/*/campaign.json", "*/archive/*/campaign.json"),
        key_path=("config",),
        model_cls=CampaignConfig,
        rewrite=_as_delta,
    ),
    _Surface(
        title="Campaign manifests (campaigns/*/campaign.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/campaigns/*/campaign.json", "*/archive/*/campaign.json"),
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
        title="Backend records (archive/backends/*/backend.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/archive/backends/*/backend.json",),
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
        title="Diagnostic runs (archive/diagnostic_runs/*.json) — pruned only",
        verb="pruned",
        workspace_globs=("*/archive/diagnostic_runs/*.json",),
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
    if not root.is_dir():
        # Nothing to re-stamp is not a failure and not an unreadable file — a fresh
        # install has no workspace yet, and counting that as a skip made the verb report
        # damage it had not found.
        print(f"No workspace at {root} — nothing to re-stamp.")
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


def _compact_one(path: pathlib.Path, *, apply: bool) -> tuple[int, int, int]:
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
            out = _compact_record(rec)
            if out is None:
                lines.append(line)
                after += len(line)
                continue
            rewritten += 1
            new_line = json.dumps(out, separators=(",", ":"), default=str) + "\n"
            lines.append(new_line)
            after += len(new_line)
    if apply and rewritten:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return before, after, rewritten


def _document_decisions(ledger_path: pathlib.Path) -> dict[int, list[dict[str, Any]]]:
    """Per round, every decision the ledger stamps with it — the document's whole content.

    Keyed on the stamp ``record_decision`` was handed, never on ledger POSITION. Position looks
    like the better signal (``persist_round`` appends a drain immediately before its
    ``round:complete``, so the next close ought to name the flushing round) and it is wrong
    here: round 0 closes TWICE, the second time when the ruler warms at round 1, so the next
    close after a round-1 decision reads 0. Trusting that dropped 118 replayed decisions.
    """
    out: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    with ledger_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("record_type") != "decision" or rec.get("round") is None:
                continue
            out[int(rec["round"])].append(
                {
                    "kind": rec.get("kind"),
                    "inputs_ref": rec.get("inputs_ref") or {},
                    "outcome": rec.get("outcome"),
                    "data": rec.get("data") or {},
                }
            )
    return out


def refile_round_decisions(*, apply: bool) -> dict[str, int]:
    """Rebuild every round document's ``decisions`` from its own round's records.

    ``replay_decisions`` walks this list and re-derives each REPLAYED kind against THIS round's
    measurements, so a foreign record makes resume's divergence seam compare one round's
    decision to another round's data — a spurious halt or a missed one, and silent either way.
    """
    root = DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return {"round_files": 0, "moved": 0, "dropped": 0}

    roots = [root]
    inner = inner_sandboxes_dir(root)
    if inner.is_dir():
        roots.extend(p for p in inner.iterdir() if p.is_dir())

    touched = added = removed = 0
    for tree in roots:
        for ledger_path in sorted(tree.glob("**/.runtime/ledger.jsonl")):
            cycle_dir = ledger_path.parent.parent
            rounds_dir = cycle_dir / "rounds"
            if not rounds_dir.is_dir():
                continue
            wanted = _document_decisions(ledger_path)
            for round_file in sorted(rounds_dir.glob("round_*.json")):
                doc = read_json_optional(round_file)
                if not isinstance(doc, dict) or doc.get("round") is None:
                    continue
                have = doc.get("decisions") or []
                want = wanted.get(int(doc["round"]), [])
                if have == want:
                    continue
                touched += 1
                added += max(0, len(want) - len(have))
                removed += max(0, len(have) - len(want))
                if apply:
                    doc["decisions"] = want
                    round_file.write_text(
                        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
                    )

    verb = "re-filed" if apply else "would re-file"
    print(f"\nRound-document decisions — {verb} {touched} file(s)")
    if touched:
        print(f"  {'entries added':>18}: {added}")
        print(f"  {'entries removed':>18}: {removed}")
    return {"round_files": touched, "moved": added, "dropped": removed}


def compact_cycle_ledgers(*, apply: bool) -> dict[str, int]:
    """Re-project every finished cycle's ``.runtime/ledger.jsonl`` onto today's record shape."""
    root = DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        print(f"No workspace at {root} — no ledgers to compact.")
        return {"cycles": 0, "skipped_live": 0, "bytes_saved": 0}

    roots = [root]
    inner = inner_sandboxes_dir(root)
    if inner.is_dir():
        roots.extend(p for p in inner.iterdir() if p.is_dir())

    total_before = total_after = touched = skipped_live = 0
    rows: list[tuple[int, str]] = []
    for tree in roots:
        for ledger_path in sorted(tree.glob("**/.runtime/ledger.jsonl")):
            cycle_dir = ledger_path.parent.parent
            manifest = read_json_optional(CycleLayout(cycle_dir).manifest)
            finished = bool(manifest.get("finished_at")) if isinstance(manifest, dict) else False
            if derive_run_phase(cycle_dir, is_terminal=finished) not in _COMPACTABLE_PHASES:
                skipped_live += 1
                continue
            before, after, rewritten = _compact_one(ledger_path, apply=apply)
            total_before += before
            total_after += after
            if rewritten:
                touched += 1
                rows.append((before - after, cycle_dir.name))

    mb = 1024 * 1024
    verb = "compacted" if apply else "would compact"
    print(f"\nLedger compaction — {verb} {touched} cycle ledger(s); {skipped_live} still live")
    print(f"  {'before':>12}: {total_before / mb:8.2f} MB")
    print(f"  {'after':>12}: {total_after / mb:8.2f} MB")
    if total_before:
        pct = 100 * (total_before - total_after) / total_before
        print(f"  {'saved':>12}: {(total_before - total_after) / mb:8.2f} MB  ({pct:.1f}%)")
    for saved, name in sorted(rows, reverse=True)[:10]:
        print(f"  {saved / mb:8.2f} MB  {name}")
    if not apply and touched:
        print("\nDry run. Re-run with --apply to rewrite.")
    return {
        "cycles": touched,
        "skipped_live": skipped_live,
        "bytes_saved": total_before - total_after,
    }
