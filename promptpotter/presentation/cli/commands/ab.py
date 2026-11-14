"""A replay, never a second run: candidate generation is non-deterministic, so two runs are no A/B."""

from __future__ import annotations

import argparse

from promptpotter.application.account import local_stores
from promptpotter.application.bench.resume_and_fork.ab_replay import ab_replay_cycle
from promptpotter.application.cycle_addressing import launched, named_campaign, resolve_cycle_id
from promptpotter.application.initialization.scoring_scope import open_diagnostic
from promptpotter.config.logging import setup_logging
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.commands.result import CommandResult


async def cmd_ab(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = local_stores(tenant=args.tenant)

    def target() -> CycleHop:
        if args.campaign:
            root = named_campaign(stores, args.campaign).root_hop
            if args.cycle:
                cycle_id = resolve_cycle_id(stores, root.campaign_id, args.cycle)
                return CycleHop(campaign_id=root.campaign_id, cycle_id=cycle_id)
            return root
        hop = launched(stores)
        if not args.cycle:
            return hop
        return CycleHop(
            campaign_id=hop.campaign_id,
            cycle_id=resolve_cycle_id(stores, hop.campaign_id, args.cycle),
        )

    hop = target()
    async with open_diagnostic(stores, hop) as opened:
        report = ab_replay_cycle(hop, opened.session, opened.config)

        scope = f"campaign {report.campaign_id} — {report.n_cycles} cycle(s), {report.n_rounds} round(s)"
        if not report.divergences:
            human = (
                f"A/B replay: {scope} re-derived IDENTICALLY under the current engine + scorer "
                f"'{report.scorer_id}'. Nothing departs: every measurement in this lineage still "
                "carries over, so a change of this size needs no fork."
            )
        else:
            lines = [
                f"A/B replay: {scope} under scorer '{report.scorer_id}' — the change departs the "
                f"record at {len(report.divergences)} point(s), leaving {len(report.divergent)} "
                "round(s) counterfactual. Fork at each departure to carry the rest forward:"
            ]
            for d in report.divergences:
                alt = (
                    f" → would elect {d.alternative_candidate_id}"
                    if d.alternative_candidate_id
                    else ""
                )
                lines.append(f"  {d.cycle_id}::r{d.round}{alt}")
            lines.append(
                f"  ({report.n_mismatches} decision(s) re-derived differently up to those points; "
                "beyond them nothing was replayed, because it describes a history that would not "
                "have happened.)"
            )
            lines += [
                f"    round {d.round_num} [{d.kind}]: recorded={d.recorded_outcome!r} "
                f"→ current={d.current_outcome!r}"
                for d in report.mismatches
            ]
            human = "\n".join(lines)

        return CommandResult(data=report.to_dict(), human=human)
