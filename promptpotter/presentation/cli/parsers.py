"""Argparse schema for ``init`` / ``optimize`` / ``compare``.

Imported by ``campaign_runner.main()``. Help text is verbose by design — this
is the operator-facing surface.
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


def _add_init_args(p_init: argparse.ArgumentParser) -> None:
    """Backend + dataset + task overrides for ``init``."""
    p_init.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_init.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p_init.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p_init.add_argument("--dataset-name", default=None)
    p_init.add_argument("--excel-path", default=None)
    p_init.add_argument("--config", default=None, help="Campaign config JSON file")
    p_init.add_argument(
        "--task-file", default=None, help="Override datasets/<name>/task_description.md"
    )
    p_init.add_argument(
        "--task-text", default=None, help="Override datasets/<name>/task_description.md inline"
    )


def _add_optimize_args(p_opt: argparse.ArgumentParser) -> None:
    """Resume / divergence / mode flags for ``optimize``."""
    p_opt.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume after round N (archives rounds > N, reloads trial_N). "
        "Omit to resume from the latest completed round.",
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
        help="M10 cheap-round_data mode: baseline → 1 full scored round → "
        "1 generation-only round (variants emitted, no scoring) → halt. "
        "index.json::final.mode lands as 'sweep' so the leaderboard can "
        "pair sweep cycles with their full counterparts.",
    )
    mode_group.add_argument(
        "--diag",
        dest="diag",
        action="store_true",
        help="M10 diagnostic mode: baseline → 1 full scored round → "
        "force L2-context (regardless of stall) → 1 generation-only "
        "round 2 (with L2 overrides applied, no scoring) → halt. "
        "index.json::final.mode lands as 'diag' and final.diag carries "
        "L2's evolved L1 surface for the operator to promote.",
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
    """Argparse schema for ``init`` + ``optimize`` + ``compare``."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="PromptPotter optimization CLI — init creates a session+cycle, "
        "optimize runs a campaign against it. Reads happen by opening the artifact "
        "tree (sessions/{id}/, campaigns/{cycle_id}/) directly.",
    )
    _add_global_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    _add_init_args(sub.add_parser("init", help="Create session+cycle for a dataset"))
    _add_optimize_args(
        sub.add_parser("optimize", help="Run optimization loop on the active session")
    )
    _add_compare_args(
        sub.add_parser(
            "compare",
            help="PoBB-compare cycle winners across the family with adaptive top-up. "
            "Each cycle's index.json::final.winner_pipeline_params is one arm; "
            "under-measured arms get one extra score per round until a decisive "
            "P(best) emerges or the topup budget is exhausted.",
        )
    )

    return parser
