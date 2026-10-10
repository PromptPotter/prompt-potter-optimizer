from __future__ import annotations

import argparse

from promptpotter.application.diagnostics.seed_screen import SeedScreenError, screen_inner_seeds
from promptpotter.config.logging import setup_logging
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


async def cmd_seed_screen(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)

    try:
        outcome = await screen_inner_seeds(
            stores=stores,
            identity=stores.identity,
            dataset_name=args.dataset,
            seeds=list(args.seeds),
            n_samples=args.n_samples,
            repeat=args.repeat,
            parallel=args.parallel,
        )
    except SeedScreenError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    rows = outcome.readings
    collapse = {True: "REWARDS COLLAPSE", False: "ok              ", None: "no floor        "}
    settled = {True: "settled  ", False: "UNSETTLED", None: "--       "}
    table = "\n".join(
        f"  seed {r.seed:<4d} {collapse[r.rewards_collapse]}"
        f"  margin {'--    ' if r.reasoning_margin is None else f'{r.reasoning_margin:+.3f}'}"
        f" +/-{r.margin_se:.3f}"
        f"  {settled[r.verdict_settled]}"
        f"  origin {r.origin_accuracy:.3f} (spread {r.origin_spread:.3f} over {len(r.origin_reads)})"
        f"  hedge {'--' if r.answer_modal_share is None else f'{r.answer_modal_share:.0%}'}"
        f"  floor {'--   ' if r.class_floor is None else f'{r.class_floor:.3f}'}"
        f"  {'--' if r.latency_median is None else f'{r.latency_median:.1f}s'} med"
        f"/{'--' if r.latency_mean is None else f'{r.latency_mean:.1f}s'} mean"
        f"  {'--' if r.cost_per_pass is None else f'${r.cost_per_pass:.4f}'}/pass"
        for r in rows
    )
    bad = [r.seed for r in rows if r.verdict == "reject"]
    suspect = [r.seed for r in rows if r.verdict == "suspect"]
    unsettled = [r.seed for r in rows if r.verdict in ("suspect", "unsettled")]
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
