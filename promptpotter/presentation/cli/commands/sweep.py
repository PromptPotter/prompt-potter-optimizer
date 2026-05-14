"""``cmd_sweep`` — sweep-toolkit subcommands (time-to / round1 / round2 / rank)."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    get_verbose,
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.commands.optimize import (
    _build_observers,
    _prepare_cycle_for_optimize,
)
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig

logger = logging.getLogger("promptpotter.presentation.cli")


def _sweep_early_exit_reason(stop_reason: str) -> str:
    """Map ``StopReason`` → sweep-result ``early_exit_reason`` enum.

    ``target_hit | max_rounds | max_spend`` is the documented surface;
    anything else (patience, interrupt, hard-cap) lands as the raw
    stop_reason string so the operator sees why the sweep didn't bottom
    out on one of the three intentional exits.
    """
    from promptpotter.domain.phases import StopReason

    mapping = {
        StopReason.TARGET_HIT.value: "target_hit",
        StopReason.MAX_ROUNDS.value: "max_rounds",
        StopReason.MAX_SPEND.value: "max_spend",
    }
    return mapping.get(stop_reason, stop_reason)


@dataclass
class _Variant:
    """One L1-meta-prompt variant in a panel iteration."""

    path: Path | None  # None ⇒ use whatever is currently loaded
    label: str | None  # operator-supplied or file stem


def _parse_variants(args: argparse.Namespace) -> list[_Variant]:
    """Resolve ``--l1-prompts`` (plural, round1/round2) or ``--l1-prompt``
    (singular, time-to). When neither is given returns a single
    ``_Variant(path=None)`` so the verb runs against the currently
    loaded L1 in one iteration."""
    label_override = getattr(args, "l1_prompt_label", None)
    raw = getattr(args, "l1_prompts", None) or getattr(args, "l1_prompt", None)
    # NB: argparse declares only ONE of --l1-prompts / --l1-prompt per subparser
    # (see _add_sweep_l1_prompt_args), so the unused attribute is never set on
    # `args`. getattr-with-default is the correct contract here, not a shim.
    if not raw:
        return [_Variant(path=None, label=label_override)]
    paths = [Path(p.strip()) for p in str(raw).split(",") if p.strip()]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"L1 prompt file(s) not found: {', '.join(missing)}")
    return [
        _Variant(path=p, label=label_override if len(paths) == 1 else None or p.stem) for p in paths
    ]


def _resolve_l1_template(variant: _Variant) -> Any:
    """Load PromptTemplate from variant's path (or None for current loader)."""
    if variant.path is None:
        return None
    from promptpotter.application.sweep import load_l1_prompt_from_path

    return load_l1_prompt_from_path(variant.path)


async def _setup_sweep_cycle(
    args: argparse.Namespace,
    campaign_config: CampaignConfig,
) -> Any:
    """Init services + resolve cycle for a sweep verb. Returns the bundle
    (ctx, session, train_data, pipeline_params) shared by every verb."""
    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return None, ctx, session

    train_data = session.samples or []
    pipeline_params, _origin = _prepare_cycle_for_optimize(
        args, ctx, session, campaign_config, train_data
    )
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    return train_data, ctx, session


async def _run_sweep_optimize(
    args: argparse.Namespace,
    ctx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list,
    *,
    halt_at_accuracy: float | None = None,
    max_spend_usd: float | None = None,
) -> tuple[Any, Any]:
    """Drive ``run_optimization`` for any sweep verb — honors
    ``halt_at_accuracy`` / ``max_spend_usd`` and the active L1 override
    (managed by the caller via :func:`l1_generate_override`). Returns
    ``(cycle_result, observers)``."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        resume_from_round_override=getattr(args, "resume_from_round", None),
        no_divergence_check=False,
        fork_on_divergence=False,
        sweep=False,
        diag=False,
        halt_at_accuracy=halt_at_accuracy,
        max_spend_usd=max_spend_usd,
    )
    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    return cycle_result, observers


def _panel_stats_from_round(round_result: Any, panel_size: int) -> dict[str, float]:
    """Pull per-candidate accuracy + pipeline_params from a ``RoundResult``
    and route through :func:`compute_panel_stats`."""
    from promptpotter.application.sweep import compute_panel_stats

    scores = list(round_result.candidate_scores or [])
    accuracies = [float(s.get("accuracy") or 0.0) for s in scores]
    pps = [s.get("pipeline_params_override") or {} for s in scores]
    return compute_panel_stats(
        candidates_planned=panel_size,
        candidate_accuracies=accuracies,
        candidate_pipeline_params=pps,
    )


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
        current_l1_meta_prompt_hash,
        l1_generate_override,
        slice_samples,
        write_sweep_result,
    )
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    campaign_config = load_session(args).campaign_config
    target_acc = args.target / 100.0
    max_rounds = int(args.max_rounds)
    max_spend = float(args.max_spend) if args.max_spend is not None else None
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
    )
    variants = _parse_variants(args)
    if len(variants) > 1:
        raise SystemExit("sweep time-to: only one L1 variant per invocation")
    variant = variants[0]
    l1_template = _resolve_l1_template(variant)

    logger.info(
        "Sweep time-to %d%%: max_rounds=%d max_spend=%s slice=%s l1=%s",
        args.target,
        max_rounds,
        f"${max_spend:.2f}" if max_spend is not None else "uncapped",
        resolved_slice,
        variant.label or "current",
    )

    try:
        with l1_generate_override(l1_template):
            cycle_result, observers = await _run_sweep_optimize(
                args,
                ctx,
                campaign_config,
                session,
                train_data,
                halt_at_accuracy=target_acc,
                max_spend_usd=max_spend,
            )
    except ResumeDivergenceError as div:
        return CommandResult(
            data={"error": "resume_divergence", "round": div.round_num},
            human=f"{div}\n\n{_DIVERGENCE_HINT}",
        )

    spend_state = observers.dashboard.state.get("spend") or {}
    spend_usd = float(spend_state.get("total_used_usd") or 0.0)
    early_exit = _sweep_early_exit_reason(cycle_result.stop_reason)
    target_hit = early_exit == "target_hit"

    result = build_sweep_result(
        verb="time-to",
        dataset=ctx.init_params["dataset_name"] or "unknown",
        l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
        l1_meta_prompt_label=variant.label,
        l1_prompt_path=str(variant.path) if variant.path else None,
        cycle_id=cycle_result.cycle_id,
        slice_name=resolved_slice,
        rounds_to_target=cycle_result.n_rounds if target_hit else None,
        early_exit_reason=early_exit,
        cost_usd=spend_usd,
        final_accuracy=cycle_result.best_accuracy,
    )
    out_path = write_sweep_result(session.store.base_dir, result, target=args.target)

    return CommandResult(
        data={**result, "result_path": str(out_path), "cycle_id": cycle_result.cycle_id},
        human=(
            f"Sweep time-to {args.target}%: {early_exit} "
            f"(rounds={cycle_result.n_rounds}, best={cycle_result.best_accuracy:.3f}, "
            f"spend=${spend_usd:.4f})\n"
            f"Result: {out_path}"
        ),
    )


def _mint_toolkit_batch_id() -> str:
    """Cycle-id-safe batch id for OPERATOR_SWEEP forks: ``b{ts}{hex}``,
    no underscores (the cycle-id regex parses on ``_sweep_{batch_id}_``).
    """
    import secrets
    from datetime import UTC
    from datetime import datetime as _dt

    return "b" + _dt.now(UTC).strftime("%Y%m%dT%H%M%SZ") + secrets.token_hex(2)


async def _fork_and_bind(
    args: argparse.Namespace,
    parent_ctx,
    sweep_id: str,
    batch_id: str,
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Session, str]:
    """Mint an OPERATOR_SWEEP fork off ``parent_ctx.cycle_id``, init a
    fresh session bound to it, return ``(fork_ctx, fork_session, fork_cycle_id)``.

    Mirrors the per-fork setup in ``application/sweep.sweep_runner`` —
    the active pointer is retargeted by ``_mint_fork`` and the caller
    must restore it after the loop.
    """
    from promptpotter.application.bootstrap import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.resume_and_fork import _mint_fork
    from promptpotter.domain.run_records import ForkPayload, ForkTrigger
    from promptpotter.infrastructure.store import build_stores

    tenant_id = getattr(args, "tenant", "default")
    store = build_stores(tenant_id=tenant_id)
    source_file = variant.path.name if variant.path else f"current-{variant.label or 'unset'}.json"
    fork_payload = ForkPayload(
        trigger=ForkTrigger.OPERATOR_SWEEP,
        reason=f"sweep-toolkit:{sweep_id}",
        issued_by=tenant_id,
    )
    new_cycle_id = _mint_fork(
        store.campaigns,
        tenant_id,
        parent_ctx.session_id,
        parent_ctx.cycle_id,
        0,
        fork_payload,
        sweep_batch_id=batch_id,
        sweep_source_file=source_file,
    )
    fork_ctx = load_session(args)
    fork_session = await init_services(
        **fork_ctx.init_params,
        tenant_id=tenant_id,
        on_status=lambda msg: logger.info(msg) if get_verbose() else None,
    )
    fork_session.session_id = fork_ctx.session_id
    fork_session.state.cycle_id = fork_ctx.cycle_id
    configure_and_apply_pipeline(
        fork_session,
        campaign_config,
        log=logger.info if get_verbose() else (lambda *_a, **_k: None),
    )
    return fork_ctx, fork_session, new_cycle_id


async def _run_one_panel_variant(
    args: argparse.Namespace,
    ctx,
    session: Session,
    train_data: list,
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Any]:
    """Run one optimize cycle with ``variant``'s L1 template applied.
    Returns ``(cycle_result, observers)``."""
    from promptpotter.application.sweep import l1_generate_override

    template = _resolve_l1_template(variant)
    with l1_generate_override(template):
        return await _run_sweep_optimize(args, ctx, campaign_config, session, train_data)


def _variant_to_result(
    *,
    verb: str,
    variant: _Variant,
    cycle_result: Any,
    observers: Any,
    panel_size: int,
    sweep_id: str,
    dataset: str,
    resolved_slice: str,
    prior_round1_acc: float | None,
) -> dict[str, Any]:
    """Compose one sweep-result dict from a finished panel run."""
    from promptpotter.application.sweep import (
        build_sweep_result,
        current_l1_meta_prompt_hash,
    )

    rounds = list(cycle_result.rounds or [])
    spend_state = observers.dashboard.state.get("spend") or {}
    spend_usd = float(spend_state.get("total_used_usd") or 0.0)
    if not rounds:
        return build_sweep_result(
            verb=verb,
            dataset=dataset,
            l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
            l1_meta_prompt_label=variant.label,
            l1_prompt_path=str(variant.path) if variant.path else None,
            cycle_id=cycle_result.cycle_id,
            sweep_id=sweep_id,
            slice_name=resolved_slice,
            panel_size=panel_size,
            cost_usd=spend_usd,
            final_accuracy=cycle_result.best_accuracy,
            notes=f"no rounds; stop_reason={cycle_result.stop_reason}",
        )

    r1 = _panel_stats_from_round(rounds[0], panel_size)
    fields: dict[str, Any] = {
        "panel_size": panel_size,
        "round1_accuracy": r1["round1_accuracy"],
        "round1_best": r1["round1_best"],
        "parse_fail_rate": r1["parse_fail_rate"],
        "pipeline_params_entropy": r1["pipeline_params_entropy"],
        "cost_usd": spend_usd,
        "final_accuracy": cycle_result.best_accuracy,
    }
    if verb == "round2" and len(rounds) >= 2:
        r2 = _panel_stats_from_round(rounds[1], panel_size)
        fields["round2_accuracy"] = r2["round1_accuracy"]
        anchor = prior_round1_acc if prior_round1_acc is not None else r1["round1_accuracy"]
        fields["round2_lift"] = r2["round1_accuracy"] - anchor

    return build_sweep_result(
        verb=verb,
        dataset=dataset,
        l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
        l1_meta_prompt_label=variant.label,
        l1_prompt_path=str(variant.path) if variant.path else None,
        cycle_id=cycle_result.cycle_id,
        sweep_id=sweep_id,
        slice_name=resolved_slice,
        **fields,
    )


async def _run_panel_verb(
    args: argparse.Namespace,
    *,
    verb: str,
    n_rounds: int,
    variants: list[_Variant],
    variant_priors: dict[str, float] | None = None,
) -> CommandResult:
    """Drive ``round1`` / ``round2`` across one or more variants.

    Single variant + no ``--l1-prompts`` ⇒ runs on the active session
    (no fork). Multi-variant ⇒ mints one OPERATOR_SWEEP fork per
    variant, restores the active pointer at the end.

    ``variant_priors`` (keyed by ``l1_meta_prompt_label`` or path) carries
    the prior round1_accuracy for round2 lift computation when called
    via ``--from-sweep``.
    """
    from promptpotter.application.sweep import slice_samples, write_sweep_result
    from promptpotter.infrastructure.store import save_active_pointer
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    panel_size = int(args.panel_size)
    campaign_config = load_session(args).campaign_config
    sweep_opt = campaign_config.optimization.model_copy(
        update={"max_rounds": n_rounds, "n_variants": panel_size}
    )
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
    )
    dataset = ctx.init_params["dataset_name"] or "unknown"
    sweep_id = __import__(
        "promptpotter.application.sweep", fromlist=["mint_sweep_id"]
    ).mint_sweep_id()
    multi = len(variants) > 1 or (len(variants) == 1 and variants[0].path is not None)
    batch_id = _mint_toolkit_batch_id() if multi else None
    parent_cycle_id = ctx.cycle_id

    results: list[dict[str, Any]] = []
    out_paths: list[Path] = []

    logger.info(
        "Sweep %s: %d variant(s), panel=%d slice=%s n_rounds=%d sweep_id=%s",
        verb,
        len(variants),
        panel_size,
        resolved_slice,
        n_rounds,
        sweep_id,
    )

    for idx, variant in enumerate(variants):
        if multi:
            ctx_v, sess_v, fork_cycle_id = await _fork_and_bind(
                args, ctx, sweep_id, batch_id or "", variant, campaign_config
            )
            logger.info(
                "Sweep %s [%d/%d] variant=%s → fork %s",
                verb,
                idx + 1,
                len(variants),
                variant.label or "current",
                fork_cycle_id,
            )
        else:
            ctx_v, sess_v = ctx, session
        try:
            cycle_result, observers = await _run_one_panel_variant(
                args, ctx_v, sess_v, train_data, variant, campaign_config
            )
        except ResumeDivergenceError as div:
            return CommandResult(
                data={"error": "resume_divergence", "round": div.round_num},
                human=f"{div}\n\n{_DIVERGENCE_HINT}",
            )

        prior = None
        if variant_priors:
            key = variant.label or (str(variant.path) if variant.path else "")
            prior = variant_priors.get(key)

        result = _variant_to_result(
            verb=verb,
            variant=variant,
            cycle_result=cycle_result,
            observers=observers,
            panel_size=panel_size,
            sweep_id=sweep_id,
            dataset=dataset,
            resolved_slice=resolved_slice,
            prior_round1_acc=prior,
        )
        out_path = write_sweep_result(session.store.base_dir, result)
        out_paths.append(out_path)
        results.append({**result, "result_path": str(out_path)})

    if multi:
        # _mint_fork retargeted the active pointer per iteration; put it
        # back so subsequent operator commands hit the parent cycle.
        save_active_pointer(getattr(args, "tenant", "default"), ctx.session_id, parent_cycle_id)

    human_lines = [f"Sweep {verb}: {len(results)} variant(s); sweep_id={sweep_id}"]
    for r in results:
        bits = [
            f"  {r.get('l1_meta_prompt_label') or 'current'}:",
            f"r1={r.get('round1_accuracy') or 0:.3f}",
        ]
        if r.get("round2_accuracy") is not None:
            bits.append(f"r2={r['round2_accuracy']:.3f}")
            bits.append(f"lift={r['round2_lift']:+.3f}")
        bits.append(f"${r.get('cost_usd') or 0:.4f}")
        human_lines.append(" ".join(bits))
    human_lines.extend(f"  → {p}" for p in out_paths)
    return CommandResult(
        data={"sweep_id": sweep_id, "results": results},
        human="\n".join(human_lines),
    )


async def _cmd_sweep_round1(args: argparse.Namespace) -> CommandResult:
    variants = _parse_variants(args)
    return await _run_panel_verb(args, verb="round1", n_rounds=1, variants=variants)


async def _cmd_sweep_round2(args: argparse.Namespace) -> CommandResult:
    """``--from-sweep <sweep_id>`` ⇒ load that sweep's top-K variants
    (by ``round1_accuracy``) and re-run each with 2 rounds, anchoring
    round2_lift against each variant's prior round1. Without
    ``--from-sweep``, falls back to ``--l1-prompts`` / current L1."""
    from promptpotter.application.sweep import find_sweep_results
    from promptpotter.infrastructure.store import build_stores

    if getattr(args, "from_sweep", None):
        stores = build_stores(tenant_id=getattr(args, "tenant", "default"))
        prior = find_sweep_results(stores.base_dir, sweep_id=args.from_sweep, verb="round1")
        if not prior:
            return CommandResult(
                data={"error": "from_sweep_not_found", "sweep_id": args.from_sweep},
                human=f"--from-sweep {args.from_sweep}: no round1 results found.",
            )
        prior.sort(key=lambda r: r.get("round1_accuracy") or 0.0, reverse=True)
        top_k = prior[: max(1, int(args.top_k))]
        variants: list[_Variant] = []
        priors: dict[str, float] = {}
        for row in top_k:
            path_str = row.get("l1_prompt_path")
            path = Path(path_str) if path_str and Path(path_str).exists() else None
            label = row.get("l1_meta_prompt_label") or (path.stem if path else "current")
            variants.append(_Variant(path=path, label=label))
            r1 = row.get("round1_accuracy")
            if isinstance(r1, int | float):
                priors[label] = float(r1)
                if path is not None:
                    priors[str(path)] = float(r1)
        logger.info("round2: anchoring against sweep %s top-%d", args.from_sweep, len(variants))
        return await _run_panel_verb(
            args, verb="round2", n_rounds=2, variants=variants, variant_priors=priors
        )

    variants = _parse_variants(args)
    return await _run_panel_verb(args, verb="round2", n_rounds=2, variants=variants)


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


async def cmd_sweep(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``sweep <verb>`` — ``time-to`` / ``round1`` / ``round2`` /
    ``rank`` per ``docs/specs/m10-prompt-iteration-framework.md``."""
    verb = args.sweep_verb
    if verb == "time-to":
        return await _cmd_sweep_time_to(args)
    if verb == "round1":
        return await _cmd_sweep_round1(args)
    if verb == "round2":
        return await _cmd_sweep_round2(args)
    if verb == "rank":
        return await _cmd_sweep_rank(args)
    raise SystemExit(f"sweep: unknown verb {verb!r}")
