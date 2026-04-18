"""Argparse schema for the CLI — pure data, no runtime logic.

Separated from ``campaign_runner`` so the subcommand registry is a single
flat file you can scan to learn the surface.
"""

from __future__ import annotations

import argparse

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)

# signal name → (control-file key, value) — used by ``control`` subparser
# choices AND by ``commands.control.cmd_control`` dispatch.
SIGNAL_ACTIONS: dict[str, tuple[str, str | bool]] = {
    "pause": ("requested_state", "pause"),
    "resume": ("requested_state", "resume"),
    "stop": ("requested_state", "stop"),
    "pause-before-l2": ("pause_before_l2_scoring", True),
    "no-pause-l2": ("pause_before_l2_scoring", False),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="CLI campaign runner for PromptPotter optimization",
    )
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
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize services and create session")
    p_init.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_init.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p_init.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p_init.add_argument("--dataset-name", default=None)
    p_init.add_argument("--excel-path", default=None)
    p_init.add_argument("--config", default=None, help="Campaign config JSON file")
    p_init.add_argument("--skip-baseline", action="store_true")

    p_tc = sub.add_parser("set-task", help="Decompose and set task description")
    p_tc.add_argument("--task-file", default=None)
    p_tc.add_argument("--task-text", default=None)

    p_recon = sub.add_parser("recon", help="Run reconnaissance pass (sensitivity scan)")
    p_recon.add_argument("--variants-file", required=True, help="Recon variants JSON")
    p_recon.add_argument("--sample-size", type=int, default=None)

    sub.add_parser("show-recon", help="Show recon analytics and seed campaign")
    p_opt = sub.add_parser("optimize", help="Run optimization loop")
    p_opt.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume the active cycle from after round N. Archives trial "
        "files for rounds > N into archived/resumed_at_<ts>/, rebuilds the "
        "trial index, and loads trial_N as the restart baseline. Omit to "
        "resume from the latest completed round (default).",
    )

    p_ctl = sub.add_parser("control", help="Write control signal to dashboard")
    p_ctl.add_argument(
        "signal",
        choices=list(SIGNAL_ACTIONS.keys()),
        help="Signal to send to the running optimizer",
    )

    p_prof = sub.add_parser("profile", help="Manage backend profile (per-backend defaults)")
    p_prof.add_argument("--backend-id", default="local")
    prof_mode = p_prof.add_mutually_exclusive_group()
    prof_mode.add_argument(
        "--show", action="store_true", default=True, help="Show profile (default)"
    )
    prof_mode.add_argument(
        "--save", action="store_true", help="Save active session config as profile"
    )
    prof_mode.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a profile field")

    p_res = sub.add_parser("show-results", help="Show results and optionally save")
    p_res.add_argument("--save", action="store_true")

    sub.add_parser("show-status", help="Emit raw JSON dashboard state")

    p_mig = sub.add_parser(
        "migrate",
        help="Migrate .promptpotter/ v2 (backend-axis) layout to v3 (tenant/cycle/library)",
    )
    p_mig.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the move plan without touching the filesystem",
    )
    p_mig.add_argument(
        "--from-path",
        default=None,
        help="Override .promptpotter/projects root (default: repo-local)",
    )

    return parser
