"""Render ``review.md`` for any finished cycle dir, on demand — the offline twin of
``application/output.py::write_review_md``: same renderer, same typed round
shape, same frozen config snapshot, so a backfilled review.md agrees with a live one."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.review_md import render_review_md
from promptpotter.domain.results import RoundResult
from promptpotter.infrastructure.projections.audit_trail import load_round_audits


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cycle_dir", type=Path, help="campaigns/{campaign_id}/cycles/{cycle_id}")
    args = parser.parse_args(argv)

    cycle_dir: Path = args.cycle_dir.resolve()
    if not (cycle_dir / "index.json").exists():
        print(f"no index.json at {cycle_dir}", file=sys.stderr)
        return 2

    index = json.loads((cycle_dir / "index.json").read_text(encoding="utf-8"))
    rounds = [
        RoundResult.model_validate(json.loads(f.read_text(encoding="utf-8")))
        for f in sorted((cycle_dir / "rounds").glob("round_*.json"))
    ]
    audits = load_round_audits(cycle_dir, [r.round for r in rounds])

    # The task-context strings ride the last persisted OptSearchPoint — the offline
    # stand-in for the live path's ``cycle.opt_sp.memory.task_context``.
    context_object: list[str] = []
    for r in reversed(rounds):
        if r.opt_sp is not None:
            td = r.opt_sp.memory.task_context
            context_object = [td.pipeline_purpose, td.optimization_goals, td.key_challenges]
            break

    # The SAME frozen snapshot the live renderer reads (``cycle.config``). Fails loud
    # when the cycle dir sits outside a campaign tree — that input is unsupported.
    manifest = json.loads((cycle_dir.parent.parent / "campaign.json").read_text(encoding="utf-8"))
    l1_patience = load_campaign_config(manifest["config"]).optimization.l1_patience

    content = render_review_md(
        index,
        rounds,
        round_audits=audits,
        context_object=context_object,
        l1_patience=l1_patience,
    )
    out_path = cycle_dir / "review.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path} ({len(rounds)} rounds, {sum(1 for a in audits if a)} audits)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
