"""Imports no ``promptpotter.application`` module and no sibling: a handler is NAMED, so ``--help`` loads no verb.

``new`` and ``resume`` dispatch their command as DATA (``CommandDispatcher(inline=Inline(…))``), so a
terminal launch is a ``CommandRecord`` like the browser's; every other command verb derives from its
payload model (``command_verb``). Run-invocation (``HeldRun.run_inline`` vs ``HeldRun.detach``) is the
only CLI↔web difference, and each terminal-only flag says why no payload carries it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from promptpotter.config.paths import SHARED_CACHE_DIRS
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CampaignPayload,
    CommandKind,
    CyclePayload,
)
from promptpotter.domain.cycle_paths import CyclePath, decode_cycle_path
from promptpotter.domain.diagnostic_run import RescoreCount, rescore_count
from promptpotter.domain.export import EXPORT_TIER_INFO, ExportTier
from promptpotter.domain.spend import Budget
from promptpotter.domain.verify_pass import VerifyStrategy
from promptpotter.presentation.cli.parsers import (
    ACTIVE_CAMPAIGN,
    ACTIVE_CYCLE,
    BUDGET_AXES,
    CAMPAIGN_ID,
    NO_WAIT,
    Arg,
    Verb,
)
from promptpotter.shared.errors import BadRequestError


def _rescore_count_arg(raw: str) -> RescoreCount:
    try:
        return rescore_count(int(raw))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a rescore count >= 2, got {raw!r}") from exc


def _inside_arg(raw: str) -> CyclePath:
    try:
        return decode_cycle_path(raw)
    except BadRequestError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _int_list_arg(raw: str) -> list[int]:
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected indices like `0,2`, got {raw!r}") from exc


_WRITTEN_VERBS: tuple[Verb, ...] = (
    Verb(
        "new",
        "new:cmd_new",
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
                metavar="FIELD=VALUE",
                help="Repeatable. Both forms take the campaign knobs — `--set optimizer=capo`, "
                "`--set budget.rounds=3` (`none` lifts an axis), `--set nodes.<node>.<knob>=VALUE`. "
                "The file form also "
                "confirms an origin field, e.g. `--set task_description='map names to codes'` or "
                "`--set column.query=input`, applied before the resolver runs.",
            ),
            Arg(
                "--arm",
                default=None,
                metavar="HEAD_TO_HEAD:KEY",
                help="Mint as a controlled arm of a head-to-head, declared by its first arm off that "
                "arm's instrument and budget; a later arm on any other is refused.",
            ),
            Arg(
                "--diag",
                dest="diag",
                action="store_true",
                help="Diagnostic mode: origin → 1 full scored round → force "
                "task-framing refinement (regardless of stall) → 1 generation-only "
                "round 2 (refinement overrides applied, no scoring) → halt. "
                "index.json::final.mode lands as 'diag'.",
            ),
            Arg(
                "--halt-at",
                dest="halt_at_accuracy",
                type=float,
                default=None,
                metavar="ACC",
                help="Halt when the optimizer's declared pick has accuracy ≥ ACC (e.g. 0.66). The "
                "campaign's own knob, so `--set halt_at_accuracy=ACC` spelled short.",
            ),
            NO_WAIT,
            *BUDGET_AXES,
        ),
    ),
    Verb(
        "resume",
        "resume_command:cmd_resume",
        help="Continue the active campaign. Bare `resume` picks up where it "
        "left off; flags handle rewind / divergence / diagnostic modes.",
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
                "--no-check",
                dest="no_divergence_check",
                action="store_true",
                help="On resume, rescore but skip the decision-replay halt.",
            ),
            Arg(
                "--fork-on-divergence",
                dest="fork_on_divergence",
                action="store_true",
                help="On divergence, mint a sibling cycle (with parent_cycle_id) "
                "and re-run the divergent round under the current scorer.",
            ),
            Arg(
                "--diag",
                dest="diag",
                action="store_true",
                help="Diagnostic mode (see `new --diag`). On a previously-completed "
                "diag cycle, branches off a counted sibling.",
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
                "it requires the `campaign.babysit` capability and grades the fork's runs C. CLI "
                "twin of the web steer-fork (`POST /commands/fork-cycle`), same seam + gate.",
            ),
            NO_WAIT,
            *BUDGET_AXES,
        ),
    ),
    Verb(
        "ab",
        "ab:cmd_ab",
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
        "reset:cmd_reset",
        help="Drop campaigns/ + active_session.json for the "
        "selected tenant; preserve its archive home's paid caches ("
        + ", ".join(f"{d}/" for d in SHARED_CACHE_DIRS)
        + "). The escape hatch for cycles "
        "obsoleted by code changes — per-sample measurements survive so the "
        "next `new` hits cache immediately.",
        args=(
            Arg(
                "--all-tenants",
                dest="all_tenants",
                action="store_true",
                help="Reset every tenant under .promptpotter/projects/ "
                "(default: only the tenant named by --tenant).",
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
        needs_registries=False,
    ),
    Verb(
        "verify",
        "verify:cmd_verify",
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
        "reindex:cmd_reindex",
        help="Rebuild the measurement index (measurements/index.jsonl) from the detail "
        "files and GC orphaned runs. The index is derived, so this loses nothing — use "
        "after a crash mid-append or to reclaim orphaned bytes. Pure disk work, zero spend.",
        needs_registries=False,
    ),
    Verb(
        "compact-archive",
        "maintenance:cmd_compact_archive",
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
    Verb(
        "restamp",
        "restamp:cmd_restamp",
        help="Bring on-disk data onto today's shape. (1) Prune knobs the engine no longer "
        "has from every CampaignConfig — the minted snapshots and the dataset templates; "
        "every dropped key is reported with the value its file held. (2) Re-project each "
        "finished cycle's ledger onto the current record shape, dropping what the archive "
        "and the round files already hold. A cycle with a live producer is left alone. (3) "
        "Walk every file the measurement archive writes: prune retired keys, and remove a "
        "record its model still refuses with what it alone describes. (4) "
        "REPORT whether every ledger, manifest and archive file still loads — "
        "read-only, because "
        "pruning cannot restore a renamed field's value, so a repair there would be silently "
        "wrong. The sanctioned remedy after a field rename or a record-shape change, and (4) is "
        "how you find out you need one. Dry-run by default. Pure disk work, zero spend.",
        args=(
            Arg("--apply", action="store_true", help="Rewrite the files (default: report only)."),
        ),
    ),
    Verb(
        "evidence",
        "evidence:cmd_evidence",
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
                # No default here: `cmd_evidence` supplies MEASURAND, so the name has one copy.
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
    Verb(
        "seed-screen",
        "seed_screen:cmd_seed_screen",
        help="Debug diagnostic (NOT a loop feature): score each candidate seed's bank "
        "with the dataset origin and report its constant-answer floor, its reasoning "
        "margin (origin - floor) and the disqualifier — a bank whose floor EXCEEDS "
        "its origin pays a candidate for collapsing to one label. Real spend: "
        "--n-samples target calls per seed, no optimizer calls. Never invoked by "
        "the loop itself.",
        args=(
            Arg(
                "dataset",
                help="ANY dataset whose seeded bank draws are being screened (e.g. 'justlogic/d234'). "
                "Not an L4-only verb: it resolves that dataset's own definition and origin, so a "
                "screen needs no inner loop and no self-optimizing campaign.",
            ),
            Arg(
                "--seeds",
                dest="seeds",
                type=int,
                nargs="+",
                required=True,
                help="Candidate seed indices to measure (e.g. --seeds 0 1 2 3 4 5).",
            ),
            Arg(
                "--n-samples",
                dest="n_samples",
                type=int,
                default=40,
                help="Rows per bank (default 40) — match the panel's `n_samples_origin`, or the "
                "screen measures a bank nobody will run.",
            ),
            Arg(
                "--repeat",
                dest="repeat",
                type=int,
                default=3,
                help="Independent origin passes per bank (default 3). They run force_fresh — "
                "the archive is content-addressed, so replays would report a spread of exactly zero. "
                "NOT 1: the verdict compares an exact floor against an origin carrying ~0.08 SE at 40 "
                "rows, so one pass cannot settle a bank near its line. Costs repeat x --n-samples "
                "calls; raise it further for any bank reported UNSETTLED.",
            ),
            Arg(
                "--parallel",
                dest="parallel",
                type=int,
                default=1,
                help="Rows to hold in flight within each pass (default 1, sequential). A screen has no "
                "round and no cycle, so this is a LAUNCH parameter held for the whole run rather than a "
                "press the browser arms and a round spends — that control belongs to `new`/`resume`, "
                "which have a round to spend it at. Clamped to the backend's own ceiling.",
            ),
            Arg(
                "--model",
                dest="model",
                default=None,
                help="Pin the solver over the dataset's own declared model, the way an L4 pool "
                "environment pins it (`panel.inner_model`): the screen then answers for "
                "the (dataset, solver) pair a pool cell would run, with the dataset's effort, output "
                "cap and temperature kept. Absent: the dataset's declared model.",
            ),
            Arg(
                "--provider",
                dest="provider",
                default=None,
                help="The gateway --model is asked through (`panel.inner_provider`). "
                "Needs --model.",
            ),
        ),
    ),
    Verb(
        "price-head-to-head",
        "head_to_head_price:cmd_price_head_to_head",
        help="Price a planned head-to-head before it is minted: every budget point, arm and "
        "replicate a plan file declares, in dollars, calls and wall clock, at the cell price the "
        "archive measured on this bank and its models and each optimizer's measured rounds, then "
        "print the one command that mints them. No LLM call, zero spend.",
        args=(
            Arg(
                "plan",
                help="A head-to-head plan (YAML, `HeadToHeadPlan`), e.g. "
                "paper/plans/comparability.yaml.",
            ),
        ),
    ),
    Verb(
        "noise-floor",
        "noise_floor:cmd_noise_floor",
        help="Debug diagnostic (NOT a loop feature): re-score a campaign's cached "
        "origin --k times with force_fresh and report the mean+CI spread — the "
        "backend's own run-to-run noise. On a pp-self cycle this measures the "
        "true inner-recursion noise floor. kx real spend; does not mutate the "
        "source cycle and is never invoked by the loop itself.",
        args=(
            Arg(
                "campaign",
                help="Campaign id, 6-hex suffix, or unambiguous prefix "
                "(e.g. 'promptpotter-self__ca6d4d' or 'ca6d4d').",
            ),
            Arg(
                "--cycle",
                dest="cycle",
                default=None,
                help="Cycle id (full or prefix) when the campaign has more than one cycle. "
                "Omit when the campaign has exactly one cycle.",
            ),
            Arg(
                "--k",
                dest="k",
                type=_rescore_count_arg,
                default=rescore_count(3),
                help="Number of force_fresh re-scores of the cached origin (default 3, at least 2). "
                "kx real spend — on a pp-self cycle each re-score re-runs the full inner "
                "recursion, so keep k small.",
            ),
        ),
    ),
    Verb(
        "probe-reasoning",
        "probe_reasoning:cmd_probe_reasoning",
        help="Debug diagnostic (NOT a loop feature): ask ONE model which reasoning rungs it "
        "honours and print the `registry._MODEL_PROFILES` row the readings support. A "
        "catalogue publishes that `reasoning_effort` EXISTS and never which values it takes, "
        "so this is the only way that table gets filled. ~6 cheap calls of real spend; writes "
        "nothing but its bills, and the loop never invokes it.",
        args=(
            Arg("model", help="Model id, e.g. openai/gpt-oss-20b (a :nitro suffix is fine)"),
            Arg(
                "--provider",
                default="openrouter",
                help="Which gateway answers (default: openrouter).",
            ),
        ),
    ),
    Verb(
        "bench",
        "bench:cmd_bench",
        help="Grade the campaign's origin and its selection on the held-out bench rows and bank "
        "the headline — the pass a `bench_trigger: manual` campaign (the default) sends only "
        "when asked, on the cycle holding its line. Reuses a pass the line already holds; refuses "
        "a line with a run in flight, a split holding nothing out and a selection already graded "
        "— the served status's own answer (the course's `BenchStat.status`). Spends: two passes "
        "over the bench rows.",
        args=(ACTIVE_CAMPAIGN,),
    ),
    Verb(
        "cycles",
        "cycles:cmd_cycles",
        help="Every cycle of every campaign you own that loads, with its run state (`run_phase`) — derived at each read and written to "
        "no file, so this is where a terminal reads which runs are live, then each head-to-head arm "
        "as its `--arm` address. Read-only, zero spend.",
        args=(
            Arg("--campaign", default="", help="Only this campaign's cycles."),
            Arg("--attached", action="store_true", help="Only cycles a producer process holds."),
            Arg(
                "--inside",
                type=_inside_arg,
                default=(),
                metavar="CAMPAIGN::CYCLE[~…]",
                help="List an L4 inner run's cycles instead: the sandbox chain it lives in, one hop per "
                "level, same codec as the API's '?descend=' and `evidence`'s ';in='. The only verb that "
                "takes it — every run-control verb acts on the outer run, and `verify` / `evidence` "
                "address an inner searchpoint through their subject.",
            ),
        ),
    ),
    Verb(
        "export",
        "export:cmd_export",
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
                choices=get_args(ExportTier),
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
    Verb(
        "machine-status",
        "machine_status:cmd_machine_status",
        help="How many campaigns the machine runs and admits, and where your queued launches "
        "stand in line. Read-only, zero spend.",
    ),
    Verb(
        "trail",
        "trail:cmd_trail",
        help="What an account did, oldest first, in UTC: its sign-ins, its consent, every upload, every attempt "
        "the server refused and every command, the terminal's own included. Bare, this tenant's; "
        "with an EMAIL, every account on the box that carries it. Read-only, zero spend.",
        args=(Arg("email", nargs="?", default="", help="An account's email address."),),
        needs_registries=False,
    ),
    Verb(
        "offer-extras",
        "offer:cmd_offer_extras",
        help="The pyproject extras the datasets this deployment offers (DATASET_OFFER) need to "
        "fetch their samples, one per line — what `deploy-linux/update.sh` installs. Read-only.",
        needs_registries=False,
    ),
    Verb(
        "health",
        "machine_status:cmd_health",
        help="Check everything this box answers for — each remote backend, each configured "
        "sign-in provider — one line each, led by held / broken / unverified; exits 1 on a broken "
        "one. A change of state is recorded and told to the admin bot. Network only, zero LLM spend.",
    ),
    Verb(
        "pin-roster",
        "pin_roster:cmd_pin_roster",
        help="Pin a published Harbor dataset's task roster into the harbor connector's resources "
        "(`harbor-roster.<dataset>@<version>.json`) — the ONE fetch of Harbor's registry, so no "
        "run ever dials it. Refuses to overwrite a pin the registry has since moved: a panel is "
        "never re-cut under the name it had; delete the file to re-cut on purpose. Needs the "
        "`harbor` extra. Network only, zero LLM spend.",
        args=(
            Arg("dataset", help="The dataset's registry name (`harbor dataset list`)."),
            Arg("version", help="Its published version — a pin names one, never 'latest'."),
        ),
        needs_registries=False,
    ),
)


def _field_args(name: str, field: FieldInfo) -> tuple[Arg, ...]:
    annotation = field.annotation
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
        "command:cmd_command",
        help=" ".join((model.__doc__ or "").split()),
        args=(*address, *fields),
    )


_WRITTEN = {verb.name for verb in _WRITTEN_VERBS}
VERBS: tuple[Verb, ...] = (
    *_WRITTEN_VERBS,
    *(command_verb(k) for k in CommandKind if k.cli_verb and k.cli_verb not in _WRITTEN),
)
