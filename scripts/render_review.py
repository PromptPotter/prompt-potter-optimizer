"""Render ``review.md`` for any finished cycle dir, on demand — the offline twin of
``application/runner/output.py::write_review_md``: same renderer, same typed round
shape, same frozen config snapshot, so a backfilled review.md agrees with a live one."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.output import read_cycle_spend
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import RoundResult
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.identity import default_identity


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cycle_dir", type=Path, help="campaigns/{campaign_id}/cycles/{cycle_id}")
    args = parser.parse_args(argv)

    cycle_dir: Path = args.cycle_dir.resolve()
    if not (cycle_dir / "index.json").exists():
        print(f"no index.json at {cycle_dir}", file=sys.stderr)
        return 2

    index = json.loads((cycle_dir / "index.json").read_text(encoding="utf-8"))
    complete_registries()
    rounds = [
        RoundResult.model_validate(json.loads(f.read_text(encoding="utf-8")))
        for f in CycleLayout(cycle_dir).round_files()
    ]
    audits = load_round_audits(cycle_dir, [r.round for r in rounds])

    # The SAME frozen snapshot the live renderer reads (``cycle.config``). Fails loud
    # when the cycle dir sits outside a campaign tree — that input is unsupported.
    manifest = json.loads((cycle_dir.parent.parent / "campaign.json").read_text(encoding="utf-8"))
    config = load_campaign_config(manifest["config"])

    # The campaign's framing, read where a run reads it — the live path's ``cycle.framing``.
    # ``projects/{tenant}/campaigns/{id}/cycles/{cycle}``: the tenant is three levels up.
    tenant_dir = cycle_dir.parents[3]
    stores = build_stores(default_identity(tenant_dir.name), projects_root=tenant_dir.parent)
    td = campaign_framing(stores, config, manifest["dataset_name"])
    context_object = [td.pipeline_purpose, td.optimization_goals, td.key_challenges]

    content = render_review_md(
        index,
        rounds,
        round_audits=audits,
        context_object=context_object,
        accuracy_ceiling=config.accuracy_ceiling,
        optimizer=select_optimizer(config.optimization),
        bench=read_cycle_bench(
            stores, CycleHop(campaign_id=cycle_dir.parent.parent.name, cycle_id=cycle_dir.name)
        ),
        spend=read_cycle_spend(CycleLayout(cycle_dir)),
    )
    out_path = cycle_dir / "review.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path} ({len(rounds)} rounds, {sum(1 for a in audits if a)} audits)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
