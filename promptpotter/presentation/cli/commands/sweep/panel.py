"""``sweep round1`` / ``round2`` — panel verbs + their per-variant fork machinery."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.phases import StopReason
from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    get_verbose,
    identity_from_args,
)
from promptpotter.presentation.cli.commands.sweep._common import (
    _panel_stats_from_round,
    _parse_variants,
    _resolve_template,
    _run_sweep_optimize,
    _setup_sweep_cycle,
    _Variant,
    _variant_id_fields,
)
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


def _mint_toolkit_batch_id() -> str:
    """Cycle-id-safe batch id for OPERATOR_SWEEP forks: ``b{ts}{hex}``,
    no underscores (the cycle-id regex parses on ``_sweep_{batch_id}_``).
    """
    import secrets
    from datetime import UTC
    from datetime import datetime as _dt

    return "b" + _dt.now(UTC).strftime("%Y%m%dT%H%M%SZ") + secrets.token_hex(2)


def _base_cycle_id(cycle_id: str) -> str:
    """The campaign's base cycle for a (possibly sweep-forked) cycle id.

    A sweep fork is ``{base}_sweep_{batch_id}_{hash}``. Forking a new sweep
    off another sweep fork compounds the suffix every time, and the cycle dir
    path grows until it trips the OS limit (``WinError 206`` on Windows, where
    MAX_PATH is 260). That happens whenever a prior sweep was interrupted before
    it restored the active pointer, leaving a fork active. A sweep must always
    branch off the base optimization cycle, so strip any sweep suffix first.
    """
    return cycle_id.split("_sweep_", 1)[0]


async def _fork_and_bind(
    args: argparse.Namespace,
    parent_ctx: SessionCtx,
    parent_cycle_id: str,
    sweep_id: str,
    batch_id: str,
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Session, str]:
    """Mint an OPERATOR_SWEEP fork off ``parent_cycle_id``, init a fresh
    session bound to it, return ``(fork_ctx, fork_session, fork_cycle_id)``.

    ``parent_cycle_id`` is the **base** cycle (see :func:`_base_cycle_id`), not
    necessarily ``parent_ctx.cycle_id`` — the campaign/session ids still come
    from ``parent_ctx``. Mirrors the per-fork setup in
    ``application/sweep.sweep_runner`` — the active pointer is retargeted by
    ``_mint_fork`` and the caller must restore it after the loop.
    """
    from promptpotter.application.bootstrap import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.resume_and_fork import _mint_fork
    from promptpotter.domain.run_records import ForkSpec, ForkTrigger
    from promptpotter.infrastructure.store import build_stores

    identity = identity_from_args(args)
    tenant_id = identity.tenant_id
    store = build_stores(identity)
    source_file = variant.path.name if variant.path else f"current-{variant.label or 'unset'}.json"
    fork_payload = ForkSpec(
        trigger=ForkTrigger.OPERATOR_SWEEP,
        reason=f"sweep-toolkit:{sweep_id}",
        issued_by=tenant_id,
    )
    new_cycle_id = _mint_fork(
        store.campaigns,
        parent_ctx.campaign_id,
        tenant_id,
        parent_ctx.session_id,
        parent_cycle_id,
        0,
        fork_payload,
        sweep_batch_id=batch_id,
        sweep_source_file=source_file,
    )
    fork_ctx = load_session(args)
    fork_session = await init_services(
        **fork_ctx.init_params,
        identity=identity,
        on_status=lambda msg: logger.info(msg) if get_verbose() else None,
    )
    fork_session.session_id = fork_ctx.session_id
    fork_session.campaign_id = fork_ctx.campaign_id
    fork_session.state.cycle_id = fork_ctx.cycle_id
    configure_and_apply_pipeline(
        fork_session,
        campaign_config,
        log=logger.info if get_verbose() else (lambda *_a, **_k: None),
    )
    return fork_ctx, fork_session, new_cycle_id


async def _run_one_panel_variant(
    args: argparse.Namespace,
    ctx: SessionCtx,
    session: Session,
    train_data: list[Sample],
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Any]:
    """Run one optimize cycle with ``variant``'s L1 template applied.
    Returns ``(cycle_result, observers)``."""
    from promptpotter.application.sweep import optimizer_prompt_override

    template = _resolve_template(variant)
    with optimizer_prompt_override(variant.node, template):
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
        current_optimizer_prompt_hash,
    )

    rounds = list(cycle_result.rounds or [])
    spend_usd_raw = observers.dashboard.spend_total_used_usd
    # ``StopReason.PAUSED`` (the pause button / Ctrl+C / asyncio cancel) lands
    # the cycle without a fair round-1 reading: either no rounds at all or a
    # partial panel cut mid-candidate. Rendering it as r1=0.000 / $0.0000 falsely
    # ranks the variant as a loser and hides the partial spend. Mark both
    # as None so the print loop shows ``incomplete``.
    interrupted = cycle_result.stop_reason == StopReason.PAUSED
    cost_usd: float | None = None if interrupted else spend_usd_raw
    if not rounds:
        return build_sweep_result(
            verb=verb,
            dataset=dataset,
            l1_meta_prompt_hash=current_optimizer_prompt_hash(),
            **_variant_id_fields(variant),
            cycle_id=cycle_result.cycle_id,
            sweep_id=sweep_id,
            slice_name=resolved_slice,
            panel_size=panel_size,
            cost_usd=cost_usd,
            final_accuracy=None if interrupted else cycle_result.best_accuracy,
            notes=f"no rounds; stop_reason={cycle_result.stop_reason}",
        )

    r1 = _panel_stats_from_round(rounds[0], panel_size)
    fields: dict[str, Any] = {
        "panel_size": panel_size,
        "round1_accuracy": None if interrupted else r1["round1_accuracy"],
        "round1_best": None if interrupted else r1["round1_best"],
        "parse_fail_rate": r1["parse_fail_rate"],
        "pipeline_params_entropy": r1["pipeline_params_entropy"],
        "cost_usd": cost_usd,
        "final_accuracy": None if interrupted else cycle_result.best_accuracy,
    }
    if verb == "round2" and len(rounds) >= 2:
        r2 = _panel_stats_from_round(rounds[1], panel_size)
        r2_acc = r2["round1_accuracy"]
        fields["round2_accuracy"] = r2_acc
        anchor = prior_round1_acc if prior_round1_acc is not None else r1["round1_accuracy"]
        # A panel that received no candidate has no accuracy — so it has no lift either.
        fields["round2_lift"] = None if (r2_acc is None or anchor is None) else r2_acc - anchor

    return build_sweep_result(
        verb=verb,
        dataset=dataset,
        l1_meta_prompt_hash=current_optimizer_prompt_hash(),
        **_variant_id_fields(variant),
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

    refresh_rates()

    panel_size = int(args.panel_size)
    campaign_config = load_session(args).campaign_config
    opt_update: dict[str, Any] = {"max_rounds": n_rounds, "n_variants": panel_size}
    if any(v.node == "l2_context" for v in variants):
        # An l2_context sweep must exercise l2 to measure it. l2 fires on L1
        # stall — force l1_patience=0 so L1 stalls after round 1 and l2 fires
        # (and is conformance-scored) in round 2 of the cheap 2-round loop.
        opt_update["l1_patience"] = 0
    sweep_opt = campaign_config.optimization.model_copy(update=opt_update)
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
        dataset_name=session.dataset_name,
    )
    dataset = ctx.init_params["dataset_name"] or "unknown"
    sweep_id = __import__(
        "promptpotter.application.sweep", fromlist=["mint_sweep_id"]
    ).mint_sweep_id()
    multi = len(variants) > 1 or (len(variants) == 1 and variants[0].path is not None)
    batch_id = _mint_toolkit_batch_id() if multi else None
    # Fork off the BASE cycle, never the active one — the active pointer may be a
    # leftover sweep fork from an interrupted prior sweep, and forking off it
    # compounds the cycle-id suffix past the OS path limit (see _base_cycle_id).
    parent_cycle_id = _base_cycle_id(ctx.cycle_id)

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
                args, ctx, parent_cycle_id, sweep_id, batch_id or "", variant, campaign_config
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
        # back so subsequent operator commands hit the parent cycle. The
        # sweep forks stay in the parent's campaign, so the restore keeps
        # the same campaign_id. dashboard.json no longer carries cycle_id
        # (active identity lives only in active_session.json), so the
        # pointer restore is sufficient — no sidecar dashboard rewrite.
        save_active_pointer(
            session.identity.tenant_id,
            ctx.session_id,
            ctx.campaign_id,
            parent_cycle_id,
        )

    human_lines = [f"Sweep {verb}: {len(results)} variant(s); sweep_id={sweep_id}"]
    for r in results:
        r1_val = r.get("round1_accuracy")
        cost_val = r.get("cost_usd")
        bits = [
            f"  {r.get('l1_meta_prompt_label') or 'current'}:",
            f"r1={r1_val:.3f}" if r1_val is not None else "r1=incomplete",
        ]
        if r.get("round2_accuracy") is not None:
            bits.append(f"r2={r['round2_accuracy']:.3f}")
            bits.append(f"lift={r['round2_lift']:+.3f}")
        bits.append(f"${cost_val:.4f}" if cost_val is not None else "$incomplete")
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
        stores = build_stores(identity_from_args(args))
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
