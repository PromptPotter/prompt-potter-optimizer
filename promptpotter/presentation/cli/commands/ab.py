"""Deterministic A/B replay of a campaign under the CURRENT engine + scorer; zero LLM calls. The honest
engine/scorer A/B, since running a campaign twice cannot be one — candidate generation is non-deterministic."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.diagnostics.ab import ab_replay_campaign
from promptpotter.application.optimization.resume_and_fork.ab_replay import AbReplayError
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
    resolve_campaign,
    resolve_cycle,
)
from promptpotter.presentation.cli.session import no_dataset_hint

logger = logging.getLogger("promptpotter.presentation.cli")


def _target(args: argparse.Namespace, stores: Stores) -> CycleHop:
    """A named campaign replays from its ROOT cycle unless ``--cycle`` says otherwise; no ``--campaign`` is the active
    pointer's pair, never half of it — the pointer's cycle belongs to the pointer's campaign."""
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
    _sid, campaign_id, cycle_id = read_active_pointer(stores.base_dir)
    if not campaign_id:
        raise SystemExit(
            "ERROR: no active campaign — pass --campaign, or start one:\n\n" + no_dataset_hint()
        )
    return CycleHop(
        campaign_id=campaign_id,
        cycle_id=resolve_cycle(stores, campaign_id, args.cycle) if args.cycle else cycle_id,
    )


async def cmd_ab(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    stores = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    try:
        report = await ab_replay_campaign(
            stores=stores,
            identity=identity,
            hop=_target(args, stores),
            log=logger.info if get_verbose() else None,
        )
    except AbReplayError as exc:
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
