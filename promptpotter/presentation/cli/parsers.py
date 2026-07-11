"""Argparse schema for ``new`` / ``resume`` / ``sweep`` / ``reset`` / ``verify``.

Imported by ``campaign_runner.main()``. Help text is verbose by design — this
is the operator-facing surface.

Two write verbs:

* ``new [DATASET|FILE]`` mints a fresh campaign — from an authored dataset
  name, or from a raw file (CSV) it ingests → origin-resolves → commits as a
  tenant dataset → runs. ``campaign_id`` is ``{dataset}__{YYYYMMDD-HHMMSS}``,
  collision-free by construction, so every invocation lands in its own
  ``campaigns/{campaign_id}/`` directory with its own dashboard.
* ``resume`` continues the active campaign (rewinds with ``--from``, forks
  on divergence with ``--fork-on-divergence``, etc.).
"""

from __future__ import annotations

import argparse

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
)


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Tenant + session + verbosity flags shared across every command."""
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    parser.add_argument(
        "--tenant",
        default=None,
        help=(
            "Tenant partition under .promptpotter/projects/. Unset → the "
            "registered developer (default-tenant claim marker) or anonymous "
            "'default' if never registered. Pass a slug to override."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logs (timestamps, module tags, every INFO line)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of human-formatted text",
    )


def _add_runtime_halts(p: argparse.ArgumentParser) -> None:
    """Shared --halt-at / --spend-budget flags (new + resume).

    ``--spend-budget`` overrides ``campaign.json::optimization.spend_budget_usd``
    when supplied; either source halts the cycle at the next round boundary
    once cumulative spend (optimizer + backend) crosses the threshold."""
    p.add_argument(
        "--halt-at",
        dest="halt_at_accuracy",
        type=float,
        default=None,
        metavar="ACC",
        help="Halt when best accuracy ≥ ACC (e.g. 0.66).",
    )
    p.add_argument(
        "--spend-budget",
        dest="spend_budget_usd",
        type=float,
        default=None,
        metavar="USD",
        help="Halt when cumulative cycle spend (optimizer + backend) ≥ USD.",
    )


def _add_new_args(p_new: argparse.ArgumentParser) -> None:
    """Fresh-init flags for ``new``.

    The positional accepts a dataset **name** *or* a **raw file** (CSV):
    ``new aime`` reads ``datasets/aime/{pipeline,campaign}.json`` and starts the
    loop; ``new data.csv`` ingests the file, resolves its origin via the AI
    check-in, commits a tenant dataset, then runs — the headless twin of the web
    onboarding (parses → draft → auto-drives the resolver → deterministic gate →
    mint). Residual gaps are answered with repeatable ``--set field=value``
    (operator-stated, applied before the resolver so they seed it), or printed so
    you can re-run — no silent default ever reaches mint. Explicit ``--config``
    overrides ``datasets/<name>/campaign.json``.
    """
    p_new.add_argument(
        "dataset",
        nargs="?",
        default=None,
        help="Dataset name under ./datasets/ OR a path to a raw file (CSV). "
        "A name reads datasets/<name>/{pipeline,campaign}.json; a file is "
        "ingested → origin-resolved → committed as a tenant dataset → run. "
        "Omit (name form) only if you pass --dataset-name explicitly.",
    )
    p_new.add_argument(
        "--dataset-name",
        default=None,
        help="Explicit dataset name (alternative to positional). Required if "
        "no positional dataset is given.",
    )
    p_new.add_argument(
        "--config",
        default=None,
        help="Campaign config JSON override (defaults to datasets/<name>/campaign.json).",
    )
    p_new.add_argument(
        "--task-file", default=None, help="Override datasets/<name>/task_description.md"
    )
    p_new.add_argument(
        "--task-text", default=None, help="Override datasets/<name>/task_description.md inline"
    )
    # File-ingest form only (ignored for the name form).
    p_new.add_argument(
        "--slug",
        default=None,
        help="(file form) Dataset slug under projects/{tenant}/datasets/ "
        "(default: derived from the filename).",
    )
    p_new.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="(file form) Confirm an origin field directly (operator-stated), e.g. "
        "`--set task_description='map names to codes'` or "
        "`--set column.query=input`. Repeatable. Applied before the resolver "
        "runs, so it seeds the rest.",
    )
    p_new.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_new.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)

    mode_group = p_new.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sweep-batch",
        dest="sweep",
        action="store_true",
        help="Multi-fork batch from datasets/<name>/sweep/*.json: mint "
        "one sweep fork per payload, run each.",
    )
    mode_group.add_argument(
        "--diag",
        dest="diag",
        action="store_true",
        help="Diagnostic mode: origin → 1 full scored round → force "
        "task-framing refinement (regardless of stall) → 1 generation-only "
        "round 2 (refinement overrides applied, no scoring) → halt. "
        "index.json::final.mode lands as 'diag'.",
    )

    _add_runtime_halts(p_new)


def _add_resume_args(p_resume: argparse.ArgumentParser) -> None:
    """Resume / divergence / mode flags for ``resume``.

    All operate on the active session pointed to by ``active_session.json``.
    No fresh-init flags here — those moved to ``new``.
    """
    p_resume.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume after round N (archives rounds > N, reloads trial_N). "
        "Omit to pick up from the latest completed round.",
    )
    p_resume.add_argument(
        "--no-check",
        dest="no_divergence_check",
        action="store_true",
        help="On resume, rescore but skip the decision-replay halt.",
    )
    p_resume.add_argument(
        "--fork-on-divergence",
        dest="fork_on_divergence",
        action="store_true",
        help="On divergence, mint a sibling cycle (with parent_cycle_id) "
        "and re-run the divergent round under the current scorer.",
    )
    p_resume.add_argument(
        "--diag",
        dest="diag",
        action="store_true",
        help="Diagnostic mode (see `new --diag`). On a previously-completed "
        "diag cycle, branches off a counted sibling.",
    )
    p_resume.add_argument(
        "--rewind",
        dest="rewind_to_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Mint a sibling cycle at ROUND (OPERATOR_REWIND trigger), retarget "
        "the active pointer, and start optimization on the fork. Parent cycle is "
        "preserved intact. Contrast with `--from N` which rewinds in place.",
    )
    p_resume.add_argument(
        "--rewind-reason",
        dest="rewind_reason",
        type=str,
        default="",
        metavar="STR",
        help="One-line audit-trail reason recorded on the OPERATOR_REWIND fork; "
        "ignored unless `--rewind ROUND` is set.",
    )
    _add_runtime_halts(p_resume)


def _add_sweep_slice_args(p: argparse.ArgumentParser) -> None:
    """Shared ``--slice`` modifier — restricts the sample population."""
    p.add_argument(
        "--slice",
        dest="slice_spec",
        default="all",
        help="Sample-population filter: 'all' (default), 'easy' (top hit-rate "
        "quartile), 'hard' (bottom quartile), or 'samples=ID1,ID2,...' for an "
        "explicit list. Easy/hard read the cross-cycle archive; fall back to "
        "'all' when no measurements exist yet.",
    )


def _add_sweep_prompt_args(p: argparse.ArgumentParser, *, allow_multi: bool = True) -> None:
    """Shared optimizer-meta-prompt selectors + labels for ``l1_generate``
    and ``l2_context``. One node per sweep — ``_parse_variants`` rejects
    passing both, so a 2-round sweep's conformance reading attributes
    cleanly to the prompt under test.

    ``allow_multi=True`` lets round1/round2 take a comma-list of variants;
    each variant runs in its own fork. ``time-to`` keeps singular semantics
    (only one halt-on-accuracy run makes sense per invocation).
    """
    for node, flag in (("l1", "l1_generate"), ("l2", "l2_context")):
        if allow_multi:
            p.add_argument(
                f"--{node}-prompts",
                dest=f"{node}_prompts",
                default=None,
                help="Comma-separated paths to prompt-template JSON files to A/B "
                "in one invocation. Each variant runs in its own sweep "
                "fork; results share a sweep_id. Omit to use the currently "
                f"loaded {flag} template.",
            )
        else:
            p.add_argument(
                f"--{node}-prompt",
                dest=f"{node}_prompt",
                default=None,
                help=f"Path to a prompt-template JSON to swap in for {flag}. "
                "Omit to use the currently loaded template.",
            )
        p.add_argument(
            f"--{node}-prompt-label",
            dest=f"{node}_prompt_label",
            default=None,
            help=f"Operator label for the {flag} meta-prompt revision "
            f"(e.g. '{node}_v3'). Recorded on the result.",
        )


def _add_sweep_args(p_sweep: argparse.ArgumentParser) -> None:
    """Sweep-toolkit verbs. Wraps the optimizer with halt gates + per-round
    panel stats and persists one result JSON per run under
    ``archive/sweeps/{l1_meta_prompt_hash}/{dataset}/``.
    """
    sweep_sub = p_sweep.add_subparsers(dest="sweep_verb", required=True)

    # time-to ----------------------------------------------------------------
    p_time_to = sweep_sub.add_parser(
        "time-to",
        help="Run optimize, halt on target accuracy / max-rounds / spend-budget; write one result JSON.",
    )
    p_time_to.add_argument(
        "target",
        type=int,
        metavar="N",
        help="Target accuracy as a percent (e.g. 66 = halt when best ≥ 0.66).",
    )
    p_time_to.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=10,
        help="Round ceiling (overrides campaign.json::optimization.max_rounds for this sweep).",
    )
    p_time_to.add_argument(
        "--spend-budget",
        dest="spend_budget",
        type=float,
        default=None,
        help="Halt when cumulative cycle spend (USD, optimizer + backend) ≥ this value.",
    )
    _add_sweep_prompt_args(p_time_to, allow_multi=False)
    _add_sweep_slice_args(p_time_to)

    # round1 -----------------------------------------------------------------
    p_round1 = sweep_sub.add_parser(
        "round1",
        help="Run one scored round on a panel of `--panel-size` candidates; "
        "record accuracy + parse-fail + entropy + cost.",
    )
    p_round1.add_argument(
        "--panel-size",
        dest="panel_size",
        type=int,
        default=6,
        help="Number of candidates to generate (overrides campaign.json::optimization.n_variants).",
    )
    _add_sweep_prompt_args(p_round1)
    _add_sweep_slice_args(p_round1)

    # round2 -----------------------------------------------------------------
    p_round2 = sweep_sub.add_parser(
        "round2",
        help="Run two scored rounds — round1 + one more. Records round1/round2 "
        "accuracy and round2_lift.",
    )
    p_round2.add_argument(
        "--panel-size",
        dest="panel_size",
        type=int,
        default=6,
        help="Candidates per round (overrides campaign.json::optimization.n_variants).",
    )
    p_round2.add_argument(
        "--from-sweep",
        dest="from_sweep",
        default=None,
        help="Sweep id of a prior round1 sweep. Reads its top-K variants by "
        "round1_accuracy, re-runs each with 2 rounds (round1 + one more), "
        "and anchors round2_lift against each variant's prior round1.",
    )
    p_round2.add_argument(
        "--top",
        dest="top_k",
        type=int,
        default=3,
        help="With --from-sweep: number of top-K variants to re-run (default 3).",
    )
    _add_sweep_prompt_args(p_round2)
    _add_sweep_slice_args(p_round2)

    # rank -------------------------------------------------------------------
    p_rank = sweep_sub.add_parser(
        "rank",
        help="Read sweep results from archive/sweeps/ and print a sorted table. "
        "Pure read — does not run optimize.",
    )
    p_rank.add_argument(
        "--by",
        dest="rank_by",
        default="final_accuracy",
        help="Column to sort by: any field in the result JSON shape, or the "
        "derived 'cost_per_lift'. Default: final_accuracy.",
    )
    p_rank.add_argument(
        "--dataset",
        dest="dataset",
        default=None,
        help="Filter to one dataset (matches result.dataset). Omit for all datasets.",
    )
    p_rank.add_argument(
        "--verb",
        dest="filter_verb",
        default=None,
        help="Filter to one verb (time-to, round1, round2). Omit for all.",
    )
    p_rank.add_argument(
        "--last",
        dest="last",
        type=int,
        default=10,
        help="Show the most recent N rows after sort (default 10).",
    )
    p_rank.add_argument(
        "--ascending",
        dest="ascending",
        action="store_true",
        help="Sort ascending (default: descending — best first).",
    )


def _add_champion_args(p_champion: argparse.ArgumentParser) -> None:
    """``champion <verb>`` — the L4 champion registry. Pure disk reads.

    ``refresh`` reduces the pp-self corpus to a ranked table of candidate
    meta-prompt states under ``<tenant>/meta_champion/registry.json``.
    """
    champion_sub = p_champion.add_subparsers(dest="champion_verb", required=True)
    champion_sub.add_parser(
        "refresh",
        help="Rebuild the champion registry from every pp-self cycle on disk and "
        "print the ranked table (anchor-to-origin effect). Zero LLM calls.",
    )
    p_promote = champion_sub.add_parser(
        "promote",
        help="Elect a state (by state_hash) as the reigning champion — writes the "
        "datasets/_optimizer_meta/champion.json pointer. Uncontested.",
    )
    p_promote.add_argument("state_hash", help="The candidate state_hash to crown.")
    p_coronate = champion_sub.add_parser(
        "coronate",
        help="Head-to-head a challenger vs the reigning champion on their shared "
        "cells (paired, from the registry); crown it only if the pooled CI clears 0.",
    )
    p_coronate.add_argument("state_hash", help="The challenger state_hash.")
    p_coronate.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the coronation verdict without writing the champion pointer.",
    )
    p_apply = champion_sub.add_parser(
        "apply",
        help="Graduate the reigning champion's prompt fields into the distributable "
        "datasets/_optimizer/pipeline.json — the shipped optimizer + the next pp-self "
        "run's inner origin both start from it. Review the git diff and commit deliberately.",
    )
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the field diff without writing _optimizer/pipeline.json.",
    )
    champion_sub.add_parser(
        "replay",
        help="Print the reigning champion + persisted registry from disk (zero "
        "recompute) — the auditable current state.",
    )


def _add_matrix_args(p_matrix: argparse.ArgumentParser) -> None:
    """``matrix <verb>`` — the L4 resource matrix (capability grid).

    ``measure <dataset> --models M [M ...]`` scores that dataset's ORIGIN under each
    target model and upserts the verdicts into the pp-self ``resource_matrix.json``.
    """
    matrix_sub = p_matrix.add_subparsers(dest="matrix_verb", required=True)
    p_measure = matrix_sub.add_parser(
        "measure",
        help="Score a dataset's origin under one or more target models; classify "
        "each (model,dataset) cell floor/in-band/saturated and record it.",
    )
    p_measure.add_argument("dataset", help="Dataset name to measure (e.g. justlogic).")
    p_measure.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="One or more target-model ids to measure origin under (e.g. "
        "openai/gpt-oss-20b:nitro).",
    )
    p_measure.add_argument(
        "--provider",
        default=None,
        help="Provider to route every listed model through (e.g. openrouter). Omit "
        "to use the dataset's own provider.",
    )
    p_measure.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Number of samples to score origin on (default 20).",
    )


def _add_verify_args(p_verify: argparse.ArgumentParser) -> None:
    """Campaign + candidate selectors + sample budget for ``verify``."""
    p_verify.add_argument(
        "campaign",
        help="Campaign id, 6-hex suffix, or unambiguous prefix "
        "(e.g. 'justlogic__ca6d4d' or 'ca6d4d').",
    )
    p_verify.add_argument(
        "label",
        help="Candidate label as persisted on the round file: 'C{round}.{n}' "
        "(1-indexed within the round, e.g. 'C4.1').",
    )
    p_verify.add_argument(
        "--cycle",
        dest="cycle",
        default=None,
        help="Cycle id (full or prefix) when the campaign has more than one cycle. "
        "Omit when the campaign has exactly one cycle.",
    )
    p_verify.add_argument(
        "--samples",
        dest="samples",
        type=int,
        default=20,
        help="Number of additional samples to score (default 20). The adaptive "
        "queue mechanism skips samples this candidate has already been measured "
        "on across the cross-cycle archive.",
    )
    p_verify.add_argument(
        "--seed",
        dest="seed",
        type=int,
        default=None,
        help="RNG seed for reproducible sample picks (default: random).",
    )


def _add_noise_floor_args(p_noise_floor: argparse.ArgumentParser) -> None:
    """Campaign selector + replicate count for ``noise-floor``."""
    p_noise_floor.add_argument(
        "campaign",
        help="Campaign id, 6-hex suffix, or unambiguous prefix "
        "(e.g. 'promptpotter-self__ca6d4d' or 'ca6d4d').",
    )
    p_noise_floor.add_argument(
        "--cycle",
        dest="cycle",
        default=None,
        help="Cycle id (full or prefix) when the campaign has more than one cycle. "
        "Omit when the campaign has exactly one cycle.",
    )
    p_noise_floor.add_argument(
        "--k",
        dest="k",
        type=int,
        default=3,
        help="Number of force_fresh re-scores of the cached origin (default 3). "
        "kx real spend — on a pp-self cycle each re-score re-runs the full inner "
        "recursion, so keep k small.",
    )


def _add_reset_args(p_reset: argparse.ArgumentParser) -> None:
    """Tenant scope + safety flags for ``reset``.

    Drops ``campaigns/`` + ``sessions/`` + ``active_session.json`` under the
    selected tenant; ``measurements/`` (the DB core) + ``archive/`` (recycle bin +
    optimizer_calls + sweeps) are preserved. ``--dry-run`` is the recommended first step.
    """
    scope = p_reset.add_mutually_exclusive_group()
    scope.add_argument(
        "--all-tenants",
        dest="all_tenants",
        action="store_true",
        help="Reset every tenant under .promptpotter/projects/ "
        "(default: only the tenant named by --tenant).",
    )
    p_reset.add_argument(
        "--yes",
        action="store_true",
        help="Skip the y/N confirmation prompt. Required for non-interactive use.",
    )
    p_reset.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="List the paths that would be removed; touch nothing. Recommended first run.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Argparse schema for ``new`` / ``resume`` / ``sweep`` / ``reset`` / ``verify``.

    Bare ``python -m promptpotter`` (no subcommand) defaults to ``resume`` —
    the most common operator action gets the shortest invocation.
    """
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="PromptPotter optimization CLI. Bare invocation runs "
        "`resume` (continue the active session). `new [DATASET|FILE]` mints a "
        "fresh campaign — from a dataset name or a raw file it ingests + "
        "origin-resolves. Reads happen by opening the artifact tree "
        "(sessions/{id}/, campaigns/{campaign_id}/) directly.",
    )
    _add_global_args(parser)
    sub = parser.add_subparsers(dest="command", required=False)

    p_new = sub.add_parser(
        "new",
        help="Mint a fresh campaign from a dataset NAME or a raw FILE (CSV). A "
        "name uses an authored datasets/<name>/; a file is ingested → "
        "origin-resolved → committed as a tenant dataset → run (headless parity "
        "with the web onboarding). Every invocation mints a brand-new campaign "
        "(campaign_id is timestamp-derived — collision-free, no discriminator).",
    )
    _add_new_args(p_new)

    p_resume = sub.add_parser(
        "resume",
        help="Continue the active campaign. Bare `resume` picks up where it "
        "left off; flags handle rewind / divergence / diagnostic modes.",
    )
    _add_resume_args(p_resume)

    sub.add_parser(
        "ab",
        help="Deterministic A/B replay of the active cycle: re-derive every recorded "
        "decision (winner / eliminations / L2-L3 triggers) under the CURRENT engine + "
        "scorer over the recorded measurements, and report where they flip. Zero LLM "
        "calls — run a cycle under one engine/scorer, then `ab` under another to diff.",
    )
    _add_sweep_args(
        sub.add_parser(
            "sweep",
            help="Sweep-toolkit verbs — cheap A/B of optimizer meta-prompt edits. "
            "Each verb wraps the optimizer with halt gates and persists one "
            "result JSON.",
        )
    )

    _add_reset_args(
        sub.add_parser(
            "reset",
            help="Drop campaigns/ + sessions/ + active_session.json for the "
            "selected tenant; preserve measurements/ (DB core) + archive/ "
            "(recycle bin + optimizer_calls + sweeps). The escape hatch for cycles "
            "obsoleted by code changes — per-sample measurements survive so the "
            "next `new` hits cache immediately.",
        )
    )

    _add_champion_args(
        sub.add_parser(
            "champion",
            help="L4 champion registry — reduce the on-disk pp-self corpus to one ranked "
            "table of candidate meta-prompt states. Pure disk read (zero LLM); developer "
            "surface for 'which meta-prompt state is overall best?'.",
        )
    )

    _add_matrix_args(
        sub.add_parser(
            "matrix",
            help="L4 resource matrix — score a dataset's origin under target models to "
            "classify (model,dataset) cells floor/in-band/saturated. The operator-set "
            "capability grid the L4 panel draws its in-band cells from.",
        )
    )

    _add_verify_args(
        sub.add_parser(
            "verify",
            help="Re-score one campaign candidate on more samples and persist a "
            "workspace-scope diagnostic-run record. Use to doublecheck whether "
            "a confidence-locked candidate's verdict generalises beyond the round's "
            "leader-locked sample budget. Does not mutate the source cycle.",
        )
    )

    sub.add_parser(
        "reindex",
        help="Rebuild the measurement index (measurements/index.jsonl) from the detail "
        "files and GC orphaned runs. The index is derived, so this loses nothing — use "
        "after a crash mid-append or to reclaim orphaned bytes. Pure disk work, zero spend.",
    )

    _add_noise_floor_args(
        sub.add_parser(
            "noise-floor",
            help="Debug diagnostic (NOT a loop feature): re-score a campaign's cached "
            "origin --k times with force_fresh and report the mean+CI spread — the "
            "backend's own run-to-run noise. On a pp-self cycle this measures the "
            "true inner-recursion noise floor. kx real spend; does not mutate the "
            "source cycle and is never invoked by the loop itself.",
        )
    )

    for verb, summary in (
        (
            "archive",
            "Move a campaign into the archive/ recycle bin — hides from the default sidebar, "
            "restorable by unarchive.",
        ),
        (
            "delete",
            "Destructively remove a campaign (no recovery); --keep-results spares the keepsake. "
            "Measurements still cache-hit for siblings.",
        ),
        ("unarchive", "Restore a campaign from the archive/ recycle bin to 'active'."),
    ):
        p = sub.add_parser(verb, help=summary)
        p.add_argument("campaign_id", help="Target campaign id ({dataset}__{rand6_hex})")
        if verb != "unarchive":
            p.add_argument(
                "--reason",
                default="",
                help="Optional operator-supplied reason for the transition.",
            )
        if verb == "delete":
            p.add_argument(
                "--keep-results",
                dest="keep_results",
                action="store_true",
                help="Spare the keepsake tier (manifest + reports + the shallow langfuse loop "
                "trace); drop only the heavy resume/audit/mirror tiers.",
            )

    return parser


__all__ = ["build_parser"]
