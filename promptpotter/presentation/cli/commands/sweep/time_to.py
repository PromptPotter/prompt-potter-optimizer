"""``sweep time-to N`` — run optimize, halt on target / max-rounds / max-spend."""

from __future__ import annotations

import argparse
import logging

from promptpotter.domain.phases import StopReason
from promptpotter.presentation.cli.commands._shared import _DIVERGENCE_HINT, CommandResult
from promptpotter.presentation.cli.commands.sweep._common import (
    _parse_variants,
    _resolve_template,
    _run_sweep_optimize,
    _setup_sweep_cycle,
    _variant_id_fields,
)
from promptpotter.presentation.cli.session import load_session

logger = logging.getLogger("promptpotter.presentation.cli")


async def _cmd_sweep_time_to(args: argparse.Namespace) -> CommandResult:
    """`sweep time-to N` — run optimize, halt on target / max-rounds /
    max-spend, write one result JSON.

    The cycle runs against whatever session+cycle the active pointer
    names — operator runs ``init`` (or implicit auto-mint) first.
    ``--max-rounds`` overrides ``campaign.json::optimization.max_rounds``
    for this sweep only; the override stays in-memory.
    """
    from promptpotter.application.sweep import (
        build_sweep_result,
        current_optimizer_prompt_hash,
        optimizer_prompt_override,
        slice_samples,
        write_sweep_result,
    )
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.spend import refresh_rates

    refresh_rates()

    campaign_config = load_session(args).campaign_config
    target_acc = args.target / 100.0
    max_rounds = int(args.max_rounds)
    spend_budget = float(args.spend_budget) if args.spend_budget is not None else None
    sweep_opt = campaign_config.optimization.model_copy(update={"max_rounds": max_rounds})
    campaign_config = campaign_config.model_copy(update={"optimization": sweep_opt})

    train_data, ctx, session = await _setup_sweep_cycle(args, campaign_config)
    if train_data is None:
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data, resolved_slice = slice_samples(
        train_data,
        args.slice_spec,
        stores=session.store,
        backend_id=session.backend_id,
        dataset_name=session.dataset_name,
    )
    variants = _parse_variants(args)
    if len(variants) > 1:
        raise SystemExit("sweep time-to: only one L1 variant per invocation")
    variant = variants[0]
    template = _resolve_template(variant)

    logger.info(
        "Sweep time-to %d%%: max_rounds=%d spend_budget=%s slice=%s l1=%s",
        args.target,
        max_rounds,
        f"${spend_budget:.2f}" if spend_budget is not None else "uncapped",
        resolved_slice,
        variant.label or "current",
    )

    try:
        with optimizer_prompt_override(variant.node, template):
            cycle_result, observers = await _run_sweep_optimize(
                args,
                ctx,
                campaign_config,
                session,
                train_data,
                halt_at_accuracy=target_acc,
                spend_budget_usd=spend_budget,
            )
    except ResumeDivergenceError as div:
        return CommandResult(
            data={"error": "resume_divergence", "round": div.round_num},
            human=f"{div}\n\n{_DIVERGENCE_HINT}",
        )

    spend_usd = observers.dashboard.spend_total_used_usd
    early_exit = cycle_result.stop_reason
    target_hit = early_exit == StopReason.TARGET_HIT

    result = build_sweep_result(
        verb="time-to",
        dataset=ctx.init_params["dataset_name"] or "unknown",
        l1_meta_prompt_hash=current_optimizer_prompt_hash(),
        **_variant_id_fields(variant),
        cycle_id=cycle_result.cycle_id,
        slice_name=resolved_slice,
        rounds_to_target=cycle_result.n_l1_rounds if target_hit else None,
        early_exit_reason=early_exit,
        cost_usd=spend_usd,
        final_accuracy=cycle_result.best_accuracy,
    )
    out_path = write_sweep_result(session.store.base_dir, result, target=args.target)

    return CommandResult(
        data={**result, "result_path": str(out_path), "cycle_id": cycle_result.cycle_id},
        human=(
            f"Sweep time-to {args.target}%: {early_exit} "
            f"(rounds={cycle_result.n_l1_rounds}, best={cycle_result.best_accuracy:.3f}, "
            f"spend=${spend_usd:.4f})\n"
            f"Result: {out_path}"
        ),
    )
