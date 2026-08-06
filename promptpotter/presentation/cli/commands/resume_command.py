"""``cmd_resume`` — continue the active campaign.

Flags: ``--from N`` (rewind), ``--fork-on-divergence`` (sibling on replay
disagree), ``--no-check`` (silent), ``--diag`` (diag-sibling BFS).

Drift detection compares the stored ``campaign.json::root_content_hash``
to a freshly computed hash; ``classify_config_diff`` flags the diff as
policy-only (safe to resume) or data-affecting (recommends fork or fresh ``new``)."""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.jobs.mint import resolve_cycle_plan
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    backend_unreachable_result,
    bind_session_identity,
    confirm_tty,
    cycle_result_command,
    drive_cycle,
    get_verbose,
    identity_from_args,
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.initialization.session import Session
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
    steer_fork: bool = False,
) -> dict[Any, Any]:
    """Apply pipeline to session + verify the campaign config still matches.

    Drift = recomputed hash vs stored ``campaign.json::root_content_hash``:
    empty stored ⇒ unstarted check-in, refuse; match ⇒ resume; differ ⇒ classify
    POLICY_ONLY (safe) vs DATA_AFFECTING (recommend fork or fresh ``new``;
    TTY pivot offered when ``pivot_prompt=True``, sweep callers pass False).

    ``steer_fork`` (``--steer-model``) downgrades the drift halt to a note: a
    steer-fork mints a fresh sibling under the current config + prompts — the very
    "config changed → fork" resolution the halt recommends — so it must not be
    blocked by it, matching the web ``fork-cycle`` path (which runs no drift check).

    OPTIMIZER drift is not asked here — it is asked per round, from the application seam
    every entry point reaches (``resume_and_fork/resume.py::_optimizer_divergences``). A
    campaign-level hash equality halting with "mint a new campaign" is the wrong shape
    twice over: on the CLI a resume from the webapp bypasses it entirely, and comparing
    against the MINT-time value cannot name which rounds ran under which optimizer or
    offer the fork every other divergence offers.
    """
    from promptpotter.application.knobs import DiffScope, classify_config_diff

    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    plan = resolve_cycle_plan(
        session, campaign_config, train_data, log=logger.info if get_verbose() else None
    )
    pipeline_params = plan.pipeline_params
    # build_origin_cycle_id yields ``cycle_<hash>``; campaign.json stores the bare hash.
    current_hash = plan.cycle_id.removeprefix("cycle_")

    campaign = session.store.campaigns.load_campaign(ctx.campaign_id)
    if campaign is None:
        raise SystemExit(
            f"ERROR: campaign manifest not found for '{ctx.campaign_id}'.\n"
            "Run `python -m promptpotter new <dataset>` to mint a fresh campaign."
        )

    if campaign.root_content_hash == "":
        # Only an unstarted check-in campaign carries an empty hash — both mint
        # seams stamp it (auto_mint at mint, finalize_checkin at Start).
        raise SystemExit(
            f"ERROR: campaign {ctx.campaign_id} has no stamped identity — an "
            "unstarted check-in can't be resumed. Start it first."
        )
    if campaign.root_content_hash == current_hash:
        print(f"config: unchanged (content hash {current_hash})")
    else:
        scope, diffed = classify_config_diff(campaign_config, campaign.config)
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
            # Policy-only: cached measurements + L1 candidates stay valid; divergence walk short-circuits.
            print("Diff is policy-only — safe to resume in place. The new policy")
            print("governs unevaluated rounds; past measurements are reused.")
        elif steer_fork:
            # A steer-fork mints a fresh sibling under the current config — the drift's
            # own recommended resolution. Don't halt; the fork carries the new config.
            print("Diff is data-affecting, but --steer-model mints a fresh sibling")
            print("under the current config — proceeding to fork (parent preserved).")
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
    """Diag-BFS: ``--diag`` against a finalized diag cycle branches a counted sibling
    (``{root}_diag_NNN``); each probe is its own cycle with ``parent_cycle_id`` set."""
    if not (
        getattr(args, "diag", False)
        and ctx.cycle_id
        and getattr(args, "resume_from_round", None) is None
    ):
        return
    existing_index = session.store.campaigns.load(ctx.hop) or {}
    if (existing_index.get("final") or {}).get("mode") != "diag":
        return
    from promptpotter.application.optimization.resume_and_fork.fork_siblings import _mint_fork
    from promptpotter.domain.run_records import ForkSpec, ForkTrigger

    tenant_id = session.identity.tenant_id
    new_cycle_id = _mint_fork(
        session.store.campaigns,
        ctx.hop,
        ctx.session_id,
        0,
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_DIAG,
            reason="diag-sibling BFS exploration",
            issued_by=tenant_id,
        ),
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id


def _maybe_fork_operator_rewind(
    args: argparse.Namespace, ctx: SessionCtx, session: Session
) -> None:
    """``--rewind N``: mint an OPERATOR_REWIND sibling cycle at round N, preserve
    the parent intact, retarget the active pointer. Parent's rounds 0..N-1 are
    copied to the fork; optimization continues on the fork from round N."""
    rewind_to = getattr(args, "rewind_to_round", None)
    if rewind_to is None:
        return
    if not ctx.cycle_id:
        raise SystemExit(
            "ERROR: `resume --rewind N` requires an active cycle on this session.\n"
            "Run `python -m promptpotter new <dataset>` first."
        )
    if rewind_to < 0:
        raise SystemExit(f"ERROR: --rewind must be >= 0, got {rewind_to}")
    if rewind_to == 0:
        raise SystemExit("ERROR: --rewind 0 mints a fork at the cycle root. Use `--diag` instead.")

    from promptpotter.application.optimization.resume_and_fork.fork_siblings import _mint_fork
    from promptpotter.domain.run_records import ForkSpec, ForkTrigger

    reason = (getattr(args, "rewind_reason", "") or "").strip() or (
        f"operator rewind to round {rewind_to}"
    )
    tenant_id = session.identity.tenant_id
    parent_cycle_id = ctx.cycle_id
    new_cycle_id = _mint_fork(
        session.store.campaigns,
        CycleHop(campaign_id=ctx.campaign_id, cycle_id=parent_cycle_id),
        ctx.session_id,
        rewind_to,
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_REWIND,
            reason=reason,
            issued_by=tenant_id,
        ),
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id
    logger.info(
        "Operator rewind: %s → %s at round %d [reason=%s]",
        parent_cycle_id,
        new_cycle_id,
        rewind_to,
        reason,
    )


def _origin_candidate_id(session: Session, cycle_id: str, from_round: int) -> str:
    """The branch-point candidate id in *cycle_id*'s round *from_round* — what the
    fork inherits its C0 from. A param-only origin's round 0 holds exactly the origin
    candidate; return the single (or first) recorded candidate. Empty string when the
    round file is missing or holds no candidate (the inherit path then re-scores)."""
    round_file = session.store.campaigns.load_round_file(
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id), from_round
    )
    scores = getattr(round_file, "candidate_scores", None) or [] if round_file else []
    return scores[0].candidate_id if scores else ""


def _maybe_fork_operator_steer(args: argparse.Namespace, ctx: SessionCtx, session: Session) -> None:
    """``--steer-model NODE=MODEL``: mint an operator-steered fork whose seed overlay
    steers a node's inner-optimizer model — the CLI twin of the web steer-fork
    (``POST /commands/fork-cycle``). The done origin's C0 is **inherited**, never
    re-scored (the overlay is model-only; ``try_inherit_fork_origin`` reads the
    branch-point candidate via ``from_candidate_id``), so only the candidate is
    measured under the steered model.

    Steering to a model OUTSIDE the origin's ``allowed_models`` (empty = nothing
    sanctioned) is a babysit act: it requires ``campaign.babysit`` (the fork-cycle
    applier's gate) and the runner stamps the branch grade C (``runner/entry.py``,
    the seam both paths share). A steer to a SANCTIONED model is a clean fork — no
    cap, no taint. Retargets the active pointer; the resume loop then runs the fork."""
    specs = getattr(args, "steer_model", None)
    if not specs:
        return
    if not ctx.cycle_id:
        raise SystemExit(
            "ERROR: `resume --steer-model` requires an active cycle on this session.\n"
            "Run `python -m promptpotter new <dataset>` first."
        )

    from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
        mint_operator_fork,
    )
    from promptpotter.domain.opt_search_point import overlay_sets_model_outside_allowed
    from promptpotter.domain.run_records import ConfigOverrides, CycleSeed
    from promptpotter.shared.identity import CAMPAIGN_BABYSIT_CAP, has_capability

    overlay: dict[str, Any] = {}
    for spec in specs:
        node, sep, model = spec.partition("=")
        node, model = node.strip(), model.strip()
        if not sep or not node or not model:
            raise SystemExit(f"ERROR: --steer-model expects NODE=MODEL, got {spec!r}")
        overlay.setdefault(node, {})["model"] = model

    # Read the origin's allow-list off the campaign config (unchanged by this steer —
    # the fork inherits it). A steer OUTSIDE it taints (babysit + grade C); a steer to
    # a sanctioned model is clean.
    allowed_models = ctx.campaign_config.allowed_models
    disallowed = overlay_sets_model_outside_allowed(overlay, allowed_models)
    if disallowed:
        # Same capability gate the web fork-cycle applier runs. The terminal owner
        # holds it; a delegated sub-principal without it is refused here.
        if not has_capability(session.identity, CAMPAIGN_BABYSIT_CAP):
            raise SystemExit(
                f"ERROR: steering to a model outside the origin's allowed_models "
                f"requires the {CAMPAIGN_BABYSIT_CAP} capability."
            )
        models = ", ".join(sorted(m for c in overlay.values() for m in [c.get("model")] if m))
        print()
        print(f"⚠  Steering the inner-optimizer model to {models} — NOT in the origin's")
        print(f"   allowed_models {allowed_models or '[] (nothing sanctioned)'}.")
        print("   This branch will be marked babysat (grade C); the origin's C0 is inherited.")
        print()
        # The `campaign.babysit` cap (checked above) is the authorization — same as the
        # web fork-cycle path, which has no confirm. The TTY prompt is a courtesy: an
        # explicit typed "no" cancels; a non-TTY run (None) proceeds on the cap.
        if confirm_tty("Proceed with the babysit steer?", default_no=True) is False:
            raise SystemExit("Cancelled. The active campaign is unchanged.")

    steer_max = getattr(args, "steer_max_rounds", None)
    config_overrides = (
        ConfigOverrides(max_rounds=steer_max) if steer_max is not None else ConfigOverrides()
    )
    parent_cycle_id = ctx.cycle_id
    new_cycle_id = mint_operator_fork(
        stores=session.store,
        hop=CycleHop(campaign_id=ctx.campaign_id, cycle_id=parent_cycle_id),
        from_round=0,
        # The origin candidate in the parent's round 0 — the C0 the fork inherits
        # (skips the origin re-score, straight to L1 on the steered model).
        from_candidate_id=_origin_candidate_id(session, parent_cycle_id, 0),
        seed=CycleSeed(pipeline_overlay=overlay, config_overrides=config_overrides),
        steered_by=str(session.identity.user_id),
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id
    logger.info(
        "Operator steer-fork (%s): %s → %s [overlay=%s]",
        "babysit, grade C" if disallowed else "clean, sanctioned model",
        parent_cycle_id,
        new_cycle_id,
        overlay,
    )


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
    from promptpotter.application.runner.entry import RunMode

    cycle_result, _ = await drive_cycle(
        args,
        ctx,
        campaign_config,
        session,
        train_data,
        mode=RunMode(
            resume_from_round_override=getattr(args, "resume_from_round", None),
            no_divergence_check=getattr(args, "no_divergence_check", False),
            fork_on_divergence=fork_on_divergence,
            diag=getattr(args, "diag", False),
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        ),
    )
    return cycle_result


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:
    """Drive the loop. ``ResumeDivergenceError`` → prompt operator (TTY) to fork; yes ⇒ re-run with ``fork_on_divergence=True``."""
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
        # Pre-authorized (--fork-on-divergence set; trigger fired pre-checkpoint) — no menu, propagate.
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
        # Interactive: show context + ask y/N; non-TTY falls through to the structured error (scripts get exit-code).
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

    return cycle_result_command(ctx, session, cycle_result)


async def cmd_resume(args: argparse.Namespace) -> CommandResult:
    """Resume the active campaign. Flags drive rewind / divergence / diag modes."""
    from promptpotter.shared.spend import refresh_rates_in_background

    refresh_rates_in_background()

    ctx = load_session(args)
    if not ctx.cycle_id:
        raise SystemExit(
            "ERROR: no active campaign to resume. Run `python -m promptpotter new <dataset>` first."
        )

    # A check-in campaign (origin still being authored — no committed dataset, no
    # rounds) isn't resumable: there's nothing to run until it's Started. Guard
    # cheaply before init_services so the operator gets a clear next step instead of
    # a confusing dataset-not-found deep in the loop.
    from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
    from promptpotter.infrastructure.store.stores import build_stores

    _campaign = build_stores(
        identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT
    ).campaigns.load_campaign(ctx.campaign_id)
    if _campaign is not None and _campaign.lifecycle_status == "checkin":
        raise SystemExit(
            f"ERROR: campaign '{ctx.campaign_id}' is still in check-in — its origin "
            "isn't authored yet, so there's nothing to resume.\n"
            "Finish + Start it in the webapp, or run "
            "`python -m promptpotter new <file>` to author and run from the CLI."
        )

    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params, identity=identity_from_args(args))

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return backend_unreachable_result(ctx.backend_url)

    train_data = session.samples
    steering = bool(getattr(args, "steer_model", None))
    try:
        pipeline_params = _prepare_cycle_for_resume(
            args, ctx, session, campaign_config, train_data, steer_fork=steering
        )
    except _PivotToFreshError as pivot:
        # Operator pivoted: synthesize a ``new`` namespace from the active session's dataset + halt/spend knobs.
        from promptpotter.presentation.cli.commands.new import cmd_new

        new_args = argparse.Namespace(
            command="new",
            dataset=pivot.dataset_name,
            dataset_name=None,
            config=None,
            task_file=None,
            task_text=None,
            backend_url=ctx.init_params.get("backend_url"),
            backend_id=ctx.init_params.get("backend_id"),
            sweep=False,
            diag=False,
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
            spend_budget_usd=getattr(args, "spend_budget_usd", None),
            tenant=getattr(args, "tenant", None),
            verbose=getattr(args, "verbose", False),
            session=getattr(args, "session", None),
            json_output=getattr(args, "json_output", False),
        )
        return await cmd_new(new_args)

    bind_session_identity(session, ctx)

    _maybe_fork_diag_sibling(args, ctx, session)
    _maybe_fork_operator_rewind(args, ctx, session)
    _maybe_fork_operator_steer(args, ctx, session)

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
