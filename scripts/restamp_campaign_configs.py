"""Re-stamp every on-disk ``CampaignConfig`` onto the current model.

``CampaignConfig`` is ``extra="forbid"``. A file written before a field was renamed or dropped
still carries that field, so ``load_campaign_config`` raises ``extra_forbidden`` and the campaign
can no longer be read — silently, until someone tries to resume it. Re-stamping is the sanctioned
remedy. Never ``extra="allow"``, never an alias, never a migration shim.

Two trees, two shapes, two treatments — they share a filename and nothing else:

- **The minted snapshot**, ``campaigns/{id}/campaign.json::config``. Machine-written. Rewritten
  as the **delta from today's defaults** (``freeze_campaign_config``), which is the shape the
  engine now persists.
- **The dataset template**, ``datasets/{slug}/campaign.json::campaign_config``. Human-authored.
  Stale keys are pruned and *nothing else changes* — an operator who wrote a default value out
  in full meant to see it there. A delta would silently reformat their file.

Both: every dropped key is reported with the value it held, never silently. Whether that value
*was* the default of the day is unknowable — a deleted field leaves no default behind — so this
reports rather than classifies, and never maps an old key onto a new one. Guessing a mapping is
a migration shim wearing a script.

Skips are counted and printed; a scan that swallows ``OSError`` reports a vacuous zero.

Usage::

    python scripts/restamp_campaign_configs.py            # dry run — reports, writes nothing
    python scripts/restamp_campaign_configs.py --apply     # rewrite
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from typing import Any

from pydantic import BaseModel, ValidationError

from promptpotter.application.config import (
    CampaignConfig,
    freeze_campaign_config,
    load_campaign_config,
)


def _prune_to_schema(
    raw: dict[str, Any], model_cls: type[BaseModel], prefix: tuple[str, ...] = ()
) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Drop keys ``model_cls`` no longer declares. Returns ``(pruned, [(dotted_path, value)])``.

    Recurses only into nested *models*; a ``dict[str, X]`` field (``pipeline_overrides``,
    ``optimizer_narrowing``) is free-form operator data and is passed through untouched.
    """
    pruned: dict[str, Any] = {}
    dropped: list[tuple[str, Any]] = []
    for key, value in raw.items():
        path = (*prefix, key)
        field = model_cls.model_fields.get(key)
        if field is None:
            dropped.append((".".join(path), value))
            continue
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel) and isinstance(value, dict):
            sub, sub_dropped = _prune_to_schema(value, ann, path)
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


def _process(
    path: pathlib.Path, key_path: tuple[str, ...], *, delta: bool, apply: bool, tally: _Tally
) -> None:
    """Prune (and for a snapshot, delta-ify) the config at *key_path* inside the JSON at *path*."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        tally.skipped += 1
        print(f"  SKIP  {path}: {type(exc).__name__}: {exc}")
        return

    holder: Any = doc
    for key in key_path[:-1]:
        holder = holder.get(key)
        if not isinstance(holder, dict):
            tally.empty += 1
            return
    raw = holder.get(key_path[-1])
    if not raw:
        tally.empty += 1
        return

    pruned, dropped = _prune_to_schema(raw, CampaignConfig)
    try:
        config = load_campaign_config(pruned)
    except ValidationError as exc:
        tally.failed += 1
        print(f"  FAIL  {path}: still invalid after pruning — {exc.error_count()} error(s)")
        for err in exc.errors():
            print(f"          {'.'.join(map(str, err['loc']))}: {err['type']}")
        return

    new = freeze_campaign_config(config) if delta else pruned
    if new == raw:
        tally.unchanged += 1
        return

    for dotted, value in dropped:
        tally.gone[f"{dotted} = {value!r}"] += 1
    tally.rewritten += 1
    if apply:
        holder[key_path[-1]] = new
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Rewrite (default: report only).")
    ap.add_argument("--root", default=".promptpotter/projects", help="Workspace tenant root.")
    ap.add_argument("--datasets", default="datasets", help="Repo benchmark datasets dir.")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    if not root.is_dir():
        print(f"No workspace at {root}", file=sys.stderr)
        return 1

    snapshots = _Tally()
    for path in sorted(root.glob("*/campaigns/*/campaign.json")) + sorted(
        root.glob("*/archive/*/campaign.json")
    ):
        _process(path, ("config",), delta=True, apply=args.apply, tally=snapshots)

    templates = _Tally()
    for path in sorted(root.glob("*/datasets/*/campaign.json")) + sorted(
        pathlib.Path(args.datasets).glob("*/campaign.json")
    ):
        _process(path, ("campaign_config",), delta=False, apply=args.apply, tally=templates)

    snapshots.report(
        "Minted snapshots (campaigns/*/campaign.json::config) — rewritten as a delta",
        "re-stamped" if args.apply else "would re-stamp",
    )
    templates.report(
        "Dataset templates (datasets/*/campaign.json::campaign_config) — pruned only",
        "pruned" if args.apply else "would prune",
    )

    gone = snapshots.gone + templates.gone
    if gone:
        print("\nDropped — knobs the engine no longer has, and the value each file held:")
        for entry, n in gone.most_common():
            print(f"  {n:4d}x  {entry}")

    failed = snapshots.failed + templates.failed
    skipped = snapshots.skipped + templates.skipped
    if not args.apply and (snapshots.rewritten or templates.rewritten):
        print("\nDry run. Re-run with --apply to rewrite.")
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
