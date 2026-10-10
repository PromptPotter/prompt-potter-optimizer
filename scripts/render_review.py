from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.application.scoring.cells import closed_rounds
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_spend
from promptpotter.infrastructure.store.layout import CampaignLayout
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.identity import default_identity


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cycle_dir", type=Path, help="campaigns/{campaign_id}/cycles/{cycle_id}")
    args = parser.parse_args(argv)

    cycle_dir: Path = args.cycle_dir.resolve()
    complete_registries()

    manifest_path = CampaignLayout(cycle_dir.parent.parent).manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = load_campaign_config(manifest["config"])

    # ``projects/{tenant}/campaigns/{id}/cycles/{cycle}``: the tenant is three levels up.
    tenant_dir = cycle_dir.parents[3]
    stores = build_stores(default_identity(tenant_dir.name), projects_root=tenant_dir.parent)
    td = campaign_framing(stores, config, manifest["dataset_name"])
    context_object = [td.pipeline_purpose, td.optimization_goals, td.key_challenges]
    hop = CycleHop(campaign_id=cycle_dir.parent.parent.name, cycle_id=cycle_dir.name)
    index = stores.campaigns.load(hop)
    if index is None:
        print(f"no cycle was minted at {cycle_dir}", file=sys.stderr)
        return 2
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    assert campaign is not None
    own = scorer_of(resolve_campaign_config(stores, campaign, hop), verifier_graded=False)
    rounds = closed_rounds(stores, hop, own)
    audits = load_round_audits(cycle_dir, [r.round for r in rounds])

    content = render_review_md(
        index,
        rounds,
        round_audits=audits,
        context_object=context_object,
        accuracy_ceiling=config.accuracy_ceiling,
        optimizer=select_optimizer(config.optimization),
        bench=read_cycle_bench(stores, campaign, hop),
        spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir))).spend,
    )
    out_path = cycle_dir / "review.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path} ({len(rounds)} rounds, {sum(1 for a in audits if a)} audits)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
