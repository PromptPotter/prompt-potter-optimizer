"""Imports no ``promptpotter.application`` module: ``--help`` and an argument error load no verb."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import settings
from promptpotter.domain.spend import Budget
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from promptpotter.domain.command_kinds import ReadKind


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
    # False only for a verb that parses no ledger, banked round or registered member.
    needs_registries: bool = True
    # A read verb's row and its renderer (`module:fn`); its handler is `read:cmd_read`.
    read: ReadKind | None = None
    render: str | None = None
    # A read verb's flags its renderer reads, which no field of the row's model takes.
    render_args: tuple[str, ...] = ()


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

NO_WAIT = Arg(
    "--no-wait",
    action="store_true",
    help="Refuse instead of waiting when every run slot on the machine is taken.",
)


def _json_value(raw: str) -> object:
    """*raw* as a request body would carry it: a JSON scalar, else the text itself."""
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return value


def _budget_axis(axis: str) -> Callable[[str], Budget]:
    """A one-axis Budget, so `none` (the lift) stays apart from an omitted flag's ``None``; the
    text read as a request body's JSON value, and refused in the words its `Budget` is."""

    def parse(raw: str) -> Budget:
        value = None if raw.strip().lower() == "none" else _json_value(raw)
        try:
            return PayloadInvalidError.validating(Budget, {axis: value})
        except PayloadInvalidError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parse


BUDGET_AXES = (
    Arg(
        "--max-usd",
        dest="max_usd",
        type=_budget_axis("usd"),
        default=None,
        metavar="USD|none",
        help="Campaign USD ceiling (optimizer + backend, every live cycle); 0 halts after the "
        "current round, `none` leaves only your account's allowance. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-tokens",
        dest="max_tokens",
        type=_budget_axis("tokens"),
        default=None,
        metavar="N|none",
        help="Campaign token ceiling (in + out) — the unit that survives an unpriced model; "
        "whichever ceiling trips first halts. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-rounds",
        dest="max_rounds",
        type=_budget_axis("rounds"),
        default=None,
        metavar="N|none",
        help="L1 round cap, read at the next round boundary; `none` lifts it, so the spend "
        "ceiling governs. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-wall-clock",
        dest="max_wall_clock",
        type=_budget_axis("wall_clock_s"),
        default=None,
        metavar="S|none",
        help="Wall-clock bound in seconds for each launch; a remote cell still open at the edge "
        "is cut. Omit to leave it as it stands.",
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
        _declare(sub.add_parser(verb.name, help=verb.help, description=verb.help), verb.args)
    return parser


def budget_from_args(args: argparse.Namespace) -> Budget:
    """Names only the axes a flag gave: an omitted one is left to the layers beneath."""
    given = (args.max_usd, args.max_tokens, args.max_rounds, args.max_wall_clock)
    return Budget().over(*(axis for axis in given if axis is not None))
