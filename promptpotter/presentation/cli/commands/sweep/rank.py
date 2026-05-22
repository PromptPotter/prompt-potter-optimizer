"""``sweep rank`` — read sweep results from disk and print a sorted table."""

from __future__ import annotations

import argparse

from promptpotter.presentation.cli.commands._shared import CommandResult


async def _cmd_sweep_rank(args: argparse.Namespace) -> CommandResult:
    """Read sweep results from disk and print a sorted table. Pure
    read — no optimize call, no LLM spend."""
    from promptpotter.application.sweep import find_sweep_results, rank_sweep_results
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tenant_id=getattr(args, "tenant", "default"))
    results = find_sweep_results(
        stores.base_dir,
        dataset=args.dataset,
        verb=args.filter_verb,
    )
    if not results:
        return CommandResult(
            data={"results": []},
            human=(
                f"No sweep results under {stores.base_dir / 'archive' / 'sweeps'}"
                + (f" for dataset={args.dataset}" if args.dataset else "")
                + (f" verb={args.filter_verb}" if args.filter_verb else "")
            ),
        )
    table = rank_sweep_results(
        results, by=args.rank_by, last=args.last, ascending=bool(args.ascending)
    )
    return CommandResult(
        data={"results": results[: args.last or len(results)], "by": args.rank_by},
        human=table,
    )
