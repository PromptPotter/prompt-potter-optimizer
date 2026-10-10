"""A replay, never a second run: candidate generation is non-deterministic, so two runs are no A/B."""

from __future__ import annotations

import argparse

from promptpotter.application.bench.resume_and_fork.ab_replay import AbReplayError
from promptpotter.application.cycle_listing import active_pointer
from promptpotter.application.diagnostics.ab import ab_replay_campaign
from promptpotter.config.logging import setup_logging
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import BankedSearchPointError
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import (
    open_stores,
    resolve_campaign,
    resolve_cycle,
)
from promptpotter.presentation.cli.session import no_dataset_hint


def _target(args: argparse.Namespace, stores: Stores) -> CycleHop:
    if args.campaign:
        campaign_id = resolve_campaign(stores, args.campaign)
        if args.cycle:
            return CycleHop(
                campaign_id=campaign_id, cycle_id=resolve_cycle(stores, campaign_id, args.cycle)
            )
        campaign = stores.campaigns.load_campaign(campaign_id)
        if campaign is None:
            raise SystemExit(f"ERROR: campaign {campaign_id!r} has no manifest on disk.")
        return campaign.root_hop
    pointer = active_pointer(stores)
    if pointer.campaign_id is None or pointer.cycle_id is None:
        raise SystemExit(
            "ERROR: no active campaign — pass --campaign, or start one:\n\n" + no_dataset_hint()
        )
    return CycleHop(
        campaign_id=pointer.campaign_id,
        cycle_id=(
            resolve_cycle(stores, pointer.campaign_id, args.cycle)
            if args.cycle
            else pointer.cycle_id
        ),
    )


async def cmd_ab(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    try:
        report = await ab_replay_campaign(stores=stores, hop=_target(args, stores))
    except (AbReplayError, BankedSearchPointError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    scope = (
        f"campaign {report.campaign_id} — {report.n_cycles} cycle(s), {report.n_rounds} round(s)"
    )
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
                f" → would elect {d.alternative_candidate_id}" if d.alternative_candidate_id else ""
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


__all__ = ["cmd_ab"]
