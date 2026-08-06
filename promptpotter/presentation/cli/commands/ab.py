"""Deterministic A/B replay of the active cycle's campaign under the CURRENT engine + scorer; zero LLM calls. The honest
engine/scorer A/B, since running a campaign twice cannot be one — candidate generation is non-deterministic."""

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
        CycleHop(campaign_id=session.campaign_id, cycle_id=ctx.cycle_id),
        session,
        campaign_config.optimization.elimination_n_min,
        enable_2pl=campaign_config.optimization.enable_2pl_graduation,
    )

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
            lines.append(f"  {d.node_key}{alt}")
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
