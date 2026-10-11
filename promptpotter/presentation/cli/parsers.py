"""Imports no ``promptpotter.application`` module: ``--help`` and an argument error load no verb."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import settings
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.infrastructure.identity.migration import registered_or_default_identity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from promptpotter.shared.identity import IdentityContext


class Arg:
    def __init__(self, *flags: str, **spec: Any) -> None:
        self.flags = flags
        self.spec = spec


@dataclass(frozen=True)
class Verb:
    name: str
    handler: str
    help: str
    args: tuple[Arg, ...] = ()


GLOBAL_ARGS = (
    Arg(
        "--tenant",
        default=None,
        help=(
            "Tenant partition under .promptpotter/projects/. Unset → the "
            "registered developer (default-tenant claim marker) or anonymous "
            "'default' if never registered. Pass a slug to override."
        ),
    ),
    Arg(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logs (timestamps, module tags, every INFO line)",
    ),
    Arg(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of human-formatted text",
    ),
)

RUNTIME_HALTS = (
    Arg(
        "--no-wait",
        action="store_true",
        help="Refuse instead of waiting when every run slot on the machine is taken.",
    ),
    Arg(
        "--halt-at",
        dest="halt_at_accuracy",
        type=float,
        default=None,
        metavar="ACC",
        help="Halt when the optimizer's declared pick has accuracy ≥ ACC (e.g. 0.66).",
    ),
    Arg(
        "--spend-budget",
        dest="spend_budget_usd",
        type=float,
        default=None,
        metavar="USD",
        help="Halt when cumulative cycle spend (optimizer + backend) ≥ USD. Sets the "
        "cycle's ceiling, raise or lower, over the dataset's; kept for later resumes.",
    ),
    Arg(
        "--token-budget",
        dest="token_budget",
        type=int,
        default=None,
        metavar="N",
        help="Halt when cumulative cycle tokens (optimizer + backend, in + out) ≥ N. "
        "The model-portable twin of --spend-budget; whichever trips first halts.",
    ),
)

ACTIVE_CAMPAIGN = Arg("--campaign", default="", help="Campaign id (default: the active one).")
ACTIVE_CYCLE = Arg("--cycle", default="", help="Cycle id (default: the active one).")
CAMPAIGN_ID = Arg("campaign_id", help="Target campaign id ({dataset}__{rand6_hex})")


def _declare(parser: argparse.ArgumentParser, args: Iterable[Arg]) -> None:
    for arg in args:
        parser.add_argument(*arg.flags, **arg.spec)


def build_parser(verbs: Iterable[Verb]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description=f"{settings.BRAND_SHORT_NAME} optimization CLI. Bare invocation runs "
        "`resume` (continue the active session). `new [DATASET|FILE]` mints a "
        "fresh campaign — from a dataset name or a raw file it ingests + "
        "origin-resolves. Reads happen by opening the artifact tree "
        "(campaigns/{campaign_id}/) directly.",
    )
    _declare(parser, GLOBAL_ARGS)
    sub = parser.add_subparsers(dest="command", required=False)
    for verb in verbs:
        _declare(sub.add_parser(verb.name, help=verb.help), verb.args)
    return parser


def identity_from_args(args: argparse.Namespace) -> IdentityContext:
    return registered_or_default_identity(args.tenant)


def launch_limits_from_args(args: argparse.Namespace) -> LaunchLimits:
    return LaunchLimits.model_validate(
        {
            "halt_at_accuracy": args.halt_at_accuracy,
            "ceiling": {"usd": args.spend_budget_usd, "tokens": args.token_budget},
        }
    )


__all__ = [
    "ACTIVE_CAMPAIGN",
    "ACTIVE_CYCLE",
    "CAMPAIGN_ID",
    "GLOBAL_ARGS",
    "RUNTIME_HALTS",
    "Arg",
    "Verb",
    "build_parser",
    "identity_from_args",
    "launch_limits_from_args",
]
