"""Re-stamp every on-disk ``StrictModel`` record onto the current model. ``extra="forbid"`` obliges
EVERY on-disk kind, and :data:`_SURFACES` is where that obligation is discharged — as a ROW."""

from __future__ import annotations

import json
import pathlib
import types
from collections import Counter
from collections.abc import Callable
from typing import Any, NamedTuple, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from promptpotter.application.campaign_config import CampaignConfig, freeze_campaign_config
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.infrastructure.store.io import write_yaml
from promptpotter.infrastructure.store.user_store import User

__all__ = ["restamp_campaign_configs"]

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
