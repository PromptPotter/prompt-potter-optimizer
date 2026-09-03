"""Argparse schema for the write + diagnostic verbs, imported by ``campaign_runner.main()``. Help
text is verbose by design — this is the operator-facing surface."""

from __future__ import annotations

import argparse

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    settings,
)


def _add_global_args(parser: argparse.ArgumentParser) -> None:
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
    """Shared ``--halt-at`` / ``--spend-budget`` / ``--token-budget``. Any source halts at the next
    round boundary once its own cumulative total (optimizer + backend) crosses the threshold."""
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
        help="Halt when cumulative cycle spend (optimizer + backend) ≥ USD. Lowers the "
        "configured ceiling only; `set-budget` is what raises one.",
    )
    p.add_argument(
        "--token-budget",
        dest="token_budget",
        type=int,
        default=None,
        metavar="N",
        help="Halt when cumulative cycle tokens (optimizer + backend, in + out) ≥ N. "
        "The model-portable twin of --spend-budget; whichever trips first halts.",
    )


def _add_new_args(p_new: argparse.ArgumentParser) -> None:
    """Fresh-init flags. The positional takes a dataset NAME or a raw FILE, the headless twin of the web
    onboarding; a residual gap is answered with ``--set`` or printed — no silent default reaches mint."""
    p_new.add_argument(
        "dataset",
        nargs="?",
        default=None,
        help="Dataset name under ./datasets/ OR a path to a raw file (CSV). "
        "A name reads datasets/<name>/{pipeline,campaign}.yaml; a file is "
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
        help="Campaign config JSON override (defaults to datasets/<name>/campaign.yaml).",
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
        help="Multi-fork batch from datasets/<name>/sweep/*.yaml: mint "
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


def _add_verify_args(p_verify: argparse.ArgumentParser) -> None:
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


def _add_seed_screen_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "dataset",
        help="Inner benchmark whose bank draws are being screened (e.g. 'justlogic-d234').",
    )
    p.add_argument(
        "--seeds",
        dest="seeds",
        type=int,
        nargs="+",
        required=True,
        help="Candidate seed indices to measure (e.g. --seeds 0 1 2 3 4 5).",
    )
    p.add_argument(
        "--n-samples",
        dest="n_samples",
        type=int,
        default=40,
        help="Rows per bank (default 40) — match the panel's `n_samples_origin`, or the "
        "screen measures a bank nobody will run.",
    )
    p.add_argument(
        "--repeat",
        dest="repeat",
        type=int,
        default=3,
        help="Independent origin passes per bank (default 3). They run force_fresh — "
        "the archive is content-addressed, so replays would report a spread of exactly zero. "
        "NOT 1: the verdict compares an exact floor against an origin carrying ~0.08 SE at 40 "
        "rows, so one pass cannot settle a bank near its line. Costs repeat x --n-samples "
        "calls; raise it further for any bank reported UNSETTLED.",
    )


def _add_noise_floor_args(p_noise_floor: argparse.ArgumentParser) -> None:
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
    """Tenant scope + safety flags for ``reset``. Drops campaigns + sessions + the active pointer;
    ``measurements/`` (the DB core) and ``optimizer_reuse/`` are PRESERVED. ``--dry-run`` first."""
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
    """Bare ``python -m promptpotter`` defaults to ``resume`` — the commonest action, shortest invocation."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description=f"{settings.BRAND_SHORT_NAME} optimization CLI. Bare invocation runs "
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
        help="Deterministic A/B replay of the active cycle's campaign: re-derive every "
        "recorded decision (winner / eliminations / L2-L3 triggers) under the CURRENT "
        "engine + scorer, and report where the change stops carrying over — which "
        "branches survive it and where a fork is needed. Zero LLM calls — run a cycle "
        "under one engine/scorer, then `ab` under another to diff.",
    )
    _add_reset_args(
        sub.add_parser(
            "reset",
            help="Drop campaigns/ + sessions/ + active_session.json for the "
            "selected tenant; preserve the two paid caches, measurements/ (DB core) "
            "+ optimizer_reuse/. The escape hatch for cycles "
            "obsoleted by code changes — per-sample measurements survive so the "
            "next `new` hits cache immediately.",
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

    p_compact = sub.add_parser(
        "compact-archive",
        help="Move the fields nothing reads out of candidate measurement rows into a gzip cold "
        "store beside them (`compact`), put them back (`restore`), or delete the store "
        "(`purge-cold`). A measurement row is paid LLM spend, so `compact` never drops a field — "
        "it moves `hit`/`scored`/`objective` plus pipeline_data's `reasoning_trace`, "
        "`result_ranking`, `final_ranking` and `total_time`, and stamps the run header with what "
        "left. `origin` and `round_parent` runs are never touched: they serve the overwhelming "
        "majority of cache replays. Refuses while any cycle can still append. Dry-run by default; "
        "`purge-cold --apply` is the ONE irreversible step. Pure disk work, zero spend.",
    )
    p_compact.add_argument(
        "mode",
        choices=["compact", "restore", "purge-cold"],
        help="Which step to run.",
    )
    p_compact.add_argument(
        "--dataset",
        default=None,
        help="Scope to one dataset (default: every dataset).",
    )
    p_compact.add_argument("--apply", action="store_true", help="Write (default: report only).")

    p_restamp = sub.add_parser(
        "restamp",
        help="Bring on-disk data onto today's shape. (1) Prune knobs the engine no longer "
        "has from every CampaignConfig — the minted snapshots and the dataset templates; "
        "every dropped key is reported with the value its file held. (2) Re-project each "
        "finished cycle's ledger onto the current record shape, dropping what the archive "
        "and the round files already hold and lifting escalation's resume counters onto "
        "the persisted view. A cycle with a live producer is left alone. (3) REPORT whether "
        "every banked round document still loads, grouped by what drifted — read-only, because "
        "pruning cannot restore a renamed field's value, so a repair there would be silently "
        "wrong. The sanctioned remedy after a field rename or a record-shape change, and (3) is "
        "how you find out you need one. Dry-run by default. Pure disk work, zero spend.",
    )
    p_restamp.add_argument(
        "--apply", action="store_true", help="Rewrite the files (default: report only)."
    )

    p_evidence = sub.add_parser(
        "evidence",
        help="What a SET of subjects jointly says: the roster, whether their levels are "
        "comparable at all, the cell/subject/residual decomposition, what the selection can "
        "resolve at its current width, the run-order confound, and (with --ranking) the measured "
        "edits. Read-only, zero spend, no LLM calls; naming a leader, never adopting one.",
    )
    p_evidence.add_argument(
        "dataset",
        nargs="?",
        default="",
        help="Pool every campaign on this dataset. Omit it and pass --subject instead.",
    )
    p_evidence.add_argument(
        "--subject",
        dest="subject",
        action="append",
        default=[],
        help="What to pool, repeated for a set: 'campaign:<id>' (its root origin), "
        "'course:<campaign>/<cycle>' (one branch, at its last elected winner) or "
        "'candidate:<campaign>/<cycle>/<candidate>' (one searchpoint). The campaign id accepts "
        "the same short prefix every other verb does. May span datasets, which the comparability "
        "line then reports on. Overrides the dataset argument. An L4 inner run names the sandbox "
        "chain it lives in, same codec as the API's '?descend=': "
        "';in=<outer_campaign>::<outer_cycle>', one hop per level. A mask rides the same "
        "address, ';'-separated: ';samples=3,7,11' reads every value over those samples only, and "
        "';lens=score:<formula>' (courses only) re-decides the branch's elections under another "
        "criterion — so the record and the counterfactual pool as two channels of one read.",
    )
    p_evidence.add_argument(
        "--config",
        dest="config",
        action="store_true",
        help="Also line the searchpoints up on WHAT THEY ARE — one row per configured key over "
        "each one's resolved node config and prompt fields, differing keys only. Off by default: "
        "a prompt field is the largest thing this read carries.",
    )
    p_evidence.add_argument(
        "--winner-chain",
        dest="winner_chain",
        action="store_true",
        help="Also print the branch behind each course / candidate subject — the winner chain "
        "from its origin to its head, each point read on its own cells. OFF by default: every "
        "point past the origin opens a round document.",
    )
    p_evidence.add_argument(
        "--metric",
        dest="metric",
        # No default spelled here: which metrics exist and which one is the headline are the
        # read's to decide against the selection in hand, so `cmd_evidence` supplies MEASURAND
        # rather than this module keeping a second copy of the name.
        default=None,
        help="Which number to compare on. Unset reads each cell's own headline: the seed's lift "
        "over its origin on the recursion, the sample's fitness elsewhere. The rest are offered "
        "only where the selection carries them — the read prints its own 'Offered here:' line — "
        "and 'expr:<formula>' composes over the names on that same line, e.g. "
        "'expr:lift / latency'.",
    )
    p_evidence.add_argument(
        "--ranking",
        dest="ranking",
        action="store_true",
        help="Also rank the measured edits, in the selected metric. OFF by default: it is the "
        "widest walk here — everything else reads one round-0 document per campaign, while this "
        "opens every round of every campaign selected.",
    )
    p_evidence.add_argument(
        "--top",
        dest="top",
        type=int,
        default=10,
        help="Ranking rows to print (default 10). The full read is always in --json.",
    )

    _add_seed_screen_args(
        sub.add_parser(
            "seed-screen",
            help="Debug diagnostic (NOT a loop feature): score each candidate seed's bank "
            "with the dataset origin and report its constant-answer floor, its reasoning "
            "margin (origin - floor) and the disqualifier — a bank whose floor EXCEEDS "
            "its origin pays a candidate for collapsing to one label. Real spend: "
            "--n-samples target calls per seed, no optimizer calls. Never invoked by "
            "the loop itself.",
        )
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

    p_pause = sub.add_parser(
        "pause",
        help="Ask a running cycle to stop at its next checkpoint (resumable by `resume`). "
        "Fires the same pause-cycle command the webapp's pause control does, so the "
        "interrupt is recorded on the cycle's ledger. Defaults to the active cycle.",
    )
    p_pause.add_argument("--campaign", default="", help="Campaign id (default: the active one).")
    p_pause.add_argument("--cycle", default="", help="Cycle id (default: the active one).")
    p_pause.add_argument(
        "--reason", default="", help="Optional operator-supplied reason, recorded with the command."
    )

    p_set_budget = sub.add_parser(
        "set-budget",
        help="Raise or lower an EXISTING cycle's spend / token ceiling — the same "
        "change-spend-budget command the webapp fires. This is how a budget-halted cycle is "
        "continued: set a higher ceiling, then `resume`. The launch flags only shape a launch. "
        "Clamped against your account allowance; read the armed value off the dashboard.",
    )
    p_set_budget.add_argument(
        "--campaign", default="", help="Campaign id (default: the active one)."
    )
    p_set_budget.add_argument("--cycle", default="", help="Cycle id (default: the active one).")
    p_set_budget.add_argument(
        "--max-usd",
        dest="max_usd",
        type=float,
        default=None,
        help="New USD ceiling. 0 halts after the current round. Omit to leave it untouched.",
    )
    p_set_budget.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=None,
        help="New token ceiling — the unit that survives an unpriced model. 0 halts after the "
        "current round. Omit to leave it untouched.",
    )

    for verb, summary in (
        (
            "archive",
            "Flag a campaign archived — hides from the default sidebar, restorable by "
            "unarchive. The tree does not move.",
        ),
        (
            "delete",
            "Destructively remove a campaign (no recovery); --keep-results spares the keepsake. "
            "Measurements still cache-hit for siblings.",
        ),
        ("unarchive", "Restore an archived campaign to 'active'."),
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

    p_rename = sub.add_parser(
        "rename",
        help="Give a campaign an operator name, shown wherever it is named to a human. "
        "Display only — the campaign id still addresses it. An empty name restores the "
        "dataset-name fallback.",
    )
    p_rename.add_argument("campaign_id", help="Target campaign id ({dataset}__{rand6_hex})")
    # Optional rather than a positional `''`: PowerShell drops an empty argument before
    # argparse ever sees it, so the clear form has to be the ABSENT one to exist at all.
    p_rename.add_argument(
        "label", nargs="?", default="", help="The new name; omit it to clear the name."
    )

    # The cycle/campaign controls the browser could already fire and the terminal could not.
    # Each posts the SAME command kind the webapp posts — the two surfaces share the server's
    # vocabulary and nothing else, which is why these needed no UI arrangement to land.
    p_skip = sub.add_parser(
        "skip-searchpoint",
        help="Cut the candidate currently being scored, at its next sample boundary. The round "
        "carries on with the rest. Defaults to the active cycle.",
    )
    p_skip.add_argument("--campaign", default="", help="Campaign id (default: the active one).")
    p_skip.add_argument("--cycle", default="", help="Cycle id (default: the active one).")

    p_step = sub.add_parser(
        "step-cycle",
        help="Let a paused cycle run a bounded number of rounds, then stop again.",
    )
    p_step.add_argument("--campaign", default="", help="Campaign id (default: the active one).")
    p_step.add_argument("--cycle", default="", help="Cycle id (default: the active one).")
    p_step.add_argument(
        "--rounds", dest="rounds", type=int, default=1, help="How many rounds to run (default 1)."
    )

    p_cleanup = sub.add_parser(
        "cleanup-empty-cycles",
        help="Remove the stub cycles a mint left behind when it never reached round 0.",
    )
    p_cleanup.add_argument("--campaign", default="", help="Campaign id (default: the active one).")
    p_cleanup.add_argument("--cycle", default="", help="Cycle id (default: the active one).")

    p_del_cycle = sub.add_parser(
        "delete-cycle",
        help="Remove ONE named stub cycle — the singular of `cleanup-empty-cycles`. Refuses a "
        "cycle that holds rounds, and refuses one with a live producer (pause it first).",
    )
    p_del_cycle.add_argument(
        "--campaign", default="", help="Campaign id (default: the active one)."
    )
    # The one cycle-scoped verb whose `--cycle` is REQUIRED. Its siblings fall back to the active
    # pointer, which for a delete would make the likeliest typo the destructive one.
    p_del_cycle.add_argument("--cycle", required=True, help="Cycle id to remove.")

    p_allowed = sub.add_parser(
        "set-allowed-models",
        help="Set the models a steered fork may pick from. `resume --steer-model` refuses "
        "against exactly this list, so this is how that refusal is widened from the terminal. "
        "Pass an empty list to clear it.",
    )
    p_allowed.add_argument("campaign_id", help="Target campaign id ({dataset}__{rand6_hex})")
    p_allowed.add_argument(
        "models",
        nargs="?",
        default="",
        help="Comma-separated model ids; omit to clear the list.",
    )

    p_replace = sub.add_parser(
        "replace-dataset",
        help="Version a dataset slug and repoint what referenced it — the terminal half of what "
        "the browser offers on a slug collision, where ingest otherwise asks for a new name.",
    )
    p_replace.add_argument("slug", help="The dataset slug to replace.")

    return parser


def parser_verbs(parser: argparse.ArgumentParser) -> frozenset[str]:
    """The subcommand names registered on ``parser``, so ``campaign_runner`` can assert ``COMMANDS``
    against it at import. They drift one way: a verb with no parser row is silently unreachable."""
    return frozenset(
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    )


__all__ = ["build_parser", "parser_verbs"]
