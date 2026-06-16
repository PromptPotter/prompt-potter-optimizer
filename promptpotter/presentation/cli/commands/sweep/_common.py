"""Shared sweep plumbing — variant resolution + cycle setup/run.

Used by every sweep verb (``time-to`` / ``round1`` / ``round2``): the
``_Variant`` model + flag parsing, and the ``_setup_sweep_cycle`` /
``_run_sweep_optimize`` pair that drives ``run_optimization``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.presentation.cli.commands._shared import (
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.commands.new import _build_observers
from promptpotter.presentation.cli.commands.resume_command import _prepare_cycle_for_resume
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx


def _sweep_early_exit_reason(stop_reason: str) -> str:
    """Map ``StopReason`` → sweep-result ``early_exit_reason`` enum.

    ``target_hit | max_rounds | spend_budget`` is the documented surface;
    anything else (patience, interrupt, hard-cap) lands as the raw
    stop_reason string so the operator sees why the sweep didn't bottom
    out on one of the three intentional exits.
    """
    from promptpotter.domain.phases import StopReason

    mapping = {
        StopReason.TARGET_HIT.value: "target_hit",
        StopReason.MAX_ROUNDS.value: "max_rounds",
        StopReason.SPEND_BUDGET.value: "spend_budget",
        StopReason.TOKEN_BUDGET.value: "token_budget",
    }
    return mapping.get(stop_reason, stop_reason)


@dataclass
class _Variant:
    """One optimizer-meta-prompt variant in a panel iteration."""

    path: Path | None  # None ⇒ use whatever is currently loaded
    label: str | None  # operator-supplied or file stem
    node: str = "l1_generate"  # optimizer node this variant overrides


def _parse_variants(args: argparse.Namespace) -> list[_Variant]:
    """Resolve the optimizer-meta-prompt variants for this sweep.

    Reads ``--l1-prompt(s)`` or ``--l2-prompt(s)`` — one optimizer node
    per invocation, so a 2-round sweep attributes its conformance reading
    cleanly to the prompt being A/B-tested. When neither is given returns
    a single ``_Variant(path=None)`` so the verb runs against the
    currently loaded ``l1_generate`` in one iteration.
    """
    l1_raw = getattr(args, "l1_prompts", None) or getattr(args, "l1_prompt", None)
    l2_raw = getattr(args, "l2_prompts", None) or getattr(args, "l2_prompt", None)
    # argparse declares only the singular OR plural flag per subparser
    # (see _add_sweep_prompt_args), so getattr-with-default is the contract.
    if l1_raw and l2_raw:
        raise SystemExit(
            "sweep: pass --l1-prompt(s) OR --l2-prompt(s), not both — one "
            "optimizer node per sweep keeps the conformance reading attributable"
        )
    if l2_raw:
        node, raw = "l2_context", l2_raw
        label_override = getattr(args, "l2_prompt_label", None)
    else:
        node, raw = "l1_generate", l1_raw
        label_override = getattr(args, "l1_prompt_label", None)
    if not raw:
        return [_Variant(path=None, label=label_override, node=node)]
    paths = [Path(p.strip()) for p in str(raw).split(",") if p.strip()]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"{node} prompt file(s) not found: {', '.join(missing)}")
    return [
        _Variant(
            path=p,
            label=(label_override if len(paths) == 1 else None) or p.stem,
            node=node,
        )
        for p in paths
    ]


def _resolve_template(variant: _Variant) -> Any:
    """Load the PromptTemplate from the variant's path (None ⇒ current loader)."""
    if variant.path is None:
        return None
    from promptpotter.application.sweep import load_prompt_template_from_path

    return load_prompt_template_from_path(variant.path)


def _variant_id_fields(variant: _Variant) -> dict[str, Any]:
    """The label + path result fields for whichever optimizer node this
    variant overrides; the other node's fields stay unset (``None``)."""
    path = str(variant.path) if variant.path else None
    if variant.node == "l2_context":
        return {"l2_meta_prompt_label": variant.label, "l2_prompt_path": path}
    return {"l1_meta_prompt_label": variant.label, "l1_prompt_path": path}


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
    pipeline_params = _prepare_cycle_for_resume(
        args, ctx, session, campaign_config, train_data, pivot_prompt=False
    )
    session.session_id = ctx.session_id
    session.campaign_id = ctx.campaign_id
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
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
    *,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
) -> tuple[Any, Any]:
    """Drive ``run_optimization`` for any sweep verb — honors
    ``halt_at_accuracy`` / ``spend_budget_usd`` and the active optimizer-prompt
    override (managed by the caller via :func:`optimizer_prompt_override`).
    Returns ``(cycle_result, observers)``."""
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
        spend_budget_usd=spend_budget_usd,
    )
    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    return cycle_result, observers


def _panel_stats_from_round(round_result: Any, panel_size: int) -> dict[str, float]:
    """Pull per-candidate accuracy + pipeline_params from a ``RoundResult``
    and route through :func:`compute_panel_stats`."""
    from promptpotter.application.sweep import compute_panel_stats

    scores = list(round_result.candidate_scores or [])
    accuracies = [s.accuracy for s in scores]
    pps = [s.pipeline_params_override or {} for s in scores]
    return compute_panel_stats(
        candidates_planned=panel_size,
        candidate_accuracies=accuracies,
        candidate_pipeline_params=pps,
    )
