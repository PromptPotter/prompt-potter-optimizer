from __future__ import annotations

import argparse

from promptpotter.application.cycle_addressing import resolve_campaign_id_hint
from promptpotter.application.evidence.metric_catalogue import MEASURAND
from promptpotter.application.evidence.read import select_evidence
from promptpotter.application.evidence.subjects import parse_subject
from promptpotter.application.views.evidence import evidence_lines
from promptpotter.config.logging import setup_logging
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


async def cmd_evidence(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    subjects = [
        spec._replace(
            campaign_id=resolve_campaign_id_hint(stores, spec.campaign_id, inside=spec.inside)
        )
        for spec in map(parse_subject, args.subject)
    ]
    ev = select_evidence(
        stores,
        subjects=subjects,
        dataset=args.dataset or "",
        grid=args.grid or "",
        include_ranking=args.ranking,
        include_winner_chain=args.winner_chain,
        include_config=args.config,
        metric=args.metric or MEASURAND,
    )
    return CommandResult(data=ev.model_dump(), human="\n".join(evidence_lines(ev, top=args.top)))
