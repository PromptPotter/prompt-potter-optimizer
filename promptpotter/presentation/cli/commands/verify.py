"""``cmd_verify`` — re-score one campaign candidate on MORE samples. Not a cycle or a fork: no ledger event, no round id, and
persistence lands in the workspace ``diagnostics/`` tree only."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.diagnostics.verify import VerifyError, verify_candidate
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import parse_candidate_label
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
    resolve_campaign,
    resolve_cycle,
)

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    """Re-score a campaign candidate on N additional samples; persist the workspace verdict."""

    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    stores = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id = resolve_campaign(stores, args.campaign)
    cycle_id = resolve_cycle(stores, campaign_id, args.cycle)
    try:
        round_num, cand_idx = parse_candidate_label(args.label)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from None

    try:
        outcome = await verify_candidate(
            stores=stores,
            identity=identity,
            hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
            round_num=round_num,
            cand_idx=cand_idx,
            label=args.label,
            samples=args.samples,
            seed=args.seed,
            log=logger.info if get_verbose() else None,
        )
    except VerifyError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    if outcome.record is None:
        return CommandResult(
            human=(
                f"{args.label}: every sample in the {outcome.dataset_name} bank is "
                f"already measured for this config ({outcome.already_measured} total). "
                "Nothing to add."
            ),
        )

    record = outcome.record
    human = (
        f"{args.label}: acc {fmt_pct(record.source_campaign_accuracy, '{:.3f}')}"
        f"→{record.workspace_accuracy:.3f} "
        f"(cf {record.source_campaign_composite:.3f}→{record.workspace_composite:.3f}) "
        f"on {record.workspace_n} samples (+{record.samples_added} new from "
        f"{record.source_campaign_n} in campaign"
        + (f", {outcome.cache_replays} cache-replay" if outcome.cache_replays else "")
        + ")."
    )
    return CommandResult(data=record.model_dump(), human=human)


__all__ = ["cmd_verify"]
