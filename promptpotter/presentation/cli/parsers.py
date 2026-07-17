"""Argparse schema for ``new`` / ``resume`` / ``reset`` / ``verify``.

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
    p_resume.add_argument(
        "--steer-model",
        dest="steer_model",
        action="append",
        default=None,
        metavar="NODE=MODEL",
        help="Mint an operator-steered fork that overrides a node's model on the seed "
        "overlay (repeatable). Editing an optimizer-locked axis is a babysit act: it "
        "requires the `campaign.babysit` capability and grades the fork's runs C. CLI "
        "twin of the web steer-fork (`POST /commands/fork-cycle`), same seam + gate.",
    )
    p_resume.add_argument(
        "--steer-max-rounds",
        dest="steer_max_rounds",
        type=int,
        default=None,
        metavar="N",
        help="Round ceiling for a `--steer-model` fork (default: inherit the parent). "
        "Ignored unless `--steer-model` is set.",
    )
    _add_runtime_halts(p_resume)


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
    """Argparse schema for ``new`` / ``resume`` / ``reset`` / ``verify``.

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
