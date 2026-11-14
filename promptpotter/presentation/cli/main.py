"""The parser, every verb's row and the dispatch. Imports no ``promptpotter.application`` module and
no verb's: a handler is NAMED and ``main()`` imports the one it dispatches, so ``--help`` and an
argument error load no verb and no use case. A terminal launch is a ``CommandRecord`` like the
browser's."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import importlib
import json
import sys
from collections.abc import Callable, Coroutine, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypeAliasType, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from promptpotter.config.settings import settings
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CampaignPayload,
    CommandKind,
    CyclePayload,
    ReadKind,
)
from promptpotter.domain.export import EXPORT_TIER_INFO, ExportTier
from promptpotter.domain.hierarchy import ListingLifecycle
from promptpotter.domain.phases import StopOutcome
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.run_mode import OnDivergence
from promptpotter.domain.said import Said
from promptpotter.domain.spend import Budget
from promptpotter.domain.verification import VerifyStrategy
from promptpotter.shared.errors import PayloadInvalidError, PotterError
from promptpotter.shared.named_rows import imported


class Arg:
    def __init__(self, *flags: str, **spec: Any) -> None:
        self.flags = flags
        self.spec = spec

    @property
    def dest(self) -> str:
        """Where argparse files the flag's value: its `dest`, else its first long flag's name."""
        if "dest" in self.spec:
            return str(self.spec["dest"])
        flag = next((f for f in self.flags if f.startswith("--")), self.flags[0])
        return flag.lstrip("-").replace("-", "_")


@dataclasses.dataclass(frozen=True)
class Verb:
    name: str
    handler: str
    help: str
    args: tuple[Arg, ...] = ()
    # A read verb's row and its renderer (`module:fn`); its handler is `read:cmd_read`.
    read: ReadKind | None = None
    render: str | None = None
    # A read verb's flags its renderer reads, which no field of the row's model takes.
    render_args: tuple[str, ...] = ()
    # Per payload field of a kind naming this verb, no `Arg` of its name: how the terminal carries it
    # instead (`_unsaid`, checked at import).
    unspelled: Mapping[str, str] = dataclasses.field(default_factory=dict)
    # A written verb dispatching no kind: why no other door carries it.
    terminal_only: str = ""


GLOBAL_ARGS = (
    Arg(
        "--tenant",
        default=None,
        help=(
            "Tenant partition under .promptpotter/projects/. Unset → the "
            "account HOST_ADMIN_EMAIL declares, once it signed in, else "
            "'default'. Pass a slug to override."
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


def _budget_axis(axis: str, cast: Callable[[str], float]) -> Callable[[str], Budget]:
    """A one-axis Budget, so `none` (the lift) stays apart from an omitted flag's ``None``; a
    refusal is the model's, in the words REST's body refuses the same text in."""

    def parse(raw: str) -> Budget:
        try:
            value: object = None if raw.strip().lower() == "none" else cast(raw)
        except ValueError:
            value = raw
        try:
            return PayloadInvalidError.validating(Budget, {axis: value})
        except PayloadInvalidError as refused:
            raise argparse.ArgumentTypeError(refused.message) from refused

    return parse


BUDGET_AXES = (
    Arg(
        "--max-usd",
        dest="max_usd",
        type=_budget_axis("usd", float),
        default=None,
        metavar="USD|none",
        help="Campaign USD ceiling (optimizer + backend, every live cycle); 0 halts after the "
        "current round, `none` leaves only your account's allowance. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-tokens",
        dest="max_tokens",
        type=_budget_axis("tokens", int),
        default=None,
        metavar="N|none",
        help="Campaign token ceiling (in + out) — the unit that survives an unpriced model; "
        "whichever ceiling trips first halts. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-rounds",
        dest="max_rounds",
        type=_budget_axis("rounds", int),
        default=None,
        metavar="N|none",
        help="L1 round cap, read at the next round boundary; `none` lifts it, so the spend "
        "ceiling governs. Omit to leave it as it stands.",
    ),
    Arg(
        "--max-wall-clock",
        dest="max_wall_clock",
        type=_budget_axis("wall_clock_s", float),
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
    # A global reads after the verb too; unsaid there, it leaves the one said before it.
    after = tuple(
        Arg(*arg.flags, **{**arg.spec, "default": argparse.SUPPRESS}) for arg in GLOBAL_ARGS
    )
    sub = parser.add_subparsers(dest="command", required=False)
    for verb in verbs:
        verb_parser = sub.add_parser(verb.name, help=verb.help, description=verb.help)
        _declare(verb_parser, (*after, *verb.args))
    return parser


def budget_from_args(args: argparse.Namespace) -> Budget:
    """Names only the axes a flag gave: an omitted one is left to the layers beneath."""
    given = (args.max_usd, args.max_tokens, args.max_rounds, args.max_wall_clock)
    return Budget().over(*(axis for axis in given if axis is not None))


def said_text(row: Said) -> str:
    """A served row as one terminal line: every part it carries, worded as served."""
    text = row.label if row.value is None else f"{row.label} {row.value}"
    text = text if row.sub is None else f"{text} ({row.sub})"
    return text if row.detail is None else f"{text} — {row.detail}"


@dataclasses.dataclass
class CommandResult:
    data: dict[str, Any] | None = None
    human: str | None = None
    # What the process exits: all a shell, a deploy script or a spawning server reads.
    exit_code: int = 0


# The grammar `launch.py::_set_body` walks: a `--set`, and each knob of an `--arm` after its optimizer.
SET_GRAMMAR = "PATH=VALUE"
ARM_GRAMMAR = f"KEY=OPTIMIZER[,{SET_GRAMMAR}…]"


def _read_verb(
    name: str,
    read: ReadKind,
    render: str,
    *,
    help: str,
    args: tuple[Arg, ...] = (),
    render_args: tuple[str, ...] = (),
) -> Verb:
    """A read verb: its flags' dests are the row's fields, else its renderer's *render_args*."""
    return Verb(
        name,
        "cli.commands.reads:cmd_read",
        help=help,
        args=args,
        read=read,
        render=render,
        render_args=render_args,
    )


def _int_list_arg(raw: str) -> list[int]:
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected indices like `0,2`, got {raw!r}") from exc


def _switch_arg(raw: str) -> bool:
    if raw not in ("on", "off"):
        raise argparse.ArgumentTypeError(f"expected `on` or `off`, got {raw!r}")
    return raw == "on"


_WRITTEN_VERBS: tuple[Verb, ...] = (
    Verb(
        "new",
        "cli.commands.launch:cmd_new",
        unspelled={
            "filename": "the FILE positional: its name",
            "blob": "the FILE positional: its bytes",
            "draft_id": "the draft the FILE form's ingest-upload mints, named back on each later kind",
            "patch": "`--set`, `--slug` and `--task-*`, folded into one patch",
            "optimization": "`--set KNOB=VALUE`, folded per knob",
            "campaign_config": "`--config FILE`'s `campaign` section",
            "head_to_head": "the `head-to-head` verb mints one; a FILE form launches one campaign",
            "rerun": "a FILE form buys only the turn its draft owes; the browser's re-run forces one",
        },
        help="Mint a fresh campaign from a dataset NAME or a raw FILE (CSV). A "
        "name uses an authored datasets/<name>/; a file is ingested → "
        "origin-resolved → committed as a tenant dataset → run (headless parity "
        "with the web onboarding). Every invocation mints a brand-new campaign "
        "(campaign_id is timestamp-derived — collision-free, no discriminator).",
        args=(
            Arg(
                "dataset",
                nargs="?",
                default=None,
                help="Dataset name under ./datasets/ OR a path to a raw file (CSV). "
                "A name reads datasets/<name>/{pipeline,campaign}.yaml; a file is "
                "ingested → origin-resolved → committed as a tenant dataset → run. "
                "Omit (name form) only if you pass --dataset-name explicitly.",
            ),
            Arg(
                "--dataset-name",
                default=None,
                help="Explicit dataset name (alternative to positional). Required if "
                "no positional dataset is given.",
            ),
            Arg(
                "--config",
                default=None,
                help="A definition file whose `campaign` section replaces the dataset's own.",
            ),
            Arg("--task-file", default=None, help="Override the dataset's task.md"),
            Arg("--task-text", default=None, help="Override the dataset's task.md inline"),
            Arg(
                "--slug",
                default=None,
                help="(file form) Dataset slug under projects/{tenant}/datasets/ "
                "(default: derived from the filename).",
            ),
            Arg(
                "--set",
                dest="sets",
                action="append",
                default=[],
                metavar=SET_GRAMMAR,
                help="Repeatable. PATH is a check-in edit's own dotted path (`EditDraftPatch`). "
                "Both forms take the campaign knobs — `--set config.optimization.optimizer=capo`, "
                "`--set config.optimization.nodes.<node>.config.<knob>=VALUE`; a launch's bound "
                "is `--max-*`'s. The file form also confirms an origin field or the criterion, "
                "e.g. `--set raw_task_description='map names to codes'`, "
                "`--set column_query=input` or `--set config.scoring.matcher=exact`, applied "
                "before the resolver runs.",
            ),
            NO_WAIT,
            *BUDGET_AXES,
        ),
    ),
    Verb(
        "head-to-head",
        "cli.commands.launch:cmd_head_to_head",
        unspelled={
            "dataset_name": "the DATASET positional",
        },
        help="Mint the arms of one controlled head-to-head on a dataset NAME in one act, each a "
        "campaign on the same origin and bench set that searches prompts alone, and run them side "
        "by side, each in its own process, until every arm ends. Every arm is planned, priced on "
        "the budget axes below (each arm's own) and admitted before any is minted: a refused arm "
        "leaves none behind. An id already declared takes these as further arms, held to its "
        "record. `evidence` reads the result.",
        args=(
            Arg("dataset", help="The dataset name every arm runs on."),
            Arg(
                "--head-to-head",
                required=True,
                metavar="ID",
                help="The head-to-head's id: letters, digits, '-' and '_'.",
            ),
            Arg(
                "--arm",
                dest="arms",
                action="append",
                required=True,
                metavar=ARM_GRAMMAR,
                help="Repeatable, one per arm: its key, the optimizer it runs, and that "
                "optimizer's knobs by their dotted path under `new --set`'s "
                "`config.optimization` — `--arm capo=capo "
                "--arm capo-b5=capo,nodes.<node>.config.max_blocks=5`; an arm on an earlier arm's "
                "treatment, this act's or a standing one's, is its next replicate, on its own seed. No `halt_at_fitness`: every arm runs the budget below to its end.",
            ),
            *BUDGET_AXES,
        ),
    ),
    Verb(
        "resume",
        "cli.commands.launch:cmd_resume",
        unspelled={
            "mode.from_round": "`--from ROUND`",
            "mode.on_divergence": "`--on-divergence`",
            "round": "`--rewind ROUND`",
            "reason": "`--rewind-reason`",
            "keep_rounds": "`--rewind` keeps them, a bare `--steer` does not",
            "seed.pipeline_overlay": "`--steer NODE.PARAM=VALUE`, folded per node",
            "seed.origin_prompts": "a terminal fork edits no prompt; the browser's steer-fork does",
            "seed.config_overrides": "a fork's caps are its parent's remainder; `--max-*` bound it",
            "seed.node_narrowing": "a terminal fork narrows no node; the browser's steer-fork does",
            "candidate_id": "a terminal fork starts from a round's line, never a named candidate",
        },
        help="Continue the active campaign. Bare `resume` picks up where it "
        "left off; flags handle rewind and divergence.",
        args=(
            Arg(
                "--campaign",
                default="",
                help="Campaign id, 6-hex suffix, or unambiguous prefix (default: the active one). Its "
                "cycle's own session is resumed; the active pointer is not consulted.",
            ),
            Arg(
                "--cycle",
                default="",
                help="Cycle id (default: the active one, or the named campaign's only cycle).",
            ),
            Arg(
                "--from",
                dest="resume_from_round",
                type=int,
                default=None,
                metavar="ROUND",
                help="Resume after round N (archives rounds > N, reloads trial_N). "
                "Omit to pick up from the latest completed round.",
            ),
            Arg(
                "--on-divergence",
                choices=[o.value for o in OnDivergence],
                default=OnDivergence.HALT.value,
                help="Where the replay departs the record: `halt` (the default), `accept` it and "
                "run on, or `fork` a sibling cycle at the divergent round and run that.",
            ),
            Arg(
                "--rewind",
                dest="rewind_to_round",
                type=int,
                default=None,
                metavar="ROUND",
                help="Mint a sibling cycle at ROUND (OPERATOR_REWIND trigger), retarget "
                "the active pointer, and start optimization on the fork. Parent cycle is "
                "preserved intact. Contrast with `--from N` which rewinds in place.",
            ),
            Arg(
                "--rewind-reason",
                dest="rewind_reason",
                type=str,
                default="",
                metavar="STR",
                help="One-line audit-trail reason recorded on the OPERATOR_REWIND fork; "
                "ignored unless `--rewind ROUND` is set.",
            ),
            Arg(
                "--steer",
                dest="steer",
                action="append",
                default=None,
                metavar="NODE.PARAM=VALUE",
                help="Mint an operator-steered fork whose seed overlay sets one node param "
                "(repeatable) — any key the served node-config rows carry: "
                "`--steer llm_only.model=gpt-5`, `--steer llm_only.temperature=0.2`, "
                '`--steer llm_only.output_schema=\'{"type":"object",...}\'`. A value is read '
                "in the param's declared type, so a number, bool or object is spelled as JSON. "
                "Steering a node to a gateway or to a model it does not permit is a babysit act: "
                "it requires the `campaign.babysit` capability and grades the fork's runs C.",
            ),
            Arg(
                "--step-rounds",
                dest="step_rounds",
                type=int,
                default=None,
                metavar="N",
                help="Run N rounds (1-100), then pause the cycle as `stepped` — a step, not a "
                "budget stop (contrast `--max-rounds`).",
            ),
            NO_WAIT,
            *BUDGET_AXES,
        ),
    ),
    Verb(
        "ab",
        "cli.commands.measure:cmd_ab",
        terminal_only="it replays recorded decisions under the engine this checkout runs, which no server door holds",
        help="Deterministic A/B replay of a campaign (the active one, or --campaign): "
        "re-derive every recorded decision (winner / eliminations / L2-L3 triggers) under "
        "the CURRENT engine + scorer, and report where the change stops carrying over — "
        "which branches survive it and where a fork is needed. Zero LLM calls — run a "
        "cycle under one engine/scorer, then `ab` under another to diff.",
        args=(
            Arg(
                "--campaign",
                default="",
                help="Campaign id, 6-hex suffix, or unambiguous prefix (default: the active one).",
            ),
            Arg(
                "--cycle",
                default="",
                help="Cycle whose round 0 calibrates the δ ruler (default: the active cycle, or the "
                "named campaign's root cycle).",
            ),
        ),
    ),
    Verb(
        "reset",
        "cli.commands.writes:cmd_reset",
        terminal_only="it drops run data under the operator's own hand, which no tenant door may",
        help="Drop the selected tenant's run data and every file no row of "
        "`infrastructure/store/data_root.py` names; keep survivors (who an account is, what it "
        "holds and was charged) and the caches, so the next `new` hits cache. Refused, exit 3, "
        "while a run in reach is live or a draft is held. The escape hatch after a reshape of "
        "run data.",
        args=(
            Arg(
                "--all-tenants",
                dest="all_tenants",
                action="store_true",
                help="Reset the whole data root: every tenant, every sandbox and the install's "
                "own run data (default: only the tenant named by --tenant and its sandboxes).",
            ),
            Arg(
                "--yes",
                action="store_true",
                help="Skip the y/N confirmation prompt. Required for non-interactive use.",
            ),
            Arg(
                "--dry-run",
                dest="dry_run",
                action="store_true",
                help="List the paths that would be removed; touch nothing. Recommended first run.",
            ),
        ),
    ),
    Verb(
        "verify",
        "cli.commands.measure:cmd_verify",
        unspelled={
            "candidate_id": "the SUBJECT positional's last part, beside its campaign and cycle",
        },
        help="Re-score one campaign candidate on search cells it has never met and bank "
        "the pass on its cycle's ledger. Use to doublecheck whether a confidence-locked "
        "candidate's verdict generalises beyond the round's leader-locked sample budget. "
        "Moves no round and no election.",
        args=(
            Arg(
                "subject",
                help="The searchpoint, as `evidence` addresses one: "
                "'candidate:<campaign>/<cycle>/<candidate_id>', with ';in=<c::y~…>' for an L4 inner "
                "cycle. The id is the key of its row in the round file's `arms`.",
            ),
            Arg(
                "--strategy",
                dest="strategy",
                choices=get_args(VerifyStrategy),
                default="random",
                help="Which unseen cells to buy: 'random', or 'hard' — the highest-δ cells on the "
                "cycle's ruler first, which read BELOW the candidate's level by construction, so the "
                "paired lift is the number to read there.",
            ),
            Arg(
                "--samples",
                dest="samples",
                type=int,
                default=None,
                help="Number of additional samples to score. Default: DERIVED — the per-candidate "
                "round budget, lifted by the rounds run since this cycle's last verification and "
                "capped by what is still unmeasured. A larger explicit count is REFUSED, naming the "
                "budget. Samples this candidate already has in the cross-cycle archive are skipped.",
            ),
            Arg(
                "--seed",
                dest="seed",
                type=int,
                default=None,
                help="RNG seed for reproducible sample picks (default: random).",
            ),
        ),
    ),
    Verb(
        "reindex",
        "cli.commands.writes:cmd_reindex",
        terminal_only="disk repair of the data root this process opens",
        help="Rebuild the measurement index (measurements/index.jsonl) from the detail "
        "files, dropping index rows no detail file stands behind. The index is derived, so this "
        "loses nothing — use after a crash mid-append. Refuses while any cycle can still append. "
        "Dry-run by default: names what it would index, writes nothing. Pure disk work, zero spend.",
        args=(Arg("--apply", action="store_true", help="Write (default: report only)."),),
    ),
    Verb(
        "compact-archive",
        "cli.commands.writes:cmd_compact_archive",
        terminal_only="`inventory` reads the archive's disk across every tenant, which no kind's "
        "mode carries",
        help="Count what the archive holds (`inventory`), move the fields nothing reads out of "
        "candidate measurement rows into a gzip cold store beside them (`compact`), put them back "
        "(`restore`), or delete the store (`purge-cold`). `inventory` writes nothing and refuses "
        "nothing: runs, cells, bytes and replay rate by dataset, run-label family and age, plus "
        "the index rows carrying no detail file, which is what makes every other count an upper "
        "bound. It is what a reclaim is sized against, so it is taken BEFORE a bulk delete — the "
        "delete destroys its own evidence. "
        "A measurement row is paid LLM spend, so `compact` never drops a field — "
        "it moves `hit`/`scored`/`objective` plus pipeline_data's `reasoning_trace`, "
        "`result_ranking`, `final_ranking` and `total_time`, and stamps the run header with what "
        "left. Only `panel` runs (a candidate's own walk) are touched: `origin` and `parent` "
        "serve the overwhelming majority of cache replays. Refuses while any cycle can still append. Dry-run by default; "
        "`purge-cold --apply` is the ONE irreversible step. Pure disk work, zero spend.",
        args=(
            Arg(
                "mode",
                choices=["inventory", "compact", "restore", "purge-cold"],
                help="Which step to run.",
            ),
            Arg("--dataset", default=None, help="Scope to one dataset (default: every dataset)."),
            Arg(
                "--apply",
                action="store_true",
                help="Write (default: report only). `inventory` never writes and ignores it.",
            ),
        ),
    ),
    _read_verb(
        "evidence",
        ReadKind.EVIDENCE,
        "cli.commands.reads:evidence_human",
        render_args=("top",),
        help="What a SET of subjects jointly says: the campaigns' bench headlines head-to-head, "
        "never paired where one bench set did not grade them all, then the roster, whether their "
        "levels are comparable at all, the cell/subject/residual decomposition, what the "
        "selection can resolve at its current width, the run-order confound, and (with --ranking) "
        "the measured edits. Read-only, zero spend, no LLM calls; naming a leader, never adopting one.",
        args=(
            Arg(
                "dataset",
                nargs="?",
                default="",
                help="Pool every campaign on this dataset, beside any --subject names. With "
                "';in=<outer_campaign>::<outer_cycle>' (the same codec as a subject's), the L4 inner "
                "campaigns of that outer cycle's sandbox.",
            ),
            Arg(
                "--subject",
                dest="subject",
                action="append",
                default=[],
                help="What to pool, repeated for a set: 'campaign:<id>' (its root origin), "
                "'course:<campaign>/<cycle>' (one branch, at its last elected winner) or "
                "'candidate:<campaign>/<cycle>/<candidate>' (one searchpoint). The campaign id accepts "
                "the same short prefix every other verb does. May span datasets, which the comparability "
                "line then reports on. An L4 inner run names the sandbox "
                "chain it lives in, same codec as the API's '?descend=': "
                "';in=<outer_campaign>::<outer_cycle>', one hop per level. A mask rides the same "
                "address, ';'-separated: ';samples=3,7,11' reads every value over those samples only, and "
                "';lens=score:<formula>' (courses only) re-decides the branch's elections under another "
                "criterion — so the record and the counterfactual pool as two channels of one read.",
            ),
            Arg(
                "--config",
                dest="config",
                action="store_true",
                help="Also line the searchpoints up on WHAT THEY ARE — one row per configured key over "
                "each one's resolved node config and prompt fields, differing keys only. Off by default: "
                "a prompt field is the largest thing this read carries.",
            ),
            Arg(
                "--winner-chain",
                dest="winner_chain",
                action="store_true",
                help="Also print the branch behind each course / candidate subject — the winner chain "
                "from its origin to its head, each point read on its own cells. OFF by default: every "
                "point past the origin opens a round file.",
            ),
            Arg(
                "--metric",
                dest="metric",
                # Unset leaves the row's own default, so the name has one copy.
                default=None,
                help="Which number to compare on. Unset reads each cell's own headline: the seed's lift "
                "over its origin on the recursion, the sample's fitness elsewhere. The rest are offered "
                "only where the selection carries them — the read prints its own 'Offered here:' line — "
                "and 'expr:<formula>' composes over the names on that same line, e.g. "
                "'expr:lift / latency'.",
            ),
            Arg(
                "--grid",
                dest="grid",
                default=None,
                metavar="ROW,COL",
                help="Cross two of the factors this selection varies on and print the pooled cell at each "
                "coordinate — 'which combination is fastest', rather than which level is on average. Names "
                "come from the factor block this read already prints, e.g. "
                "'--grid dataset,llm_only.model'. Every OTHER factor is marginalised into the cells and "
                "named as such; to hold one fixed instead, narrow the selection to its level. Two axes at "
                "any number of factors — a third is a decision about that factor, never a third dimension.",
            ),
            Arg(
                "--ranking",
                dest="ranking",
                action="store_true",
                help="Also rank the measured edits, in the selected metric. OFF by default: it is the "
                "widest walk here — everything else reads one round-0 document per campaign, while this "
                "opens every round of every campaign selected.",
            ),
            Arg(
                "--top",
                dest="top",
                type=int,
                default=10,
                help="Ranking rows to print (default 10). The full read is always in --json.",
            ),
        ),
    ),
    _read_verb(
        "price-head-to-head",
        ReadKind.HEAD_TO_HEAD_PRICE,
        "cli.commands.reads:price_human",
        help="Price a planned head-to-head before it is minted: every budget point, arm and "
        "replicate a plan file declares, in dollars, calls and wall clock, at the cell price the "
        "archive measured on this bank and its models and each optimizer's measured rounds, then "
        "print the one command that mints them. No LLM call, zero spend.",
        args=(
            Arg(
                "plan",
                type=Path,
                help="A head-to-head plan (YAML, `HeadToHeadPlan`), e.g. "
                "paper/plans/comparability.yaml.",
            ),
        ),
    ),
    Verb(
        "bench",
        "cli.commands.measure:cmd_bench",
        help="Grade the campaign's origin and its selection on the held-out bench rows and bank "
        "the headline — the pass a `bench_trigger: manual` campaign (the default) sends only "
        "when asked, on the cycle holding its line. Reuses a pass the line already holds; refuses "
        "a line with a run in flight, a split holding nothing out and a selection already graded "
        "— the served status's own answer (the course's `facts.bench.status`). Spends: two passes "
        "over the bench rows.",
        args=(ACTIVE_CAMPAIGN,),
    ),
    _read_verb(
        "cycles",
        ReadKind.HIERARCHY,
        "cli.commands.reads:cycles_human",
        help="Every cycle of every active campaign you own that loads (`--lifecycle` for the "
        "others), with its run state (`run_phase`) — derived at each read and written to "
        "no file, so this is where a terminal reads which runs are live, then each head-to-head's "
        "arms by key. Read-only, zero spend.",
        args=(
            Arg("--campaign", default=None, help="Only this campaign's cycles."),
            Arg("--attached", action="store_true", help="Only cycles a producer process holds."),
            Arg(
                "--lifecycle",
                choices=get_args(ListingLifecycle.__value__),
                default=None,
                help="Which campaigns: 'active' (unset), 'archived', 'deleted', 'unstarted' "
                "(the open check-ins) or 'all'.",
            ),
            Arg(
                "--inside",
                dest="descend",
                default=None,
                metavar="CAMPAIGN::CYCLE[~…]",
                help="List an L4 inner run's cycles instead: the sandbox chain it lives in, one hop per "
                "level, same codec as the API's '?descend=' and `evidence`'s ';in='. The only verb that "
                "takes it — every run-control verb acts on the outer run, and `verify` / `evidence` "
                "address an inner searchpoint through their subject.",
            ),
        ),
    ),
    _read_verb(
        "export",
        ReadKind.CYCLE_EXPORT,
        "cli.commands.reads:export_written",
        render_args=("out",),
        help="One reading of a cycle, chosen by what you do with it next — the same tiers the "
        "webapp's copy and download offer and `GET …/cycles/{id}/export?tier=` serves. `chat` and "
        "`report` print text to paste; `deploy` prints `export.json`; `notebook` and `package` "
        "write a folder. Read-only, zero spend.",
        args=(
            ACTIVE_CAMPAIGN,
            ACTIVE_CYCLE,
            Arg(
                "--tier",
                required=True,
                choices=get_args(ExportTier.__value__),
                help="; ".join(f"{tier}: {info.label}" for tier, info in EXPORT_TIER_INFO.items()),
            ),
            Arg(
                "--out",
                type=Path,
                default=None,
                metavar="DIR",
                help="Write the files under DIR/<name>/ (default: print a one-file tier, and write "
                "a several-file tier under ./<name>/).",
            ),
        ),
    ),
    _read_verb(
        "dashboard",
        ReadKind.CYCLE_DASHBOARD,
        "cli.commands.reads:dashboard_human",
        help="One cycle's live dashboard, the body `GET …/cycles/{id}/dashboard` serves every "
        "door: its run phase (derived at each read, not the file's `declared_phase`), why it is "
        "paused, the verb its admission offers, its rounds and spend. `--json` prints the body. "
        "Read-only, zero spend.",
        args=(
            ACTIVE_CAMPAIGN,
            ACTIVE_CYCLE,
            Arg(
                "--at",
                type=int,
                default=None,
                help="A line of the cycle's own ledger: the state then.",
            ),
        ),
    ),
    _read_verb(
        "command",
        ReadKind.COMMAND,
        "cli.commands.reads:command_human",
        help="Where one command you sent stands, by the id its door answered: running, done, "
        "refused, or each job a launch launched. Read-only, zero spend.",
        args=(Arg("command_id", help="The command's id, as `--json` or its door answered it."),),
    ),
    _read_verb(
        "machine-status",
        ReadKind.MACHINE_STATUS,
        "cli.commands.reads:machine_status_human",
        help="How many campaigns the machine runs and admits, and where your queued launches "
        "stand in line. Read-only, zero spend.",
    ),
    Verb(
        "close-admission",
        "cli.commands.reads:cmd_close_admission",
        terminal_only="a landing's act on this box, before its checkout moves",
        help="A landing's first act, before its checkout moves: close this box's admission to every "
        "process not running TARGET, until the restarted server runs it. Prints `closed …`, or one "
        "`held …` line per live run on other code and closes nothing. Zero spend.",
        args=(Arg("target", help="The full commit the landing checks out."),),
    ),
    Verb(
        "land-data",
        "cli.commands.writes:cmd_land_data",
        terminal_only="a landing's one write to the data root, under the code it lands",
        help="A landing's one write to the data root, under THIS code: stage each survivor "
        "migration (`application/maintenance/migrate.py::MIGRATIONS`), load every file under the "
        "data root as its row of `infrastructure/store/data_root.py` shapes it would then stand, and "
        "write the migrations and drop each cache file that no longer loads only if nothing refuses. "
        "A file no row names, or a survivor or run file that does not load, refuses: exit 3, "
        "nothing written. Zero spend.",
        args=(
            Arg(
                "--reset",
                action="store_true",
                help="Also drop every account's run data and every file no row names, as "
                "`reset --all-tenants` does; refused while a run is live.",
            ),
            Arg(
                "--dry-run",
                dest="dry_run",
                action="store_true",
                help="Print what the landing would write, drop and refuse; write nothing.",
            ),
        ),
    ),
    Verb(
        "trail",
        "cli.commands.reads:cmd_trail",
        terminal_only="the operator's audit read across accounts, outside every tenant's door",
        help="What an account did, oldest first, in UTC: its sign-ins, its consent, every upload, every attempt "
        "the server refused and every command, the terminal's own included. Bare, this tenant's; "
        "with an EMAIL, every account on the box that carries it. Read-only, zero spend.",
        args=(Arg("email", nargs="?", default="", help="An account's email address."),),
    ),
    Verb(
        "offer-extras",
        "cli.commands.writes:cmd_offer_extras",
        terminal_only="what the deploy script installs, read before any server runs",
        help="The pyproject extras the datasets this deployment offers (DATASET_OFFER) need to "
        "fetch their samples, one per line — what `deploy-linux/update.sh` installs. Writes nothing but a missing data root.",
    ),
    Verb(
        "health",
        "cli.commands.reads:cmd_health",
        terminal_only="the box's own probe of what it answers for",
        help="Check everything this box answers for — each remote backend, each configured "
        "sign-in provider — one line each, led by held / broken / unverified; exits 1 on a broken "
        "one. A change of state is recorded and told to the admin bot. Network only, zero LLM spend.",
    ),
    Verb(
        "mcp",
        "mcp.server:cmd_mcp",
        terminal_only="it is the host door, over this terminal's stdio",
        help="Serve this workspace to a local host assistant over MCP stdio: every command and "
        "read as a tool, campaigns and courses as resources, the check-in as a prompt — each "
        "call as this terminal's own principal. Runs until the host closes stdin. Needs the "
        "`mcp` extra.",
    ),
    Verb(
        "pin-roster",
        "cli.commands.writes:cmd_pin_roster",
        terminal_only="the one fetch of Harbor's registry, written into the package's resources",
        help="Pin a published Harbor dataset's task roster into the harbor connector's resources "
        "(`harbor-roster.<dataset>@<version>.json`) — the ONE fetch of Harbor's registry, so no "
        "run ever dials it. Refuses to overwrite a pin the registry has since moved: a panel is "
        "never re-cut under the name it had; delete the file to re-cut on purpose. Needs the "
        "`harbor` extra. Network only, zero LLM spend.",
        args=(
            Arg("dataset", help="The dataset's registry name (`harbor dataset list`)."),
            Arg("version", help="Its published version — a pin names one, never 'latest'."),
        ),
    ),
)


def _field_args(name: str, field: FieldInfo) -> tuple[Arg, ...]:
    annotation: Any = field.annotation
    # A `type` alias is spelled by what it stands for.
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    if annotation is Budget:
        return BUDGET_AXES
    flag = f"--{name.replace('_', '-')}"
    spec: dict[str, Any] = {"help": field.description}
    # Never `is bool`: past a final class, mypy narrows `type[Any]` away and drops every later arm.
    if isinstance(annotation, type) and issubclass(annotation, bool):
        return (Arg(flag, dest=name, action="store_true", **spec),)
    if get_origin(annotation) is Literal:
        spec["choices"] = get_args(annotation)
    elif annotation is int:
        spec["type"] = int
    elif annotation == bool | None:
        spec |= {"type": _switch_arg, "metavar": "on|off"}
    elif get_origin(annotation) is list:
        (item,) = get_args(annotation)
        if isinstance(item, type) and issubclass(item, BaseModel):
            spec |= {"type": Path, "metavar": "FILE.YAML"}
        else:
            spec |= {"type": _int_list_arg, "metavar": "0,2"}
    elif annotation is not str:
        raise TypeError(f"`{name}` has no terminal spelling: {annotation}")
    if not field.is_required():
        return (Arg(flag, dest=name, default=argparse.SUPPRESS, **spec),)
    if annotation is str and not any(getattr(m, "min_length", 0) for m in field.metadata):
        # `''` is a value here, and PowerShell drops an empty argument before argparse sees it.
        return (Arg(name, nargs="?", default="", **spec),)
    return (Arg(name, **spec),)


def command_verb(kind: CommandKind) -> Verb:
    """The kind's payload model IS the verb: its docstring the help, its fields the arguments."""
    model = PAYLOAD_MODEL_FOR_KIND[kind]
    address: tuple[Arg, ...] = ()
    if issubclass(model, CyclePayload):
        cycle = ACTIVE_CYCLE if model.implied_target else Arg("--cycle", required=True)
        address = (ACTIVE_CAMPAIGN, cycle)
    elif issubclass(model, CampaignPayload):
        address = (CAMPAIGN_ID,)
    fields = (
        arg
        for name, field in model.model_fields.items()
        if name not in CyclePayload.model_fields
        for arg in _field_args(name, field)
    )
    assert kind.cli_verb is not None
    return Verb(
        kind.cli_verb,
        "cli.commands.writes:cmd_command",
        help=" ".join((model.__doc__ or "").split()),
        args=(*address, *fields),
    )


def _paths(model: type[BaseModel], prefix: str = "") -> dict[str, FieldInfo]:
    """Each field by its dotted path, a nested model's own fields beside it."""
    paths: dict[str, FieldInfo] = {}
    for name, field in model.model_fields.items():
        paths[path := prefix + name] = field
        nested = field.annotation
        if isinstance(nested, type) and issubclass(nested, BaseModel) and nested is not Budget:
            paths |= _paths(nested, f"{path}.")
    return paths


def _unsaid(verb: Verb) -> list[str]:
    """What *verb* leaves unsaid: a payload field, nested ones too, with no `Arg` or `unspelled`
    reason on it or a model holding it, or the reverse."""
    if verb.read is not None:
        return []
    kinds = [kind for kind in CommandKind if kind.cli_verb == verb.name]
    if not kinds:
        return [] if verb.terminal_only else [f"`{verb.name}` dispatches no kind and says not why"]
    dests = {arg.dest for arg in verb.args}
    fields = {
        name: field
        for kind in kinds
        for name, field in _paths(PAYLOAD_MODEL_FOR_KIND[kind]).items()
        if name.split(".")[0] not in CyclePayload.model_fields
    }
    budgets = {name for name, field in fields.items() if field.annotation is Budget}
    said, holders = dests | verb.unspelled.keys(), {name.rpartition(".")[0] for name in fields}
    unsaid = [
        f"`{verb.name}` spells no `{name}`"
        for name in sorted(fields.keys() - budgets - holders)
        if not any(".".join(name.split(".")[:n]) in said for n in range(1, name.count(".") + 2))
    ]
    if budgets and not set(BUDGET_AXES) <= set(verb.args):
        unsaid.append(f"`{verb.name}` takes a budget without `BUDGET_AXES`")
    unsaid += [
        f"`{verb.name}` says why it does not spell `{name}`, which it spells or no kind has"
        for name in sorted(verb.unspelled.keys() - (fields.keys() - dests))
    ]
    return unsaid


if unsaid := [line for verb in _WRITTEN_VERBS for line in _unsaid(verb)]:
    raise TypeError("; ".join(unsaid))

_WRITTEN = {verb.name for verb in _WRITTEN_VERBS}
VERBS: tuple[Verb, ...] = (
    *_WRITTEN_VERBS,
    *(command_verb(k) for k in CommandKind if k.cli_verb and k.cli_verb not in _WRITTEN),
)


# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


# A verb's handler and renderer are named under it: `mcp`'s lives with its server.
VERB_PACKAGE = "promptpotter.presentation"
COMMANDS: dict[str, str] = {verb.name: verb.handler for verb in VERBS}
_PARSER = build_parser(VERBS)


def _handler(verb: str) -> Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]:
    return cast(
        "Callable[[argparse.Namespace], Coroutine[Any, Any, CommandResult]]",
        imported(VERB_PACKAGE, COMMANDS[verb]),
    )


def main() -> None:

    parser = _PARSER
    args = parser.parse_args()

    if args.command is None:
        # Named as a string like the handlers: `--help` loads no use case.
        listing = importlib.import_module("promptpotter.application.cycle_addressing")
        if not listing.holds_active_pointer(tenant=args.tenant):
            print(
                f"Welcome to {settings.BRAND_SHORT_NAME}.\n\n"
                "Pick a verb to get started:\n"
                "  promptpotter new <dataset>   mint a fresh campaign on the named dataset\n"
                "  promptpotter new <file.csv>  ingest a raw file → resolve origin → mint + run\n"
                "  promptpotter resume          continue the active campaign\n"
                "  promptpotter verify          re-score a candidate on more samples\n"
                "  promptpotter bench           grade the origin and the selection on held-out rows\n"
                "  promptpotter ab              re-derive the active cycle's decisions under the current engine\n\n"
                "Run `promptpotter <verb> --help` for per-verb options.\n"
                f"Docs: {settings.BRAND_DOCS_URL}"
            )
            return
        # Appended to the ORIGINAL argv, so `resume`'s defaults populate without dropping the globals before the verb.
        args = parser.parse_args([*sys.argv[1:], "resume"])

    runner = asyncio.Runner()
    try:
        # Every verb lands, its registries completed as the landing imports; its refusal is this
        # one catch's to say, as a verb's is. Named as a string like the handlers: `--help` loads
        # no use case.
        landing = importlib.import_module("promptpotter.application.maintenance.landing")
        landing.land_at_entry(every_treatment=False)
        # A launch verb's handler runs its preamble and THEN answers the coroutine: a preamble's
        # refusal is this one catch's to say, as the run's is.
        result = runner.run(_handler(args.command)(args))
    except PotterError as exc:
        runner.close()
        refused = Refusal.of(exc)
        if args.json_output:
            # One stream: the refusal is the answer, and stderr stays empty of it.
            print(json.dumps(refused.model_dump(mode="json")))
        else:
            # What this terminal alone can do about it rides the refusal as a note, said after it.
            said = [f"Error: {refused.message}", *getattr(exc, "__notes__", ())]
            print("\n\n".join(said), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # The cycle already finalized itself as PAUSED; closing a loop a second SIGINT stopped mid-unwind raises.
        with contextlib.suppress(RuntimeError, KeyboardInterrupt):
            runner.close()
        reason = f" — {exc}" if str(exc) else ""
        print(f"\nPaused{reason}. Completed work is saved.", file=sys.stderr)
        lines = cast(
            "Callable[[str], list[str]]",
            imported("promptpotter.application.commands", "dispatcher:interrupted_lines"),
        )
        print("\n".join(lines(args.command)), file=sys.stderr)
        sys.exit(StopOutcome.PAUSED.exit_code)
    runner.close()
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    elif result.human:
        # An empty one prints nothing: a verb whose stdout was a wire (`mcp`) leaves it clean.
        print(result.human)
    if result.exit_code:
        sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
