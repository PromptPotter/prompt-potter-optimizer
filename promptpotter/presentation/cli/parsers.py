"""Argparse schema for ``optimize`` / ``compare`` / ``sweep``.

Imported by ``campaign_runner.main()``. Help text is verbose by design — this
is the operator-facing surface.

``optimize`` is the single entry verb. With ``--config`` (or ``--dataset-name``)
it mints a fresh session+cycle and runs from round 0. Without, it resumes
the active session.
"""

from __future__ import annotations

import argparse

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Tenant + session + verbosity flags shared across every command."""
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant partition under .promptpotter/projects/ (default: 'default')",
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


def _add_fresh_init_args(p: argparse.ArgumentParser) -> None:
    """Backend + dataset + task overrides used when ``optimize`` mints a fresh
    session+cycle.

    Presence of ``--config`` or ``--dataset-name`` switches ``optimize`` into
    fresh mode (run init body, then start at round 0). Absence keeps the
    resume default.
    """
    p.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p.add_argument(
        "--dataset-name",
        default=None,
        help="Dataset under ./datasets/. Triggers fresh-init mode "
        "(mint new session+cycle, start at round 0). "
        "Auto-loads datasets/<name>/campaign.json when --config is omitted.",
    )
    p.add_argument("--excel-path", default=None)
    p.add_argument(
        "--config",
        default=None,
        help="Campaign config JSON file. Triggers fresh-init mode "
        "(mint new session+cycle, start at round 0).",
    )
    p.add_argument("--task-file", default=None, help="Override datasets/<name>/task_description.md")
    p.add_argument(
        "--task-text", default=None, help="Override datasets/<name>/task_description.md inline"
    )


def _add_optimize_args(p_opt: argparse.ArgumentParser) -> None:
    """Resume / divergence / mode flags for ``optimize``.

    These are resume-path-only — combining any with ``--config`` /
    ``--dataset-name`` is rejected at the top of ``cmd_optimize``.
    """
    p_opt.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume after round N (archives rounds > N, reloads trial_N). "
        "Omit to resume from the latest completed round. "
        "Resume-path only — rejected with --config / --dataset-name.",
    )
    p_opt.add_argument(
        "--no-divergence-check",
        dest="no_divergence_check",
        action="store_true",
        help="On resume, rescore but skip the decision-replay halt.",
    )
    p_opt.add_argument(
        "--fork-on-divergence",
        dest="fork_on_divergence",
        action="store_true",
        help="On divergence, mint a sibling cycle (with parent_cycle_id) "
        "and re-run the divergent round under the current scorer.",
    )
    mode_group = p_opt.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sweep",
        dest="sweep",
        action="store_true",
        help="M10 cheap-round_data mode: origin → 1 full scored round → "
        "1 generation-only round (variants emitted, no scoring) → halt. "
        "index.json::final.mode lands as 'sweep' so the leaderboard can "
        "pair sweep cycles with their full counterparts.",
    )
    mode_group.add_argument(
        "--diag",
        dest="diag",
        action="store_true",
        help="M10 diagnostic mode: origin → 1 full scored round → "
        "force L2-context (regardless of stall) → 1 generation-only "
        "round 2 (with L2 overrides applied, no scoring) → halt. "
        "index.json::final.mode lands as 'diag' and final.diag carries "
        "L2's evolved L1 surface for the operator to promote.",
    )
    p_opt.add_argument(
        "--refresh-rates",
        dest="refresh_rates",
        action="store_true",
        help="Force-refetch the LLM provider rate table from upstream "
        "(otherwise the cached table is reused for 24 h, with the "
        "in-repo bundled floor as fallback).",
    )


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


def _add_sweep_l1_prompt_args(p: argparse.ArgumentParser, *, allow_multi: bool = True) -> None:
    """Shared L1-prompt selector + label.

    ``allow_multi=True`` lets round1/round2 take a comma-list of variants;
    each variant runs in its own fork. ``time-to`` keeps singular semantics
    (only one halt-on-accuracy run makes sense per invocation).
    """
    if allow_multi:
        p.add_argument(
            "--l1-prompts",
            dest="l1_prompts",
            default=None,
            help="Comma-separated paths to PromptTemplate JSON files to A/B "
            "in one invocation. Each variant runs in its own OPERATOR_SWEEP "
            "fork; results share a sweep_id. Omit to use the currently "
            "loaded l1_generate template.",
        )
    else:
        p.add_argument(
            "--l1-prompt",
            dest="l1_prompt",
            default=None,
            help="Path to a PromptTemplate JSON to swap in for l1_generate. "
            "Omit to use the currently loaded template.",
        )
    p.add_argument(
        "--l1-prompt-label",
        dest="l1_prompt_label",
        default=None,
        help="Operator label for the L1 meta-prompt revision (e.g. 'l1_v3'). "
        "Recorded on the result; the hash is always derived from the loaded prompt.",
    )


def _add_sweep_args(p_sweep: argparse.ArgumentParser) -> None:
    """M10 sweep-toolkit verbs. Wraps ``optimize`` with halt gates +
    per-round panel stats and persists one result JSON per run under
    ``archive/sweeps/{l1_meta_prompt_hash}/{dataset}/``. See
    ``docs/specs/m10-sweep-toolkit.md``.
    """
    sweep_sub = p_sweep.add_subparsers(dest="sweep_verb", required=True)

    # time-to ----------------------------------------------------------------
    p_time_to = sweep_sub.add_parser(
        "time-to",
        help="Run optimize, halt on target accuracy / max-rounds / max-spend; write one result JSON.",
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
        "--max-spend",
        dest="max_spend",
        type=float,
        default=None,
        help="Halt when cumulative cycle spend (USD, optimizer + backend) ≥ this value.",
    )
    _add_sweep_l1_prompt_args(p_time_to, allow_multi=False)
    _add_sweep_slice_args(p_time_to)
    p_time_to.add_argument(
        "--refresh-rates",
        dest="refresh_rates",
        action="store_true",
        help="Force-refetch the LLM provider rate table (same semantics as ``optimize --refresh-rates``).",
    )

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
    _add_sweep_l1_prompt_args(p_round1)
    _add_sweep_slice_args(p_round1)
    p_round1.add_argument(
        "--refresh-rates",
        dest="refresh_rates",
        action="store_true",
        help="Force-refetch the LLM provider rate table.",
    )

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
    _add_sweep_l1_prompt_args(p_round2)
    _add_sweep_slice_args(p_round2)
    p_round2.add_argument(
        "--refresh-rates",
        dest="refresh_rates",
        action="store_true",
        help="Force-refetch the LLM provider rate table.",
    )

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


def _add_compare_args(p_cmp: argparse.ArgumentParser) -> None:
    """Cycle list + PoBB knobs for ``compare``."""
    p_cmp.add_argument(
        "cycle_ids",
        nargs="*",
        help="cycle ids to compare (each contributes one arm). "
        "Omit (or pass --all) to auto-discover every cycle in the active "
        "family with a final winner.",
    )
    p_cmp.add_argument(
        "--all",
        action="store_true",
        dest="all_family",
        help="Auto-discover every cycle in the active family with a final winner. "
        "Implied when no positional cycle_ids are given.",
    )
    p_cmp.add_argument("--epsilon", type=float, default=0.05, help="PoBB threshold (default 0.05)")
    p_cmp.add_argument(
        "--max-topups",
        type=int,
        default=16,
        dest="max_topups",
        help="Upper bound on extra LLM calls (default 16; -1 = unbounded, Ctrl+C to stop).",
    )
    p_cmp.add_argument(
        "--n-min-per-arm",
        type=int,
        default=4,
        dest="n_min_per_arm",
        help="Sample floor before SE-driven selection kicks in (default 4)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Argparse schema for ``optimize`` + ``compare`` + ``sweep``."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="PromptPotter optimization CLI — `optimize --config <path>` "
        "mints a fresh session+cycle and runs from round 0; `optimize` alone "
        "resumes the active session. Reads happen by opening the artifact tree "
        "(sessions/{id}/, campaigns/{cycle_id}/) directly.",
    )
    _add_global_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_optimize = sub.add_parser(
        "optimize",
        help="Run optimization loop. With --config / --dataset-name: mint a "
        "fresh session+cycle and start at round 0. Without: resume the active "
        "session.",
    )
    _add_fresh_init_args(p_optimize)
    _add_optimize_args(p_optimize)

    _add_compare_args(
        sub.add_parser(
            "compare",
            help="PoBB-compare cycle winners across the family with adaptive top-up. "
            "Each cycle's index.json::final.winner_pipeline_params is one arm; "
            "under-measured arms get one extra score per round until a decisive "
            "P(best) emerges or the topup budget is exhausted.",
        )
    )
    _add_sweep_args(
        sub.add_parser(
            "sweep",
            help="M10 sweep-toolkit verbs — cheap-grade L1 meta-prompt edits. "
            "Each verb wraps optimize with halt gates and persists one result "
            "JSON; see docs/specs/m10-sweep-toolkit.md.",
        )
    )

    return parser
