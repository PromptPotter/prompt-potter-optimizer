from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from promptpotter.application.diagnostics.decision_bank import (
    DecisionBankError,
    plan_totals,
    run_decision_bank,
)
from promptpotter.config.logging import setup_logging
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


def _usd(value: float | None) -> str:
    return "unpriced" if value is None else f"${value:.2f}"


def _difference(name: str, reading: dict[str, Any] | None) -> str:
    if reading is None:
        return f"  {name}: no decision pairs it"
    lo, hi = reading["ci"]
    exact_lo, exact_hi = reading["exact_ci"]
    bracket = "--" if lo is None else f"[{lo:+.3f}, {hi:+.3f}]"
    exact = "--" if exact_lo is None else f"[{exact_lo:+.3f}, {exact_hi:+.3f}]"
    return (
        f"  {name}: {reading['mean']:+.3f} {bracket} p={reading['p']} over {reading['n']} "
        f"decision(s) · exact median {reading['exact_median']:+.3f} {exact} p={reading['exact_p']}"
    )


async def cmd_decision_bank(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    try:
        outcome = await run_decision_bank(
            stores=stores,
            dataset_name=args.dataset,
            campaign_ids=list(args.campaigns),
            base={} if args.base is None else read_yaml(Path(args.base)),
            variant=None if args.variant is None else read_yaml(Path(args.variant)),
            seed=args.seed,
            cells=args.cells,
            max_usd=args.max_usd,
            parallel=args.parallel,
        )
    except DecisionBankError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    compared = "the variant" if args.variant is not None else "the base under the next seed"
    head = (
        f"decision-bank {outcome.dataset_name}: {len(outcome.decisions)} decision(s), base vs "
        f"{compared}; not re-derivable: {outcome.skipped or 'none'}."
    )
    data: dict[str, Any] = {
        "decisions": [d.as_dict() for d in outcome.decisions],
        "skipped": outcome.skipped,
    }
    if outcome.reading is None:
        totals = plan_totals(outcome.decisions)
        data["plan"] = totals
        lines = [
            f"  {name}: {t['optimizer_calls']} optimizer call(s) to send "
            f"({_usd(t['optimizer_calls_usd'])} at most), {t['cells']} target cell(s) of which "
            f"{t['cells_replayed']} replay free · at most {_usd(t['bound_usd'])}, "
            f"{_usd(t['measured_usd'])} at what such cells have billed"
            for name, t in zip(("base ", "other"), totals, strict=True)
        ]
        human = "\n".join([head, "DRY RUN — nothing was sent. Pass --max-usd to run it.", *lines])
        return CommandResult(data=data, human=human)

    reading = outcome.reading
    data["reading"] = reading
    human = "\n".join(
        [
            head,
            "Other arm minus base, on each decision's own cells (matched-parent lift in fitness):",
            _difference("best proposal", reading["best_lift"]),
            _difference("mean proposal", reading["mean_lift"]),
            f"  proposed {reading['proposed']} · rejected {reading['rejected']} · empty "
            f"generations {reading['empty_generations']}  (base, other)",
            *([f"STOPPED EARLY: {outcome.stopped}"] if outcome.stopped else []),
            f"Record → {outcome.artifact_path}",
        ]
    )
    return CommandResult(data=data, human=human)


__all__ = ["cmd_decision_bank"]
