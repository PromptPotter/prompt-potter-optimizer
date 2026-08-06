"""Measure candidate inner-bank draws before putting one on a panel. A fenced debug diagnostic — no config field, no L1
injection, no ledger event; the loop never learns this verb exists."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.seed_screen import SeedScreenError, screen_inner_seeds
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_seed_screen(args: argparse.Namespace) -> CommandResult:
    """Score each candidate seed's bank with the dataset origin and the strongest banked
    configuration, and report what separates them."""
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    stores = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)

    try:
        outcome = await screen_inner_seeds(
            stores=stores,
            identity=identity,
            dataset_name=args.dataset,
            seeds=list(args.seeds),
            n_samples=args.n_samples,
            repeat=args.repeat,
            log=logger.info if get_verbose() else None,
        )
    except SeedScreenError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    # Sorted by reasoning margin, and DISQUALIFIED banks first regardless of it: a bank where
    # collapse outscores the origin is not a weak cell to rank, it is one to reject.
    rows = sorted(outcome.readings, key=lambda r: (not r.rewards_collapse, -r.reasoning_margin))
    # Speed and cost ride the same row as the margin, because choosing a target model is one
    # decision over all three and reading them from separate places is how a model gets picked
    # on quality it cannot afford. Median and mean are both shown: a gap between them is a
    # route stalling, which no single number says.
    table = "\n".join(
        f"  seed {r.seed:<4d} {'REWARDS COLLAPSE' if r.rewards_collapse else 'ok              '}"
        f"  margin {r.reasoning_margin:+.3f} +/-{r.margin_se:.3f}"
        f"  {'settled' if r.verdict_settled else 'UNSETTLED'}"
        f"  origin {r.origin_accuracy:.3f} (spread {r.origin_spread:.3f} over {len(r.origin_reads)})"
        f"  hedge {'--' if r.answer_modal_share is None else f'{r.answer_modal_share:.0%}'}"
        f"  floor {r.class_floor:.3f}"
        f"  {r.latency_median:.1f}s med/{r.latency_mean:.1f}s mean"
        f"  ${r.cost_per_pass:.4f}/pass"
        for r in rows
    )
    # A rejection requires a SETTLED margin. The floor is exact; the origin is not, so near the
    # line the sign of the margin is just the sign of one noisy read — and printing REJECT off
    # that is how this tool condemned seed-5 on a margin its own second read reversed. A
    # suspected bank is NAMED, never rejected.
    bad = [r.seed for r in rows if r.rewards_collapse and r.verdict_settled]
    suspect = [r.seed for r in rows if r.rewards_collapse and not r.verdict_settled]
    unsettled = [r.seed for r in rows if not r.verdict_settled]
    verdict = (
        f"REJECT {bad} — a candidate that stops reasoning and answers one label outscores the "
        f"origin there, by more than the measurement's own error bar.\n"
        if bad
        else "No bank rewards collapse on settled evidence.\n"
    ) + (
        f"SUSPECT {suspect} — margin negative but inside 2 SE; raise --repeat before acting.\n"
        if suspect
        else ""
    )
    passes = len(rows[0].origin_reads)
    human = (
        f"seed-screen {outcome.dataset_name}: {len(rows)} seed(s), {passes} origin pass(es) each"
        f" over {rows[0].n} rows.\n{verdict}{table}\n"
        # A verdict inside its own error bar is not a verdict — say so, rather than let a reader
        # take the sign of a near-zero margin at face value.
        + (
            f"UNSETTLED (margin within 2 SE): {unsettled} — re-run those at a higher --repeat "
            f"before acting on their sign.\n"
            if unsettled
            else ""
        )
        + f"Record → {outcome.artifact_path}"
    )
    return CommandResult(data={"readings": [r.as_dict() for r in rows]}, human=human)


__all__ = ["cmd_seed_screen"]
