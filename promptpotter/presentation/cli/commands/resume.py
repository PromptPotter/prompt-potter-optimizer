"""``cmd_resume`` — continue the active campaign.

Bare ``resume`` picks up from the latest completed round. ``--from N``
rewinds in place. ``--fork-on-divergence`` mints a sibling cycle when
the decision-replay walker disagrees with the recorded trail.
``--no-check`` accepts divergence silently. ``--diag`` runs diagnostic
mode against the active cycle (branches a counted sibling on top of a
prior diag cycle).

Drift detection is a stored-value comparison: the campaign manifest
(``campaign.json::root_content_hash``) holds the origin content hash at
mint time, and ``resume`` recomputes the current config's hash and
compares. A match resumes in place; a mismatch surfaces a "what changed"
report and — via :meth:`CampaignConfig.classify_diff_against` — tells the
operator whether the diff is policy-only (safe, resume proceeds) or
data-affecting (recommend ``--fork-on-divergence`` or a fresh ``new``).
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    _prepare_cycle,
    campaign_result_human,
    confirm_tty,
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.commands.new import _build_observers
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


class _PivotToFreshError(Exception):
    """Operator confirmed at the drift prompt: pivot resume → new on this dataset."""

    def __init__(self, dataset_name: str) -> None:
        self.dataset_name = dataset_name


def _prepare_cycle_for_resume(
    args: argparse.Namespace,
    ctx: SessionCtx,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    *,
    pivot_prompt: bool = True,
) -> dict[Any, Any]:
    """Apply pipeline to session + verify the campaign config still matches.

    Drift is detected by recomputing the current config's content hash and
    comparing it to the stored ``campaign.json::root_content_hash``:

    * ``root_content_hash == ""`` (migrated campaign with no backfilled
      hash) — accept the resume and backfill the recomputed hash.
    * hashes MATCH — resume in place, no fork prompt.
    * hashes DIFFER — surface a "what changed" report. The diff is also
      classified via :meth:`CampaignConfig.classify_diff_against`:
      ``POLICY_ONLY`` is safe (resume proceeds — the divergence walk in
      ``resume_with_divergence_check`` continues on the parent's data
      trace); ``DATA_AFFECTING`` recommends ``--fork-on-divergence`` and,
      with a TTY and ``pivot_prompt=True``, offers a pivot to a fresh
      ``new`` run. Sweep callers pass ``pivot_prompt=False`` — pivoting
      would silently turn a sweep into a new-campaign run. Non-TTY ⇒
      structured error so scripts get a deterministic exit-code path.

    A second, independent check covers **optimizer-prompt drift**: the
    campaign id folds in the optimizer meta-prompt hash, so editing
    ``datasets/_optimizer/`` since mint time halts the resume with a
    pointer to ``new`` (which mints a distinct campaign for the new
    optimizer set). Empty stored hash means backfill, like the target hash.
    """
    from promptpotter.application.config import DiffScope
    from promptpotter.application.optimization.dispatch.llm_call import (
        combined_optimizer_prompt_hash,
    )

    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, _origin, expected_cycle_id = _prepare_cycle(
        session, campaign_config, train_data
    )
    # build_origin_cycle_id yields ``cycle_<content_hash>`` — the bare hash
    # is what campaign.json::root_content_hash stores.
    current_hash = expected_cycle_id.removeprefix("cycle_")

    campaign = session.store.campaigns.load_campaign(ctx.campaign_id)
    if campaign is None:
        raise SystemExit(
            f"ERROR: campaign manifest not found for '{ctx.campaign_id}'.\n"
            "Run `python -m promptpotter new <dataset>` to mint a fresh campaign."
        )

    if campaign.root_content_hash == "":
        # Migrated campaign — no backfilled hash. Accept the resume and
        # backfill so future resumes have a baseline to diff against.
        session.store.campaigns.update_campaign(
            ctx.campaign_id, {"root_content_hash": current_hash}
        )
        logger.info(
            "Resume: backfilled root_content_hash=%s on campaign %s",
            current_hash,
            ctx.campaign_id,
        )
    elif campaign.root_content_hash == current_hash:
        print(f"config: unchanged (content hash {current_hash})")
    else:
        scope, diffed = campaign_config.classify_diff_against(campaign.config)
        dataset_name = ctx.init_params.get("dataset_name") or "<dataset>"
        print()
        print("Config changed since the campaign was minted.")
        print(f"  campaign:     {ctx.campaign_id}")
        print(f"  stored hash:  {campaign.root_content_hash}")
        print(f"  current hash: {current_hash}")
        if diffed:
            print(f"  changed:      {', '.join(diffed)}")
        print()
        if scope is DiffScope.POLICY_ONLY:
            # Policy-only diff: cached measurements + L1 candidates stay
            # valid, past decisions are still the audit record. Resume
            # proceeds; the divergence walk short-circuits in
            # resume_with_divergence_check.
            print("Diff is policy-only — safe to resume in place. The new policy")
            print("governs unevaluated rounds; past measurements are reused.")
        else:
            print("Diff is data-affecting — cached measurements may not apply.")
            print("  • `python -m promptpotter resume --fork-on-divergence` branches a")
            print("    sibling cycle in this campaign at the divergence point.")
            print(f"  • `python -m promptpotter new {dataset_name}` mints a fresh")
            print("    campaign with the new config (this campaign is preserved).")
            drift_error_msg = (
                f"ERROR: data-affecting config drift on campaign {ctx.campaign_id}.\n"
                f"  stored hash:  {campaign.root_content_hash}\n"
                f"  current hash: {current_hash}\n"
                f"  changed:      {', '.join(diffed) or '(unclassified)'}\n"
                f"\n"
                f"Run `resume --fork-on-divergence` to branch a sibling cycle, or "
                f"`new {dataset_name}` for a fresh campaign."
            )
            if not pivot_prompt:
                raise SystemExit(drift_error_msg)
            answer = confirm_tty(
                f"Start a fresh campaign on `{dataset_name}` instead?", default_no=True
            )
            if answer is None:
                raise SystemExit(drift_error_msg)
            if not answer:
                raise SystemExit(
                    "Cancelled. Re-run `resume --fork-on-divergence` to branch a "
                    "sibling cycle, or revert the config edits and retry `resume`."
                )
            raise _PivotToFreshError(dataset_name)

    # Optimizer-prompt drift — the campaign's identity folds in the optimizer
    # meta-prompts (datasets/_optimizer/). Editing them is data-affecting:
    # cached rounds ran the old prompts, unevaluated rounds would run the new
    # ones. `new` is the fix — it auto-mints a distinct campaign for the new
    # optimizer set.
    current_optimizer_hash = combined_optimizer_prompt_hash()
    if campaign.optimizer_prompt_hash == "":
        session.store.campaigns.update_campaign(
            ctx.campaign_id, {"optimizer_prompt_hash": current_optimizer_hash}
        )
        logger.info(
            "Resume: backfilled optimizer_prompt_hash=%s on campaign %s",
            current_optimizer_hash,
            ctx.campaign_id,
        )
    elif campaign.optimizer_prompt_hash != current_optimizer_hash:
        dataset_name = ctx.init_params.get("dataset_name") or "<dataset>"
        raise SystemExit(
            f"ERROR: the optimizer meta-prompts changed since campaign "
            f"{ctx.campaign_id} was minted.\n"
            f"  stored optimizer hash:  {campaign.optimizer_prompt_hash}\n"
            f"  current optimizer hash: {current_optimizer_hash}\n"
            f"\n"
            f"The optimizer meta-prompts (datasets/_optimizer/) are part of a "
            f"campaign's identity — resuming would mix old-prompt rounds with "
            f"new-prompt rounds.\n"
            f"  - `python -m promptpotter new {dataset_name}` mints a distinct "
            f"campaign for the new optimizer prompts (recommended).\n"
            f"  - Revert datasets/_optimizer/ to resume this campaign in place."
        )

    if resume_from_round is not None:
        if not ctx.cycle_id:
            raise SystemExit(
                "ERROR: `resume --from N` requires an active cycle on this session.\n"
                "Run `python -m promptpotter new <dataset>` first."
            )
        if resume_from_round < 0:
            raise SystemExit(f"ERROR: --from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", ctx.cycle_id, resume_from_round)
    elif ctx.cycle_id:
        logger.info("Resuming cycle %s", ctx.cycle_id)

    return pipeline_params


def _maybe_fork_diag_sibling(args: argparse.Namespace, ctx: SessionCtx, session: Session) -> None:
    """Diag-BFS: re-running ``--diag`` against a finalized diag cycle branches
    off a counted sibling (``{root}_diag_NNN``) instead of overwriting the
    parent's archive. Each probe is its own cycle with ``parent_cycle_id`` set;
    origin measurements stay shared via the JSP-keyed archive."""
    if not (
        getattr(args, "diag", False)
        and ctx.cycle_id
        and getattr(args, "resume_from_round", None) is None
    ):
        return
    existing_index = session.store.campaigns.load(ctx.campaign_id, ctx.cycle_id) or {}
    if (existing_index.get("final") or {}).get("mode") != "diag":
        return
    from promptpotter.application.optimization.resume_and_fork import _mint_fork
    from promptpotter.domain.run_records import ForkPayload, ForkTrigger

    tenant_id = session.tenant.tenant_id if session.tenant else "default"
    new_cycle_id = _mint_fork(
        session.store.campaigns,
        ctx.campaign_id,
        tenant_id,
        ctx.session_id,
        ctx.cycle_id,
        0,
        ForkPayload(
            trigger=ForkTrigger.OPERATOR_DIAG,
            reason="diag-sibling BFS exploration",
            issued_by=tenant_id,
        ),
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id


async def _drive_optimization(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
    *,
    fork_on_divergence: bool,
) -> CycleResult:
    """One pass through the loop. Caller handles divergence menu + re-invoke."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    cycle_dir = session.store.campaigns.cycle_dir(ctx.campaign_id, ctx.cycle_id)
    session.stop_check = (cycle_dir / ".runtime" / "stop.flag").is_file

    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        resume_from_round_override=getattr(args, "resume_from_round", None),
        no_divergence_check=getattr(args, "no_divergence_check", False),
        fork_on_divergence=fork_on_divergence,
        sweep=False,
        diag=getattr(args, "diag", False),
        halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        max_spend_usd=getattr(args, "max_spend_usd", None),
        ignore_render_errors=getattr(args, "ignore_render_errors", False),
    )
    return cycle_result


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:
    """Drive the loop. On ``ResumeDivergenceError``, prompt the operator (if
    TTY) to fork inline; on yes, re-run the loop once with
    ``fork_on_divergence=True``."""
    from promptpotter.shared.errors import ResumeDivergenceError

    fork_on_divergence = bool(getattr(args, "fork_on_divergence", False))
    try:
        cycle_result = await _drive_optimization(
            args,
            ctx,
            campaign_config,
            session,
            train_data,
            fork_on_divergence=fork_on_divergence,
        )
    except ResumeDivergenceError as div:
        # Already pre-authorized (--fork-on-divergence set, but the trigger
        # fired before its first checkpoint) — no menu, propagate.
        if fork_on_divergence:
            return CommandResult(
                data={
                    "error": "resume_divergence",
                    "round": div.round_num,
                    "kind": div.kind,
                    "recorded_outcome": div.recorded_outcome,
                    "current_outcome": div.current_outcome,
                },
                human=f"{div}\n\n{_DIVERGENCE_HINT}",
            )
        # Interactive: show context, ask y/N. On non-TTY, fall through to
        # the structured error so scripts keep their exit-code contract.
        print()
        print(str(div))
        print()
        print(f"Active cycle: {ctx.cycle_id}")
        print("Forking branches a sibling cycle at this point under the current scorer.")
        print("The parent campaign is preserved untouched.")
        answer = confirm_tty("Fork here?", default_no=True)
        if answer is None:
            return CommandResult(
                data={
                    "error": "resume_divergence",
                    "round": div.round_num,
                    "kind": div.kind,
                    "recorded_outcome": div.recorded_outcome,
                    "current_outcome": div.current_outcome,
                },
                human=f"{div}\n\n{_DIVERGENCE_HINT}",
            )
        if not answer:
            return CommandResult(
                data={"cancelled": True, "reason": "divergence_declined"},
                human="Cancelled. The active campaign is unchanged.",
            )
        logger.info(
            "Operator accepted fork on divergence — re-running with fork_on_divergence=True"
        )
        cycle_result = await _drive_optimization(
            args,
            ctx,
            campaign_config,
            session,
            train_data,
            fork_on_divergence=True,
        )

    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    campaign_dir = session.store.campaigns.campaign_root_dir(ctx.campaign_id)
    return CommandResult(
        data=cycle_result.model_dump(),
        human=campaign_result_human(
            campaign_dir,
            dataset_name=ctx.init_params.get("dataset_name") or "?",
            cycle_id=cycle_result.cycle_id,
        ),
    )


async def cmd_resume(args: argparse.Namespace) -> CommandResult:
    """Resume the active campaign. Flags drive rewind / divergence / diag modes."""
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    ctx = load_session(args)
    if not ctx.cycle_id:
        raise SystemExit(
            "ERROR: no active campaign to resume. Run `python -m promptpotter new <dataset>` first."
        )
    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.samples or []
    try:
        pipeline_params = _prepare_cycle_for_resume(args, ctx, session, campaign_config, train_data)
    except _PivotToFreshError as pivot:
        # Operator chose at the drift prompt: pivot to a fresh `new` run.
        # Synthesize a `new` namespace from the active session's dataset +
        # the resume args's halt/spend knobs; dispatch directly so we don't
        # re-init services from scratch.
        from promptpotter.presentation.cli.commands.new import cmd_new

        new_args = argparse.Namespace(
            command="new",
            dataset=pivot.dataset_name,
            dataset_name=None,
            config=None,
            task_file=None,
            task_text=None,
            excel_path=None,
            backend_url=ctx.init_params.get("backend_url"),
            backend_id=ctx.init_params.get("backend_id"),
            experiment_id=ctx.init_params.get("experiment_id"),
            sweep=False,
            diag=False,
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
            max_spend_usd=getattr(args, "max_spend_usd", None),
            refresh_rates=False,
            tenant=getattr(args, "tenant", "default"),
            verbose=getattr(args, "verbose", False),
            session=getattr(args, "session", None),
            json_output=getattr(args, "json_output", False),
        )
        return await cmd_new(new_args)

    session.session_id = ctx.session_id
    session.campaign_id = ctx.campaign_id
    session.state.cycle_id = ctx.cycle_id

    _maybe_fork_diag_sibling(args, ctx, session)

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_resume"]
