"""``cmd_ab`` — deterministic A/B replay of the active cycle.

Re-derives every recorded decision (round winner, PoBB eliminations, L2/L3 triggers) under
the CURRENT engine + active scorer over the recorded measurements, and reports where they
flip vs what is on disk. Zero LLM calls. The honest engine/scorer A/B (running a campaign
twice can't be — candidate generation is non-deterministic). Contract: ``ab_replay.py``.
"""

from __future__ import annotations

import argparse
import logging

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    bind_session_identity,
    get_verbose,
    identity_from_args,
    init_services_cli,
)
from promptpotter.presentation.cli.session import load_session

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_ab(args: argparse.Namespace) -> CommandResult:
    """Re-derive the active cycle's recorded decisions under the current engine; report flips."""
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.initialization.loop_start import populate_session_scoring
    from promptpotter.application.optimization.resume_and_fork.ab_replay import ab_replay_cycle
    from promptpotter.application.scoring.formula import split_scoring_block

    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params, identity=identity_from_args(args))
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
    )

    report = ab_replay_cycle(
        session.store.campaigns,
        CycleHop(campaign_id=session.campaign_id, cycle_id=ctx.cycle_id),
        session,
        campaign_config.optimization.elimination_n_min,
        enable_2pl=campaign_config.optimization.enable_2pl_graduation,
    )

    if report.n_divergences == 0:
        human = (
            f"A/B replay: cycle {ctx.cycle_id} — {report.n_rounds} round(s) re-derived "
            f"IDENTICALLY under the current engine + scorer '{report.scorer_id}'. No decision "
            "flips: the engines agree on this cycle's recorded measurements."
        )
    else:
        lines = [
            f"A/B replay: cycle {ctx.cycle_id} — {report.n_divergences} divergence(s) across "
            f"{report.n_rounds} round(s) under scorer '{report.scorer_id}':"
        ]
        lines += [
            f"  round {d.round_num} [{d.kind}]: recorded={d.recorded_outcome!r} "
            f"→ current={d.current_outcome!r}"
            for d in report.divergences
        ]
        human = "\n".join(lines)

    return CommandResult(data=report.to_dict(), human=human)


__all__ = ["cmd_ab"]
