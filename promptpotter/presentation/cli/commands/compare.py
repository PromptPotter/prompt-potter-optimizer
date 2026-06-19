"""``cmd_compare`` — PoBB-compare cycle winners across the family."""

from __future__ import annotations

import argparse
import logging

from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    bind_session_identity,
    get_verbose,
    init_services_cli,
)
from promptpotter.presentation.cli.session import load_session

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_compare(args: argparse.Namespace) -> CommandResult:
    """PoBB-compare cycle winners across the family with adaptive top-up."""
    from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.pobb.elevation import (
        discover_compare_arms,
        elevate_to_decisive,
    )
    from promptpotter.application.scoring.formula import split_scoring_block

    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)
    bind_session_identity(session, ctx)

    campaign_config = ctx.campaign_config
    configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if get_verbose() else (lambda *_a, **_k: None)
    )

    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_sample,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        experiment_id=ctx.state.get("experiment_id", ""),
        cycle_id=ctx.cycle_id,
    )

    armset = discover_compare_arms(
        session, ctx.cycle_id, list(args.cycle_ids), all_family=args.all_family
    )
    if armset.error is not None:
        return CommandResult(human=f"ERROR: {armset.error}")

    discover_family = args.all_family or not args.cycle_ids
    result = await elevate_to_decisive(
        armset.arms,
        session,
        session.samples or [],
        epsilon=args.epsilon,
        max_topups=args.max_topups,
        n_min_per_arm=args.n_min_per_arm,
        stream=discover_family or args.max_topups < 0,
    )

    return CommandResult(
        data={
            "decision": result.decision,
            "best_arm": result.best_arm,
            "p_best": result.p_best,
            "topups_per_arm": result.topups_per_arm,
            "score_histories_n": {k: len(v) for k, v in result.score_histories.items()},
            "score_means": {
                k: (sum(v) / len(v) if v else 0.0) for k, v in result.score_histories.items()
            },
        },
        human=result.note,
    )


__all__ = ["cmd_compare"]
