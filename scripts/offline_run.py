"""The one supported OFFLINE run: a real campaign per installed optimizer on ``justlogic/d234``,
every network edge answered in-process, zero spend. What it is for, what it pins and how to read
it: ``docs/developer/offline-run.md``.

    PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py [--optimizer NAME ...]
    python scripts/offline_run.py --digests
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import atexit
import contextlib
import copy
import dataclasses
import functools
import hashlib
import inspect
import itertools
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import types
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

# Its own tree, never the venv's editable one: a child's cwd is its workspace, so a relative
# PYTHONPATH falls through to whichever tree installed the venv.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import yaml
from last_green import OFFLINE_RUN, OFFLINE_RUN_READS, LastGreen
from offline_world import (
    LABELS,
    MARKER,
    NOT_A_KEY,
    PROVIDER_KEYS,
    WORLD,
    FakeBackend,
    FakeLLM,
    Heal,
    Router,
    Said,
    completion,
    draw,
    install_network,
    invalid,
    shown_text,
)
from processes import (
    REPO,
    RamBudget,
    end_children_with_this_process,
    long_path,
    queue_behind_siblings,
    replace_changed,
)

from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_operator_fork
from promptpotter.application.caller import Caller
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.measuring import held_out
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import (
    CycleDashboardParams,
    answer_read,
    host_dispatcher,
    run_campaign,
    run_head_to_head,
)
from promptpotter.application.evidence.head_to_head import GuardState
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.host_surface import CallTool, HostSession
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.maintenance.landing import (
    check_every_treatment,
    complete_registries,
)
from promptpotter.application.optimizer_manifest import (
    bench_choice,
    manifest_directories,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.optimizers.fence import FENCE_CLOSE, FENCE_OPEN_PREFIX
from promptpotter.application.optimizers.nodes import (
    BEHAVIOUR_KEYS_FILE,
    NODE_MEMBERS,
    BehaviourKeys,
    member_package_dir,
)
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.scoring.measurement_log import cells_scope, measurement_log
from promptpotter.application.served_dashboard import ServedDashboard
from promptpotter.config.paths import (
    MEASUREMENTS_DIR,
    PACKAGE_ROOT,
    PROJECTS_DIR,
    default_projects_root,
    install_datasets_root,
    optimizer_manifest_path,
    user_data_root,
)
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import Settings
from promptpotter.domain.arm_reading import ScoreboardRow
from promptpotter.domain.bench import BenchScore, BenchTrigger
from promptpotter.domain.campaign import Compare
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.command_events import WORKSPACE_SUBJECT, CommandRecord, subject_key
from promptpotter.domain.command_kinds import (
    ArmRequest,
    CommandKind,
    CommandPayload,
    Door,
    ForkCyclePayload,
    HeadToHeadRequest,
    HeldOutPayload,
    LookaheadPress,
    MintCampaignPayload,
    PauseCyclePayload,
    ReadKind,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
)
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, Subject
from promptpotter.domain.draft_edit import CampaignLayer, OptimizationOverrides
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.origin_panel import RoundEnding
from promptpotter.domain.phases import RunPhase, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.pipeline_manifest import prompt_key
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.records.run_records import CycleStart, OriginGatePass
from promptpotter.domain.results import RoundOutcome
from promptpotter.domain.ruler import RulerOwn
from promptpotter.domain.run_mode import OnDivergence, RunMode
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.run_scope import InnerSpawnContext, run_scope, scoped
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    list_populations,
    load_population,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_answers,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.campaign_store.store import fold_cycle_index
from promptpotter.infrastructure.store.dataset_access import install_dataset, resolve_dataset
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.layout import dataset_id_prefix, inner_sandboxes_dir
from promptpotter.infrastructure.store.measurement_archive import ArchiveFile
from promptpotter.infrastructure.store.read_model import LedgerSpan
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli import main as cli_main
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import default_identity
from promptpotter.shared.import_walk import imported_files

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_result import CycleResult
    from promptpotter.infrastructure.store.stores import Stores

DATASET = "justlogic/d234"
STAMP = "offline-run.json"
# The key a workspace went green under, written last, so a killed or rewritten one holds none.
GREEN = "green.key"
TRACE = "trace.json"
# Each leg's process files the lines it ran into its own workspace, as `ran-lines-<pid>.json`.
RAN_LINES = "ran-lines"
CONTROLLED = ("controlled", "controlled-bare")
RESUMED = "resumed"
DIVERGED = "diverged"
REBASED = "rebased"
GATED = "gated"
# Potter with its crossover on: offspring added beside L1's by a draw and by an LLM host.
CROSSED = "crossed"
CROSSED_HOSTS = ("l1_generate", "l2_context")
CROSSED_OFFSPRING = 2
# L2 fires every round, a leg's premise pinned rather than left to the fake replies.
EVERY_L2 = {"l1_patience": 0}
# The two stalls `offline_world.py::FakeBackend.SKILL` scripts for potter fire L2 at this pacing.
SCRIPTED_L2 = {"l1_patience": 2}
# Spreads δ without saturating k: at 6 most rows clamp to always/never, every arm answers them
# alike, and θ reads `prior_pinned`; 3 and 4 hold a level, 0 and 2 leave the ruler too flat.
REBASE_HARDNESS = 3
# What the rebase leg's L2 steers L1 onto (`L2Memory.steered_onto`): no other leg's L2 steers.
L2_STEER = {"temperature": 0.5}
# Each optimizer's warm straight run, the same pressed, and paused inside a walk then resumed: the
# press and the resume move the bill, never the record (`docs/developer/offline-run.md`).
WARM = "warm"
PRESSED = "pressed"
# A fixed depth, never `max`: the backend's ceiling is the machine's, so `max` presses another
# depth on another box and a trace would read the box.
PRESS = LookaheadPress(cells=4, abreast="all", auto=True)
# Potter's warm run reading its rolling race's calls as they return, pressed, paused inside a walk
# and resumed: the record hangs on arrival order, so the resume replays the order the run filed.
ARRIVED = "arrived"
# Potter's warm run pressed and paused inside a walk, then resumed: what the press bought ahead and
# no turn took stays out of the campaign's own memory across the pause.
PRESSED_RESUMED = f"{PRESSED}-{RESUMED}"
# Each optimizer's warm run with an operator's skip at this cell of its first walk past the origin,
# and its twin paused on the cell after: the resume replays the skip at the depth it was pressed.
SKIPPED = "skipped"
SKIP_AT = 3
# Each optimizer's warm run graded under `every_adoption`: a pass per selection its line adopted.
EVERY = "every-adoption"
# The first optimizer's one-round campaign whose origin is re-scored into a noise band at round 0.
REPLICATED = "replicated"
REPLICATES = 2
# Each optimizer's warm run paused as its bench pass answers this cell, then resumed.
BENCHED = "bench-paused"
BENCH_PAUSE_AT = 6
# The crossed pair's origin errs on one row in this many, so the resume replays the parent's bar
# at the grade its live election charged.
CHARGED = 8
ARRIVAL = {"pobb": {"read_order": "arrival"}}
INTERRUPTED = {1: "interrupted", 2: "interrupted-twice"}
# Potter's straight campaign with invalid answers scripted by call, each on the rung that heals it:
# a re-ask, a repair, a salvaged refusal, a repair salvaged, a repair failing into a re-ask.
L4 = "l4"
# The browser walk's L4 fixture: an outer potter campaign whose every cell runs an inner potter
# campaign on `DATASET`, in this world.
L4_DATASET = "promptpotter-self/e2e"
HEALED = "healed"
HEALS: dict[str, tuple[Heal, ...]] = {
    "L1CritiqueOutput_0": (Heal.EMPTY,),
    "L1GenerateOutput_0": (Heal.NONCOMPLIANT,),
    "L1GenerateOutput_1": (Heal.REFUSED,),
    "L1CritiqueOutput_1": (Heal.NONCOMPLIANT, Heal.REFUSED),
    "L2ContextOutput_0": (Heal.NONCOMPLIANT, Heal.NONCOMPLIANT),
}
WOUNDED = "wounded"
# Per optimizer, calls answered invalid on every send (by `requests/` name) and the mark its run
# records: L1 no candidates, an L2 fire a warning, a text node's child its parse failure.
WOUNDS: dict[str, dict[str, tuple[tuple[Heal, ...], str]]] = {
    "potter": {
        "L1GenerateOutput_0": ((Heal.NONCOMPLIANT,) * 3, PARSE_FAILURE_MALFORMED),
        # An empty answer is the provider's unless the clean re-ask reproduces it.
        "L1GenerateOutput_1": ((Heal.EMPTY, Heal.NONCOMPLIANT), PARSE_FAILURE_TOOLING),
        "L1GenerateOutput_2": ((Heal.EMPTY,) * 2, PARSE_FAILURE_MALFORMED),
        "L2ContextOutput_0": ((Heal.NONCOMPLIANT,) * 3, "layer_parse_failure"),
    },
    "capo": {
        "capo_init_164716606.0": ((Heal.NONCOMPLIANT,), PARSE_FAILURE_MALFORMED),
        "capo_crossover_547430771.0": ((Heal.NONCOMPLIANT,), PARSE_FAILURE_MALFORMED),
        "capo_crossover_1395829419.0": ((Heal.EMPTY,), PARSE_FAILURE_TOOLING),
    },
    "gepa": {
        "gepa_reflect_1270264589.0": ((Heal.EMPTY,), PARSE_FAILURE_TOOLING),
        "gepa_reflect_1276920231.0": ((Heal.NONCOMPLIANT,), PARSE_FAILURE_MALFORMED),
    },
    "levi": {
        "levi_paradigm_shift_1980294598.0": ((Heal.NONCOMPLIANT,), PARSE_FAILURE_MALFORMED),
        "levi_refine_1987354733.0": ((Heal.EMPTY,), PARSE_FAILURE_TOOLING),
    },
}


def data_root(leg: Path) -> Path:
    """A leg's outputs and the legs nested in it sit beside its run's data root, never in it: every
    process lands its root at entry, and a file no row names refuses the landing. Spelled long, as
    the run spells it, so a read under a deep home finds what the run wrote."""
    return Path(long_path(leg / "data"))


# The Ctrl+C contract is the pause and the resume, not the search: one round ends the cycle soonest.
TERMINAL_ROUNDS = 1
RESUME_APPENDS = ("phases", "run_phases", "arms", "rounds_entered", "standings")
# Which of a round's cells a launch paid and which it replayed: a cell row's `cached`, under the
# round picks that hold rows (`ROUND`).
CELL_ROWS, ROW_SPLIT = ("cells", "panel_cells"), ("cached",)
LABEL = "OFFLINE — fake optimizer LLM and fake backend; every number is synthetic"

BENCH: dict[str, Any] = {
    "sp_budget_origin": 14,
    "dataset_split": {"bench": 10, "demo": 10, "tolerance": 0},
}
ROUND_CELLS = 20
# Paper configurations scaled to a few-hundred-row bank, so a race cuts within a few rounds.
SCALED: dict[str, dict[str, Any]] = {
    "blocks": {"config": {"block_size": 5, "max_blocks": 4}},
    "paired_t": {"config": {"alpha": 0.2}},
    "survival": {"config": {"size": 4}},
    "capo_init": {"config": {"size": 4, "k_max": 2}},
    "mating": {"config": {"offspring": 2}},
    "few_shot": {"config": {"k_max": 2}},
    "minibatch": {"config": {"pareto_size": 20}},
    "proxy_css": {"config": {"discovery": 40, "size": 10}},
    "levi_paradigm_shift": {"config": {"interval": 4}},
    "levi_seed": {"config": {"n_diverse_seeds": 2}},
    "map_elites": {"config": {"centroids": 8, "cvt_samples": 400}},
}
LISTED = 15

WORDS = [
    "premise", "claim", "negation", "quantifier", "conditional", "chain", "derive", "verify",
    "contrapositive", "modus", "ponens", "tollens", "entail", "contradict", "universal", "scope",
    "track", "rewrite", "restate", "isolate", "compare", "check", "each", "step", "atoms", "map",
    "literals", "resolve", "disjunction", "conjunction", "implication", "assume", "refute",
    "confirm", "consistent", "evidence", "symbol", "table", "audit", "trace", "backward", "goal",
    "lemma", "case", "split", "exhaust", "enumerate", "count", "parity", "order", "strict",
    "commit", "label", "decide", "final", "answer", "stated", "facts", "closed", "open", "proven",
]  # fmt: skip


_ROW_MARK = re.compile(r"\[#(\d+)\]")


def _marks(text: str) -> list[int]:
    return list(dict.fromkeys(int(m) for m in _ROW_MARK.findall(text)))


_AXIS_MENU = re.compile(r"(?:EXACTLY one of|VERBATIM from): (.+)")
# A cited panel, by the heading it opens on: the framing where the leg's config renders one.
FRAMING = "TASK CONTEXT:\n"
TALLY = "ANSWER DISTRIBUTION ("


def cited_panel(config: CampaignConfig) -> str:
    return FRAMING if config.task_framing == "committed" else TALLY


def _axis_menu(prompt: str) -> list[str]:
    """Empty where an L4 arm rewrote the instruction stating it away."""
    if (menu := _AXIS_MENU.search(prompt)) is None:
        return []
    return menu.group(1).split(". ", 1)[0].removesuffix(".").split(", ")


def _variant(tag: str, rng: random.Random) -> str:
    return f"Variant {tag}: " + " ".join(rng.sample(WORDS, 18)) + "."


class SchemaFiller:
    def __init__(self, schema: dict[str, Any], *, node: str, call: str) -> None:
        self.node = node
        self.call = call
        self.defs: dict[str, Any] = {**schema.get("$defs", {}), **schema.get("definitions", {})}

    def resolve(self, s: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in s:
            ref = self.defs[s["$ref"].split("/")[-1]]
            s = {**ref, **{k: v for k, v in s.items() if k != "$ref"}}
        if "allOf" in s and len(s["allOf"]) == 1:
            s = {**self.resolve(s["allOf"][0]), **{k: v for k, v in s.items() if k != "allOf"}}
        return s

    def said(self, s: dict[str, Any]) -> dict[str, Any]:
        """A nullable schema's other arm: what a model with something to say writes."""
        s = self.resolve(s)
        return next((self.resolve(o) for o in s.get("anyOf", ()) if o.get("type") != "null"), s)

    def fill(self, s: dict[str, Any], path: tuple[str, ...]) -> Any:
        s = self.resolve(s)
        if "const" in s:
            return s["const"]
        if "enum" in s:
            return s["enum"][0]
        for key in ("anyOf", "oneOf"):
            if key in s:
                opts = [self.resolve(o) for o in s[key]]
                # As a model with nothing to say: a nullable property is left unsaid.
                if any(o.get("type") == "null" for o in opts):
                    return None
                return self.fill(opts[0], path)
        t = s.get("type")
        if isinstance(t, list):
            t = next(x for x in t if x != "null")
        if t == "object" or (t is None and "properties" in s):
            props = s.get("properties", {})
            return {n: self.fill(props.get(n, {}), (*path, n)) for n in s.get("required", [])}
        if t == "array":
            items = s.get("items", {})
            return [self.fill(items, (*path, str(i))) for i in range(s.get("minItems", 0))]
        if t == "string":
            text = f"{'.'.join(path) or 'value'} {self.node} {self.call}".ljust(
                s.get("minLength", 0), "x"
            )
            return text[: s["maxLength"]] if "maxLength" in s else text
        if t == "integer":
            return int(s.get("minimum", 0))
        if t == "number":
            return float(s.get("minimum", 0.0))
        if t == "boolean":
            return False
        return None


def _recombine(obj: dict[str, Any], props: dict[str, Any], filler: SchemaFiller, call: str) -> None:
    """Where the slot is offered, as many children as it takes, each the next main given one locus
    by the next donor apart from it."""
    if "recombinations" not in props:
        return
    slot = filler.said(props["recombinations"])
    spec = filler.resolve(slot["items"])["properties"]
    mains = filler.resolve(spec["main"])["enum"]
    ids = filler.resolve(spec["donor"])["enum"]
    loci = filler.resolve(spec["from_donor"]["items"])["enum"]
    children = []
    for i in range(slot["maxItems"]):
        main = mains[i % len(mains)]
        donors = [d for d in ids if d != main]
        children.append(
            {
                "main": main,
                "donor": donors[i % len(donors)],
                "from_donor": [loci[i % len(loci)]],
                "changes_description": f"Recombine (call {call}, child {i}).",
            }
        )
    obj["recombinations"] = children


def _asker() -> str:
    """Empty for the run's own call; an L4 cell's inner campaign by its seat's `run_seed` and the
    arm laid into its overlay, so two seats of one arm never share a turn, never by its id, which hashes the code."""
    if not run_scope().depth:
        return ""
    scope = run_scope()
    spawn, stores = scope.spawn, scope.account
    assert isinstance(spawn, InnerSpawnContext) and stores is not None, "an inner run binds both"
    config = cycle_config(stores, stores.campaigns.campaign_cycle(spawn.inside[-1])).config
    overlay = {node: dict(o.config) for node, o in sorted(config.optimization.nodes.items())}
    held = json.dumps([config.run_seed, overlay], sort_keys=True, default=str)
    return hashlib.sha256(held.encode()).hexdigest()[:8]


class ByCallIdentity:
    """The offline run's `FakeLLM` policy: an answer follows the CALL's identity, never the
    prompt's wording nor the arrival order. *rebasing*, every L2 fire steers L1 and proposes a
    fork. *crossing*,
    every variant rewrites a second field, so any two arms differ on two loci a crossover swaps."""

    def __init__(
        self,
        capture: Path,
        templates: dict[str, str],
        *,
        cites: str,
        rebasing: bool = False,
        crossing: bool = False,
    ) -> None:
        self.capture = capture
        self.templates = templates
        self.cites = cites
        self.rebasing = rebasing
        self.crossing = crossing
        self.prefixes = {node: template.split("{{")[0] for node, template in templates.items()}
        self.calls: dict[str, int] = {}
        self.turns: dict[tuple[str, str, int | None], int] = {}
        # Scripted invalid answers by call (`node_call`), one per send before the call heals.
        self.heals: dict[str, tuple[Heal, ...]] = {}
        # Scripted invalid answers by call on its every send: a structured call fails each rung.
        self.wounds: dict[str, tuple[Heal, ...]] = {}
        self.sends: Counter[str] = Counter()
        # A structured node's call a heal left open, by asker: its next send retries it.
        self.healing: dict[tuple[str, str], tuple[str, int]] = {}
        # The captured requests an L4 cell's inner campaign sent, by file name.
        self.inner: set[str] = set()

    def _text_node(self, prompt: str) -> str:
        opened = [node for node, prefix in self.prefixes.items() if prompt.startswith(prefix)]
        if not opened:
            raise KeyError(f"offline run: no llm node's template opens {prompt[:80]!r}")
        return max(opened, key=lambda node: len(self.prefixes[node]))

    def __call__(self, body: dict[str, Any]) -> Said:
        # A retry re-sends the call's own turns, then the failed answer and the ask to mend it.
        asked = itertools.takewhile(lambda m: m["role"] != "assistant", body["messages"])
        prompt = "\n".join(str(m["content"]) for m in asked)
        schema = body.get("response_format", {}).get("json_schema")
        node = schema["name"] if schema else self._text_node(prompt)
        self.calls[node] = self.calls.get(node, 0) + 1
        asker = _asker()
        if (asker, node) in self.healing:
            call, turn = self.healing.pop((asker, node))
        else:
            # Concurrent text sends of one node land in any order; their seeds tell them apart. A
            # structured node sends one at a time per asker, so its turn is the asker's count of it.
            seed: int | None = None if schema else body.get("seed")
            turn = self.turns.get((asker, node, seed), 0)
            self.turns[asker, node, seed] = turn + 1
            call = ".".join(str(p) for p in (asker, seed, turn) if p not in ("", None))
        if schema:
            filler = SchemaFiller(schema["schema"], node=node, call=call)
            obj = filler.fill(schema["schema"], ())
            content = json.dumps(
                self._shape(node, call, turn, obj, filler, schema["schema"], prompt)
            )
        else:
            content = self._text(node, call, prompt)
        key = f"{node}_{call}"
        attempt = self.sends[key]
        self.sends[key] += 1
        script = self.heals.get(key) or self.wounds.get(key, ())
        said: Said = completion(content, model=body["model"], id=f"offline-{node}-{call}")
        shown: Any = content
        if attempt < len(script):
            said = invalid(script[attempt], content, model=body["model"])
            # Its bytes hash into the key, so a price (the rate table's, not the run's) stays off it.
            shown = (
                said.json()
                if isinstance(said, httpx.Response)
                else {k: v for k, v in said.items() if k != "usage"}
            )
            # A wound's last send leaves the client no rung to retry it on.
            retried = key in self.heals or attempt + 1 < len(script)
            if schema and script[attempt] is not Heal.REFUSED and retried:
                self.healing[asker, node] = (call, turn)
        sent = f"{key}{f'~{attempt}' if attempt else ''}.json"
        if asker:
            self.inner.add(sent)
        (self.capture / sent).write_text(
            json.dumps(
                {"request": body, "response": shown}, indent=1, sort_keys=True, ensure_ascii=False
            ),
            encoding="utf-8",
            # Its bytes are the request's hash in a behaviour key: one platform's newline.
            newline="\n",
        )
        return said

    def off_script(self) -> list[str]:
        """Each scripted call not sent once per invalid answer, plus the send that healed it where
        it heals; a refusal heals on its own send."""
        healed = [
            key
            for key, script in self.heals.items()
            if self.sends[key] != len(script) + (script[-1] is not Heal.REFUSED)
        ]
        return healed + [k for k, script in self.wounds.items() if self.sends[k] != len(script)]

    def _text(self, node: str, call: str, prompt: str) -> str:
        rng = random.Random(f"{node}:{call}")
        fresh = _variant(f"{node}.{call}", rng)
        template = self.templates[node]
        if "<prompt>" in template:
            # Rephrasing the individual's own instruction keeps its parent's marker, so its skill.
            parents = MARKER.findall(prompt) if "{{instruction}}" in template else []
            return f"<prompt>{_variant(parents[-1], rng) if parents else fresh}</prompt>"
        if "```" in template:
            return f"```\n{fresh}\n```"
        if "array" in template:
            return json.dumps([_variant(f"{node}.{call}.{i}", rng) for i in range(LISTED)])
        raise KeyError(
            f"offline run: llm node {node!r}'s template asks for no form the fake writes"
        )

    def _shape(
        self,
        node: str,
        call: str,
        turn: int,
        obj: dict[str, Any],
        filler: SchemaFiller,
        schema: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        if node.startswith("L1Generate"):
            props = filler.resolve(schema)["properties"]
            item = filler.resolve(props["variants"])
            obj["variants"] = [
                self._variant_edit(turn, i, filler, filler.resolve(item["items"]), prompt)
                for i in range(item.get("minItems") or item["maxItems"])
            ]
            _recombine(obj, props, filler, call)
        elif node.startswith("L1Critique"):
            # Read off the prompt as a model would: the rows a cluster may span are the `[#id]` marks.
            # Pairs of them, then one row alone: the parse drops the lone row, and every pair where
            # the detectable move is wider (`critique_response_model`), keeping the rest.
            ids = _marks(prompt)
            clusters = [*(ids[k : k + 2] for k in range(0, min(len(ids), 4) - 1, 2)), ids[4:5]]
            obj["failure_highlights"] = [
                {
                    "sample_ids": rows,
                    "mechanism": f"negation tracking {call}.{k}",
                    "claim": f"critique {call}: the chain breaks at a negation; said and truth differ",
                }
                for k, rows in enumerate(clusters)
                if rows
            ]
            # Read off the prompt's axis list, as a model naming its axis verbatim would.
            axes = _axis_menu(prompt)
            # Listing none, a model names the axis it guesses, which the parse drops.
            guess = axes[0] if axes else "instruction"
            lead = next((a for a in axes if a.endswith(".instruction")), guess)
            obj["priority_fix"] = f"{lead}: track every negation explicitly (critique {call})"
            obj["suggested_axes"] = axes[:2]
        elif node.startswith("L2Context"):
            obj["axis_targeted"] = "instruction"
            obj["rationale"] = f"L2 fire {call}: L1 stalled on the instruction axis."
            _recombine(obj, filler.resolve(schema)["properties"], filler, call)
            obj.pop("fork_proposal", None)
            if self.rebasing:
                obj["l1_overrides"] = L2_STEER
                obj["fork_proposal"] = {
                    "reason": f"L2 fire {call}: the stall is the output schema's names.",
                    "unlock_schema_field_rename": True,
                }
        elif node.startswith("L3Plan"):
            obj["plan"] = (
                f"Replan {call}: L1 rewrites the instruction to track negation explicitly."
            )
            obj["note"] = f"l3 note {call}"
            obj["rationale"] = f"L2 stalled twice (replan {call})"
            obj.pop("fork_proposal", None)
        return obj

    def _variant_edit(
        self, generation: int, i: int, filler: SchemaFiller, item: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        """*generation* is the call's turn whatever its seed: the `SKILL` trajectory is keyed on it."""
        v: dict[str, Any] = filler.fill(item, ("variants", str(i)))
        # A citation quotes one panel's whole words, at an offset that is the call's, not the text's.
        assert self.cites in prompt, f"offline run: no {self.cites!r} panel to cite"
        start = prompt.index(self.cites) + len(self.cites) + draw("cite", generation, i) % 20
        v["evidence_grounding"] = {"citation": " ".join(prompt[start : start + 40].split()[1:-1])}
        # Every prompt node the wire offers an `instruction` on: a model sees no "first" node.
        second = ("thinking_style",) if self.crossing else ()

        def text(node: str, tag: str) -> str:
            rng = random.Random(f"l1:{generation}:{i}:{node}{tag and f':{tag}'}")
            return _variant(f"g{generation}v{i}{node}{tag}", rng)

        edits = filler.said(item["properties"]["edits"])
        at = filler.resolve(edits["items"])["properties"]["at"]["enum"]
        nodes = [locus.split(".")[0] for locus in at if locus.endswith(".instruction")]
        v["edits"] = [
            {
                "at": f"{node}.{field}",
                "op": "replace",
                "anchor": shown_text(prompt, f"{node}.{field}"),
                "text": text(node, tag),
            }
            for node in nodes
            for field, tag in (("instruction", ""), *((f, f) for f in second))
            if f"{node}.{field}" in at
        ]
        # As a model would: one cluster line of the CRITIQUE panel, else the first rows any panel marks.
        clusters = [
            ids for line in prompt.splitlines() if " rows: " in line and (ids := _marks(line))
        ]
        v["targets_rows"] = (clusters[i % len(clusters)] if clusters else _marks(prompt))[:2]
        v["mechanism"] = f"Rewrite the instruction (generation {generation}, variant {i})."
        return v


def stand_in_fetch(stores: Stores, samples: list[Sample]) -> None:
    """The synthetic rows as the cut of the definition's own `source:`: no fetch reaches the hub."""
    src = definition_source(resolve_dataset(stores, DATASET))
    assert src is not None, f"{DATASET} names a source"
    stores.tenant_datasets.record_cut(src.identity, Bank.of(samples))


def synthetic_rows(n: int) -> list[Sample]:
    """JustLogic-shaped rows: no dataset download, and nothing licensed lands in the home."""
    rows = []
    for i in range(n):
        rng = random.Random(f"row:{i}")
        premises = ". ".join(" ".join(rng.sample(WORDS, 6)) for _ in range(3))
        rows.append(
            Sample(
                id=i,
                query=(
                    f"Premises:\n{premises}.\n\nClaim: row {i} {' '.join(rng.sample(WORDS, 5))}."
                    "\n\nIs the claim TRUE, FALSE, or Uncertain given the premises?"
                ),
                ground_truth=LABELS[draw("label", i) % len(LABELS)],
            )
        )
    return rows


def campaign_config(
    optimizer: str,
    rounds: int,
    *,
    bench_trigger: BenchTrigger,
    crossover: str | None = None,
    knobs: dict[str, dict[str, Any]] | None = None,
) -> CampaignConfig:
    raw = dict(dataset_campaign_block(install_dataset(install_datasets_root(), DATASET)))
    raw.update(BENCH, bench_trigger=bench_trigger)
    opt = raw["optimization"]
    # The template's node overlay is written for the optimizer it selects, and only that one.
    templated = opt["optimizer"]
    # The clamp's seed is what each send carries; the draws follow `run_seed` (`Cycle.rng`).
    opt.update(
        budget={"usd": 1000.0, "rounds": rounds},
        origin_gate="off",
        optimizer=optimizer,
        determinism={"seed": 0},
    )
    if optimizer != templated:
        roster = select_optimizer(bench_choice(optimizer)).manifest.nodes
        opt["nodes"] = {node: block for node, block in SCALED.items() if node in roster}
    knobs = ({"escalation": SCRIPTED_L2} if optimizer == "potter" else {}) | (knobs or {})
    # The draw's host and the LLM host *crossover* names at once, drawing on one measured pool; an
    # L2 host fires every round, so each round past the first can add its children.
    if crossover:
        knobs |= {
            "mating": {"offspring": CROSSED_OFFSPRING},
            "l1_generate": {"recombine": CROSSED_OFFSPRING, "recombination_host": crossover},
            **({"escalation": EVERY_L2} if crossover == "l2_context" else {}),
        }
    nodes = opt.setdefault("nodes", {})
    for node, config in knobs.items():
        held = dict(nodes.get(node) or {})
        held["config"] = {**(held.get("config") or {}), **config}
        nodes[node] = held
    sampler = select_optimizer(CampaignConfig.model_validate(raw).optimization).sampler
    if sampler.size_knob is not None:
        nodes = opt.setdefault("nodes", {})
        block = nodes[sampler.name] = dict(nodes.get(sampler.name) or {})
        block["config"] = {**(block.get("config") or {}), sampler.size_knob: ROUND_CELLS}
    return CampaignConfig.model_validate(raw)


def stated(config: CampaignConfig) -> CampaignLayer:
    """*config* as a door states it: the layer `ConfigLayers.stating` lays over `defaults.yaml`."""
    layer: CampaignLayer = CampaignLayer.model_validate(
        config.model_dump(mode="json", exclude_unset=True)
    )
    return layer


def text_templates(config: CampaignConfig) -> dict[str, str]:
    selected = select_optimizer(config.optimization)
    return {
        node: running_prompt(node, selected.node_config(node), selected.manifest).render()
        for node in selected.llm_nodes
    }


@contextlib.contextmanager
def edited_proposer(config: CampaignConfig) -> Iterator[None]:
    """The proposer's template edited in the install's shadow of its manifest, as an operator's
    edit lands (`config/paths.py::optimizer_manifest_path`); appended, so the fake still knows the
    node by its template's opening."""
    selected = select_optimizer(config.optimization)
    node = selected.proposer
    node_config = selected.node_config(node)
    key = prompt_key(node_config)
    held = running_prompt(node, node_config, selected.manifest)
    for directory in manifest_directories(selected.name):
        authored = yaml.safe_load(optimizer_manifest_path(directory).read_text(encoding="utf-8"))
        if key in (authored.get("resolved_prompts") or {}):
            break
    else:
        raise SystemExit(f"offline run: no manifest {selected.name!r} reads holds {key!r}")
    authored["resolved_prompts"][key]["problem_description"] = (
        f"{held.problem_description}\n\nEdited on resume."
    )
    shadow = user_data_root() / "optimizers" / directory.name / "pipeline.yaml"
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shadow.write_text(yaml.safe_dump(authored, sort_keys=False, allow_unicode=True), "utf-8")
    try:
        if optimizer_manifest_path(directory) != shadow:
            raise SystemExit(f"offline run: the install's manifest shadow {shadow} is not read")
        yield
    finally:
        shadow.unlink()


def closed_a_round(stores: Stores, hop: CycleHop) -> bool:
    ledger = stores.campaigns.layout(hop).ledger
    return any(r.get("record_type") == "round_closed" for r in _jsonl(ledger))


def forked_at_init(stores: Stores, parent: CycleHop, fork: CycleHop) -> None:
    if fork == parent:
        raise SystemExit("offline run: a diverged resume ran on in the cycle it diverged from")
    layout = stores.campaigns.layout(parent)
    rebased = [
        r
        for r in _jsonl(layout.ledger)
        if r.get("record_type") == "run_phase"
        and r["state"].get("stop_reason") == StopReason.REBASED
    ]
    if [r["spend"] is not None for r in rebased] != [True]:
        raise SystemExit(f"offline run: the parent ended on {rebased}, not one REBASED with spend")
    if producer_lock.cycle_held(layout.cycle_dir):
        raise SystemExit("offline run: the fork left its parent's producer lock held")
    last = layout.readout.read_text(encoding="utf-8").rstrip().splitlines()[-1]
    if fork.cycle_id not in last:
        raise SystemExit(f"offline run: the parent's readout ends {last!r}, naming no fork")
    if not closed_a_round(stores, fork):
        raise SystemExit("offline run: the fork closed no round of its own")
    print(f"diverged at run init: {parent.cycle_id} REBASED onto {fork.cycle_id}, which ran on")


async def forked_twice(stores: Stores, fork: CycleHop, rounds: int) -> None:
    """An operator fork off the divergence fork, then one off that: one campaign, four cycles."""
    # Each cut leaves the fork a round of its own to close under the campaign's rounds cap.
    for from_round in (rounds - 2, rounds - 1):
        asked = ForkCyclePayload(
            campaign_id=fork.campaign_id, cycle_id=fork.cycle_id, round=from_round, keep_rounds=True
        )
        cycle_id = cut_operator_fork(stores, asked, CycleStart()).take(stores)
        fork = CycleHop(campaign_id=fork.campaign_id, cycle_id=cycle_id)
        session, result = await run_campaign(
            fork,
            stores=stores,
            readout_sink=print,
            budget=Budget(),
            mode=RunMode(),
        )
        ended = stop_reason_outcome(result.stop_reason).exit_code or session.hop != fork
        if ended or not closed_a_round(stores, fork):
            raise SystemExit(
                f"offline run: the fork from round {from_round} ended {result.stop_reason}"
            )
    print(f"forked twice: {fork.cycle_id} is a fork of a fork")


@dataclass(frozen=True)
class Harness:
    """Every network edge one act's campaigns reach, answered in-process."""

    llm: ByCallIdentity
    backend: FakeBackend
    router: Router

    @classmethod
    def installed(
        cls,
        out: Path,
        config: CampaignConfig,
        templates: dict[str, str],
        samples: list[Sample],
        *,
        crossing: bool,
        hardness: int = 0,
    ) -> Harness:
        (out / "requests").mkdir(parents=True)
        llm = ByCallIdentity(
            out / "requests", templates, cites=cited_panel(config), crossing=crossing
        )
        backend = FakeBackend(samples, hardness=hardness)
        router = Router(FakeLLM(llm), backend)
        install_network(router)
        return cls(llm, backend, router)


async def run_one(
    optimizer: str,
    workspace: Path,
    *,
    rounds: int,
    rows: int,
    out: Path | None = None,
    framing: bool = True,
    pause_after: int | None = None,
    pause_in_walk: int | None = None,
    bench_trigger: BenchTrigger = "at_end",
    diverge: bool = False,
    crossover: str | None = None,
    heal: bool = False,
    wound: bool = False,
    hardness: int = 0,
    press: bool = False,
    arrival: bool = False,
    charged: bool = False,
    skip_at: int | None = None,
    skip_resumed: bool = False,
    origin_replicates: int | None = None,
    pause_in_bench: bool = False,
) -> tuple[Path, str]:
    """The archive is the workspace's, never *out*'s: campaigns in one process share it.
    *pause_after* pauses at that round's boundary; *pause_in_walk* at the first cell that round
    measures, its walk banked, so the resume replays the walk and runs no proposer; *press* presses
    `PRESS` as the first cell is answered; *arrival* lays `ARRIVAL`; *charged* answers one in `CHARGED`
    of the origin's rows as the pipeline's error, a miss its bar is charged; *skip_at* skips the arm
    walking at that cell of the first walk past the origin (`skipping`), *skip_resumed* pausing on
    the cell after and resuming; *origin_replicates* re-scores the origin's round-0 rows that many
    times into its noise band; *pause_in_bench* pauses inside the bench pass (`pausing_in_bench`)
    and resumes."""
    out = workspace if out is None else out
    config = campaign_config(
        optimizer,
        rounds,
        bench_trigger=bench_trigger,
        crossover=crossover,
        knobs=ARRIVAL if arrival else None,
    )
    if not framing:
        config = config.model_copy(update={"task_framing": "off"})
    if origin_replicates is not None:
        config = config.model_copy(update={"origin_replicates": origin_replicates})
    samples = synthetic_rows(rows)
    harness = Harness.installed(
        out,
        config,
        text_templates(config),
        samples,
        crossing=crossover is not None,
        hardness=hardness,
    )
    if heal:
        harness.llm.heals = HEALS
    harness.backend.origin_errors = CHARGED if charged else 0
    if wound:
        harness.llm.wounds = {key: sent for key, (sent, _) in WOUNDS[optimizer].items()}

    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    acts = [
        *([pressing(stores)] if press else []),
        *([pausing_in_walk(stores, pause_in_walk)] if pause_in_walk is not None else []),
        *([skipping(stores, skip_at, then_pause=skip_resumed)] if skip_at is not None else []),
        *([pausing_in_bench(stores, rounds)] if pause_in_bench else []),
    ]

    async def acting() -> None:
        for act in acts:
            await act()

    harness.backend.before_answer = acting if acts else None
    session, result = await run_campaign(
        DATASET,
        stated(config),
        stores=stores,
        readout_sink=print,
        budget=Budget(),
        step_rounds=pause_after,
        mode=RunMode(),
    )
    harness.backend.before_answer = None
    if pause_after is not None or pause_in_walk is not None or skip_resumed or pause_in_bench:
        if result.stop_reason != StopReason.PAUSED:
            raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}, never paused")
        # The resume reads its rounds off the ledger and the archive: their checkouts are gone.
        shutil.rmtree(stores.campaigns.layout(session.hop).rounds)
        paused = session.hop
        with edited_proposer(config) if diverge else contextlib.nullcontext():
            session, result = await run_campaign(
                paused,
                stores=stores,
                readout_sink=print,
                budget=Budget(),
                mode=RunMode(on_divergence=OnDivergence.FORK if diverge else OnDivergence.HALT),
            )
            if diverge:
                forked_at_init(stores, paused, session.hop)
                await forked_twice(stores, session.hop, rounds)
    cycle = await banked(
        stores, session, result, harness, out, name=optimizer, bench_trigger=bench_trigger
    )
    if strayed := harness.llm.off_script():
        raise SystemExit(f"offline run: the scripted calls {strayed} were not sent as scripted")
    return cycle, session.campaign_id


def _run_hop(stores: Stores) -> CycleHop:
    (campaign,) = stores.campaigns.list_campaigns(lifecycle="all")
    return campaign.root_hop


async def _pressed(stores: Stores, payload: CommandPayload) -> None:
    """*payload* pressed at the terminal, as an operator presses it."""
    await (await host_dispatcher(stores)).dispatch(CommandCall(payload, door=Door.TERMINAL))


def _walk_banked(stores: Stores, at: int | None, from_round: int) -> bool:
    """A cell answered in a round from *from_round* on whose walk banked: a cell measured inside a
    walk, or replayed, is none."""
    if at is None or at < from_round:
        return False
    return (
        stores.campaigns.standing_rounds(_run_hop(stores), moment=None).proposals.get(at)
        is not None
    )


def _paused(hop: CycleHop, reason: str) -> PauseCyclePayload:
    return PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason=reason)


def pausing_in_walk(stores: Stores, round_num: int) -> Callable[[], Awaitable[None]]:
    """The pause an operator presses as the first cell is answered of a round from *round_num* on
    whose walk banked."""
    asked = False

    async def pause() -> None:
        nonlocal asked
        if asked or not _walk_banked(stores, current_round(), round_num):
            return
        asked = True
        await _pressed(stores, _paused(_run_hop(stores), "offline: inside a walk"))

    return pause


def pausing_in_bench(stores: Stores, rounds: int) -> Callable[[], Awaitable[None]]:
    """The pause an operator presses as the end's bench pass, every one of *rounds* closed,
    answers its `BENCH_PAUSE_AT`-th cell."""
    answered = 0

    async def pause() -> None:
        nonlocal answered
        hop = _run_hop(stores)
        if max(stores.campaigns.standing_rounds(hop, moment=None).rounds, default=0) < rounds:
            return
        answered += 1
        if answered == BENCH_PAUSE_AT:
            await _pressed(stores, _paused(hop, "offline: inside a bench pass"))

    return pause


def skipping(stores: Stores, skip_at: int, *, then_pause: bool) -> Callable[[], Awaitable[None]]:
    """The skip an operator presses as the *skip_at*-th cell is answered of the first walk past the
    origin that banked, cutting the arm it walks; *then_pause*, a pause on the cell after, inside
    that walk, so the resume replays the skipped arm."""
    answered = 0

    async def skip() -> None:
        nonlocal answered
        if not _walk_banked(stores, current_round(), 1):
            return
        answered += 1
        hop = _run_hop(stores)
        if answered == skip_at:
            await _pressed(
                stores, SkipSearchpointPayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id)
            )
            if then_pause:
                await _pressed(stores, _paused(hop, "offline: past a skip"))

    return skip


def pressing(stores: Stores) -> Callable[[], Awaitable[None]]:
    """`PRESS`, pressed by an operator as the run's first cell is answered, standing every round."""
    pressed = False

    async def press() -> None:
        nonlocal pressed
        if pressed:
            return
        pressed = True
        hop = _run_hop(stores)
        await _pressed(
            stores,
            SetSampleLookaheadPayload(
                campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, **PRESS.model_dump()
            ),
        )

    return press


def outside_the_fence(capture: Path, bank: Iterable[str]) -> list[str]:
    """The captured requests reading a bank row outside the dataset fence: a row is data, never an
    instruction, wherever an optimizer quotes it (a row, a wound, a critique's evidence)."""
    stems = [" ".join(query.split())[:40] for query in bank]
    unfenced = []
    for sent in sorted(capture.iterdir()):
        messages = json.loads(sent.read_text(encoding="utf-8"))["request"]["messages"]
        text = " ".join(
            " ".join(m["content"].split()) for m in messages if isinstance(m.get("content"), str)
        )
        head, *fenced = text.split(FENCE_OPEN_PREFIX)
        outside = head + "".join(part.partition(FENCE_CLOSE)[2] for part in fenced)
        if any(stem in outside for stem in stems):
            unfenced.append(sent.name)
    return unfenced


async def banked(
    stores: Stores,
    session: Session,
    result: CycleResult,
    harness: Harness,
    out: Path,
    *,
    name: str,
    bench_trigger: BenchTrigger,
) -> Path:
    """Checks one campaign's run, then writes its `BehaviourTrace` to *out*."""
    if stop_reason_outcome(result.stop_reason).exit_code:
        raise SystemExit(f"offline run: {name} ended {result.stop_reason}")
    beside = sorted(
        f"{node.name}.{key}"
        for node in session.bound_resolution.chain.declared_nodes
        for key in node.param_keys
        if key not in node.prompt_fields
    )
    arm = isinstance(session.purpose, Compare)
    if arm and beside:
        raise SystemExit(f"offline run: arm {name} searched {beside} beside its prompt")
    stores.campaigns.update_campaign(session.campaign_id, label=LABEL)
    cycle = Path(stores.campaigns.cycle_dir(session.hop))
    dashboard = json.loads((cycle / "dashboard.json").read_text(encoding="utf-8"))
    spend = SpendRollup.model_validate(dashboard["spend"])
    if billed := spend.total_used_usd:
        raise SystemExit(f"offline run billed ${billed}: a fake answered with a cost")
    backend = harness.backend
    bench, searched_cells = result.bench, backend.cells
    # An arm is minted `at_end` whatever its config says, so only an ordinary campaign waits.
    if bench_trigger == "manual" and not arm:
        sent = spend.by_kind["bench"]
        if sent.incurred_usd or sent.input_tokens or sent.output_tokens:
            raise SystemExit(f"offline run: a manual launch of {name} sent bench cells")
        if bench.status.state != "not_asked" or not bench.status.can_run:
            raise SystemExit(f"offline run: a manual launch of {name} serves {bench}")
        call = CommandCall(HeldOutPayload(campaign_id=session.hop.campaign_id), door=Door.TERMINAL)
        bench = (await (await host_dispatcher(stores)).answer(held_out, call)).result
    decisions = extract(cycle, bench)
    # An L4 outer's inner campaigns, as a set: cells measured at once land in any order.
    nested = inner_sandboxes_dir(default_projects_root()).glob("*/*/campaigns/*/cycles/*")
    if inner := sorted(json.dumps(extract(c, None), sort_keys=True) for c in nested):
        decisions["inner"] = [json.loads(one) for one in inner]
    decisions["harness"] = {
        "stop_reason": str(result.stop_reason),
        "unrouted": sorted(harness.router.unrouted),
        "llm_calls": dict(sorted(harness.llm.calls.items())),
        "backend_cells": searched_cells,
        "bench_cells_asked_for": backend.cells - searched_cells,
    }
    captured = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(harness.llm.capture.iterdir())
    }
    if unfenced := outside_the_fence(harness.llm.capture, harness.backend.truth):
        raise SystemExit(f"offline run: {name} sent a bank row outside the fence in {unfenced}")
    asked = harness.llm.inner
    BehaviourTrace(
        world=WORLD,
        decisions=decisions,
        requests={n: h for n, h in captured.items() if n not in asked},
        inner_requests={n: h for n, h in captured.items() if n in asked},
    ).write(out)
    return cycle


def _moved(now: Any, then: Any, path: str, depth: int) -> list[str]:
    """The paths, *depth* levels deep, where two traces' parts differ; a part one side lacks moved."""
    if now == then:
        return []
    if depth and isinstance(now, dict) and isinstance(then, dict):
        return [
            moved
            for key in sorted(now.keys() | then.keys())
            for moved in _moved(now.get(key), then.get(key), f"{path}.{key}", depth - 1)
        ]
    if depth and isinstance(now, list) and isinstance(then, list) and len(now) == len(then):
        return [
            moved
            for i, (a, b) in enumerate(zip(now, then, strict=True))
            for moved in _moved(a, b, f"{path}[{i}]", depth - 1)
        ]
    return [path]


@dataclass(frozen=True)
class BehaviourTrace:
    """What one optimizer's run decided, canonical (`extract`), the race standings among them, and
    the hash of each optimizer request it sent, in the world `offline_world.py::WORLD` names; an
    L4 outer's inner campaigns' requests apart from its own, as their decisions are."""

    world: str
    decisions: dict[str, Any]
    requests: dict[str, str]
    inner_requests: dict[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def read(cls, workspace: Path) -> BehaviourTrace:
        return cls(**json.loads((workspace / TRACE).read_text(encoding="utf-8")))

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        dumped = json.dumps(dataclasses.asdict(self), indent=1, sort_keys=True, ensure_ascii=False)
        (out / TRACE).write_text(dumped + "\n", encoding="utf-8")

    def moved(self, earlier: BehaviourTrace) -> list[str]:
        def sent(name: str, now: dict[str, str], then: dict[str, str]) -> list[str]:
            moved = [n for n in now.keys() | then.keys() if now.get(n) != then.get(n)]
            return [f"{name} ({len(moved)} of {len(now)})"] if moved else []

        return [
            *(["world"] * (self.world != earlier.world)),
            *_moved(self.decisions, earlier.decisions, "decisions", 2),
            *sent("requests", self.requests, earlier.requests),
            *sent("inner_requests", self.inner_requests, earlier.inner_requests),
        ]

    def inner_half(self) -> list[Any]:
        """What an L4 cell's number reads: its inner campaigns alone, never the outer that asked."""
        return [self.decisions["inner"], self.inner_requests]


async def run_rebasing(workspace: Path, *, rounds: int, rows: int) -> None:
    """Potter resumed with `--from` under an L2 proposing the same fork at every fire: only the
    first changes something, and the line's forks spend one round budget between them."""
    # L2 fires every round, so the fork owes nothing to whether the fake replies happen to stall;
    # no arm stops short, so the ruler a rewind needs spans the rows whatever PoBB would cut.
    config = campaign_config(
        "potter",
        rounds,
        bench_trigger="manual",
        knobs={"escalation": EVERY_L2, "pobb": {"epsilon_stop": False}},
    )
    samples = synthetic_rows(rows)
    (workspace / "requests").mkdir()
    llm = ByCallIdentity(workspace / "requests", text_templates(config), cites=cited_panel(config))
    # A rewind lands on a round holding a θ level, which only a ruler spanning the rows gives.
    install_network(Router(FakeLLM(llm), FakeBackend(samples, hardness=REBASE_HARDNESS)))
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    # The first campaign banks the rows the second's ruler is fit on; the second pauses at 1.
    banked: list[str] = []
    for step_rounds in (None, 1):
        session, _ = await run_campaign(
            DATASET,
            stated(config),
            stores=stores,
            readout_sink=print,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(),
        )
        banked.append(session.campaign_id)
    llm.rebasing = True
    session, _ = await run_campaign(
        session.hop,
        stores=stores,
        readout_sink=print,
        budget=Budget(),
        mode=RunMode(from_round=1),
    )
    cycles = dict(stores.campaigns.campaign_cycles(session.campaign_id))
    forks = [index for index in cycles.values() if index.parent_cycle_id is not None]
    if not forks:
        raise SystemExit("offline run: a fork that changes the config minted no fork")
    ended = [index.stop_reason for index in forks]
    if any(r is None or stop_reason_outcome(r).exit_code for r in ended):
        raise SystemExit(f"offline run: a rebase's fork re-applied the operator's rewind: {ended}")
    spent = sum(
        1
        for hop, index in cycles.items()
        for rnd in stores.campaigns.standing_rounds(hop, moment=None).rounds
        if rnd >= (1 if index.cut_round is None else index.cut_round)
    )
    if spent > rounds:
        raise SystemExit(
            f"offline run: the line's forks ran {spent} rounds on a budget of {rounds}"
        )
    final = cycles[session.hop].stop_reason
    if final in (None, StopReason.REBASED):
        raise SystemExit(f"offline run: the line still reads {final}")
    sent = (json.loads(p.read_text())["request"] for p in (workspace / "requests").glob("L1Gen*"))
    if not any(L2_STEER.items() <= request.items() for request in sent):
        raise SystemExit(f"offline run: L2's steer {L2_STEER} reached no L1 send")
    print(f"rebased {len(forks)} times, {spent} of {rounds} rounds spent, line ended {final}")
    await noise_walk_doors(stores, session.hop, list(cycles))
    spread_reads_the_selection(
        stores, [hop for c in banked for hop, _ in stores.campaigns.campaign_cycles(c)]
    )


def spread_reads_the_selection(stores: Stores, hops: Iterable[CycleHop]) -> None:
    """At each election past a pick, a sample only C0 measured reads no miss of the selection
    the line stands on: the spread reads the headline's cells, never every candidate's."""
    only_c0 = 0
    for hop in hops:
        lines = list(_jsonl(stores.campaigns.layout(hop).ledger))
        elections = [at for at, r in enumerate(lines, 1) if r.get("record_type") == "election"]
        for at in elections:
            past = lines[:at]
            if not any(
                r.get("record_type") == "election" and r["ending"] == RoundEnding.CROWNED
                for r in past
            ):
                continue
            scope = cells_scope(
                stores,
                DATASET,
                scope="cycle",
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                descend=(),
                at=at,
            )
            read = measurement_log(
                scope,
                limit=10_000,
                max_unmeasured=None,
                order=None,
                candidate_id=None,
                round=None,
                status=None,
            )
            label = {c.key: c.label for c in read.candidates}
            seen: dict[int, set[str]] = {}
            for cell in read.cells:
                if cell.fitness is not None:
                    seen.setdefault(cell.position, set()).add(label[cell.candidate])
            c0_only = [s for s in read.samples if seen.get(s.position) == {"C0"}]
            if missed := [s.position for s in c0_only if s.hit_spread == "never"]:
                raise SystemExit(
                    f"offline run: {hop.cycle_id} at {at} reads {missed}, which only C0 measured, "
                    "as missed by the selection"
                )
            only_c0 += len(c0_only)
    if not only_c0:
        raise SystemExit("offline run: no read past a pick held a sample only C0 measured")
    print(f"the spread read {only_c0} samples only C0 measured as none of the selection's")


async def noise_walk_doors(stores: Stores, hop: CycleHop, line: list[CycleHop]) -> None:
    """The line's warm ruler separates arms, so each door reads the noise walk and the hold off
    one served round: the readouts of *line* name μ0 and τ, *hop*'s stop every hold the last
    close's series reads (a close's round block reads its own), the embedded and MCP reads of
    *hop* serve both."""
    embedded = await answer_read(
        CycleDashboardParams(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id), stores
    )
    assert isinstance(embedded, ServedDashboard), "the dashboard read serves the dashboard"
    asked = {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id}
    mcp = await HostSession(Caller(stores)).answer(CallTool(ReadKind.CYCLE_DASHBOARD, asked))
    readout = "".join(stores.campaigns.layout(at).readout.read_text("utf-8") for at in line)
    walk = {"lift_prior", "hold_up"}
    served = [r.model_dump(mode="json", include=walk) for r in embedded.rounds]
    hosted = json.loads(json.dumps(mcp.value))["rounds"]
    if served != [{k: r[k] for k in walk} for r in hosted]:
        raise SystemExit("offline run: the MCP and embedded doors read two noise walks")
    held = [r.hold_up for r in embedded.rounds if r.hold_up is not None]
    said = {
        "lift_prior": any(r["lift_prior"] for r in served),
        "hold_up": bool(held),
        "μ0": "noise walk: μ0 " in readout,
        "τ": ", τ " in readout,
        "held up": all(f"held up: the promotion holds {h.text} " in readout for h in held),
    }
    if missing := [name for name, read in said.items() if not read]:
        raise SystemExit(f"offline run: the separating round's doors read no {missing}")
    print(f"noise walk read at every door: {len(held)} promotions held up")


async def run_gated(optimizer: str, workspace: Path, *, rows: int) -> None:
    """An origin the backend refused whole halts at the gate. Resumed still refused, the resume is
    the operator's override, banked once, each cell its origin walk sends sent once; resumed once it answers,
    the origin passes on health, each lost cell re-sent once, on no fork. Broken first: the archive
    then holds no answer to replay."""
    refused = 0
    config = campaign_config(optimizer, 1, bench_trigger="at_end")
    gated = config.optimization.model_copy(update={"origin_gate": "strict"})
    config = config.model_copy(update={"optimization": gated})
    samples = synthetic_rows(rows)
    (workspace / "requests").mkdir()
    llm = ByCallIdentity(workspace / "requests", text_templates(config), cites=cited_panel(config))
    backend = FakeBackend(samples)
    install_network(Router(FakeLLM(llm), backend))
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)

    def passes(hop: CycleHop) -> list[str]:
        decided = stores.campaigns.standing_rounds(hop, moment=None).decisions.get(0, [])
        return [d.inputs.by for d in decided if isinstance(d.inputs, OriginGatePass)]

    for fixed in (False, True):
        backend.down, backend.sent = True, []
        session, result = await run_campaign(
            DATASET,
            stated(config),
            stores=stores,
            readout_sink=print,
            budget=Budget(),
            mode=RunMode(),
        )
        hop, lost = session.hop, {(prompt, query) for prompt, query, _ in backend.sent}
        if result.stop_reason != StopReason.ORIGIN_GATE or passes(hop):
            raise SystemExit(f"offline run: a refused origin ended {result.stop_reason}")
        backend.down, backend.sent = not fixed, []
        session, result = await run_campaign(
            hop,
            stores=stores,
            readout_sink=print,
            budget=Budget(),
            mode=RunMode(),
        )
        said = f"resumed {'answering' if fixed else 'still refused'}"
        if result.stop_reason == StopReason.ORIGIN_GATE:
            raise SystemExit(f"offline run: {said}, the origin halted at the gate again")
        if passes(hop) != ([] if fixed else ["resume"]):
            raise SystemExit(f"offline run: {said}, round 0 banked passes {passes(hop)}")
        if not fixed:
            # Round 0's walk, before the loop opens round 1: one send of a cell is its attempts,
            # and a second send of it would exceed them.
            attempts = Counter((p, q) for p, q, at in backend.sent if at in (None, 0))
            if twice := sorted(q for (_, q), n in attempts.items() if n > SEND_ATTEMPTS):
                raise SystemExit(f"offline run: {said}, round 0 sent cells {twice} twice")
            refused = len(attempts)
            continue
        # The origin's every send: its lost cells, then those its walk never sent, each once.
        origin = {prompt for prompt, _ in lost}
        resent = [(p, q) for p, q, _ in backend.sent if p in origin]
        if len(resent) != len(set(resent)) or not lost <= set(resent):
            raise SystemExit(
                f"offline run: {said}, {len(resent)} origin sends re-measured {len(lost)} lost cells"
            )
        cycles = stores.campaigns.campaign_cycles(hop.campaign_id)
        if session.hop != hop or len(dict(cycles)) != 1:
            raise SystemExit(f"offline run: {said}, the origin was re-measured onto a fork")
        if stop_reason_outcome(result.stop_reason).exit_code:
            raise SystemExit(f"offline run: {said}, the line ended {result.stop_reason}")
        print(
            f"gated halts twice; the override banked once, its origin walk sending {refused} "
            f"cells once each; the fixed origin sent its {len(lost)} lost cells once "
            f"({len(resent)} origin sends) and ran on to {result.stop_reason}"
        )


async def run_l4(workspace: Path, *, rows: int) -> None:
    """The fixture's knobs, its spend cap lifted and its draws seeded: the inner runs execute the
    optimizer code a change touches, and the outer trace reads what they measured."""
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    samples = synthetic_rows(rows)
    stand_in_fetch(stores, samples)
    raw = dict(dataset_campaign_block(resolve_dataset(stores, L4_DATASET)))
    opt = raw["optimization"]
    opt.update(budget={**opt["budget"], "usd": 1000.0}, determinism={"seed": 0})
    config = CampaignConfig.model_validate(raw)
    harness = Harness.installed(workspace, config, text_templates(config), samples, crossing=False)
    session, result = await run_campaign(
        L4_DATASET,
        stated(config),
        stores=stores,
        readout_sink=print,
        budget=Budget(),
        mode=RunMode(),
    )
    await banked(
        stores, session, result, harness, workspace, name=L4, bench_trigger=config.bench_trigger
    )
    askers = {asker for asker, _, _ in harness.llm.turns if asker}
    print(
        f"{L4} outer {result.stop_reason}: {len(askers)} inner campaigns asked, "
        f"{harness.backend.cells} inner cells measured"
    )


def run_terminal(
    optimizer: str, workspace: Path, verb: str, *, rounds: int, rows: int, interrupts: int
) -> None:
    config = campaign_config(optimizer, rounds, bench_trigger="at_end")
    samples = synthetic_rows(rows)
    requests = workspace / f"requests-{verb}"
    requests.mkdir()
    llm = FakeLLM(ByCallIdentity(requests, text_templates(config), cites=cited_panel(config)))
    install_network(Router(llm, FakeBackend(samples, interrupts=interrupts)))
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    declared = workspace / "campaign.yaml"
    declared.write_text(
        yaml.safe_dump({"campaign": config.model_dump(mode="json", exclude_unset=True)}),
        encoding="utf-8",
    )
    new = ["new", DATASET, f"--config={declared}"]
    sys.argv = ["promptpotter", *(new if verb == "new" else [verb])]
    # A child of a pool thread may inherit SIGINT ignored; a terminal's process never does.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        cli_main.main()
    finally:
        (workspace / f"{verb}.json").write_text(json.dumps(left(stores)), encoding="utf-8")


def left(stores: Stores) -> dict[str, Any]:
    """What a terminal leg left, read by its own process: a store builds only in its own home."""
    (campaign_id,) = stores.campaigns.list_campaign_ids()
    (cycle,) = stores.campaigns.campaign_cycle_dirs(campaign_id)
    hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle.name)
    state = stores.run_state(hop)
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in stores.jobs_dir.glob("*.json")]
    (mint,) = _commands(stores, WORKSPACE_SUBJECT)[CommandKind.MINT_CAMPAIGN]
    assert isinstance(mint, MintCampaignPayload)
    started = _commands(stores, subject_key(stores.subject(hop))).get(CommandKind.START_RUN, [])
    return {
        "state": str(state),
        "phase": state.run_phase,
        "held": state.producer.attached or any(j["released_at"] is None for j in jobs),
        "minted_config": mint.campaign_config is not None,
        "started": len(started),
    }


def _commands(stores: Stores, subject: str) -> dict[CommandKind, list[CommandPayload]]:
    """The payloads of what *subject* was commanded and applied, by kind, off the account's log."""
    book = stores.commands.book()
    out: dict[CommandKind, list[CommandPayload]] = {}
    for r in book.subject_events(subject):
        if isinstance(r, CommandRecord) and book.status(r.command_id) == "applied":
            out.setdefault(r.kind, []).append(r.payload)
    return out


def interrupted(
    spawn_terminal: Callable[[Path, str, int], int], workspace: Path, interrupts: int
) -> tuple[bool, str]:
    workspace.mkdir()
    if (rc := spawn_terminal(workspace, "new", interrupts)) != StopOutcome.PAUSED.exit_code:
        return False, f"`new` under {interrupts} SIGINT exited {rc}, not as a pause does"
    stopped = json.loads((workspace / "new.json").read_text(encoding="utf-8"))
    if stopped["held"]:
        return False, f"`new` exited 130 still holding its cycle or its slot ({stopped['state']})"
    if interrupts == 1 and stopped["phase"] != RunPhase.PAUSED:
        return False, f"one SIGINT left the cycle {stopped['phase']}, not paused"
    if not stopped["minted_config"]:
        return False, "the mint-campaign record carries no config"
    if rc := spawn_terminal(workspace, "resume", 0):
        return False, f"`resume` after {interrupts} SIGINT exited {rc}"
    ended = json.loads((workspace / "resume.json").read_text(encoding="utf-8"))
    if ended["phase"] != RunPhase.TERMINAL or ended["started"] != 1:
        return False, f"`resume` left the cycle {ended['phase']} on {ended['started']} start-run"
    return True, f"exit 130 {stopped['phase']}, slot released; `resume` ran it to its end"


async def run_arms(
    workspace: Path, names: list[str], *, rounds: int, rows: int
) -> dict[str, tuple[Path, str]]:
    """Every arm minted in one act on one shared declaration, as `head-to-head` mints them, each
    laying its own optimizer and node knobs over it; then run one after another."""
    configs = {name: campaign_config(name, rounds, bench_trigger="at_end") for name in names}
    samples = synthetic_rows(rows)
    templates = {
        node: t for config in configs.values() for node, t in text_templates(config).items()
    }
    harness = Harness.installed(
        workspace / "arms", configs[names[0]], templates, samples, crossing=False
    )
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    sent = {name: c.model_dump(mode="json", exclude_unset=True) for name, c in configs.items()}
    request = HeadToHeadRequest(
        head_to_head_id="h2h",
        arms=[
            ArmRequest(
                arm_key=name,
                optimization=OptimizationOverrides.model_validate(
                    {"optimizer": name, "nodes": sent[name]["optimization"].get("nodes", {})}
                ),
            )
            for name in names
        ],
    )
    # What every arm shares: the first's declaration less what each arm lays over it.
    shared = sent[names[0]]
    for knob in ("optimizer", "nodes"):
        shared["optimization"].pop(knob, None)
    ran = await run_head_to_head(
        DATASET,
        CampaignLayer.model_validate(shared),
        request,
        stores=stores,
        readout_sink=print,
    )
    arms = {}
    for name, armed, result in ran:
        out = workspace / f"arm-{name}"
        cycle = await banked(stores, armed, result, harness, out, name=name, bench_trigger="at_end")
        arms[name] = (cycle, armed.campaign_id)
    return arms


async def run_controlled(
    workspace: Path, arm_names: list[str], *, rounds: int, rows: int, foreign: bool
) -> None:
    subjects = []
    if foreign:
        _, cid = await run_one(
            "potter", workspace, rounds=rounds, rows=rows, out=workspace / "foreign", framing=False
        )
        subjects.append(cid)
    arms = await run_arms(workspace, arm_names, rounds=rounds, rows=rows)
    subjects += [cid for _, cid in arms.values()]
    stores = build_stores(default_identity(), projects_root=default_projects_root())

    def walks(campaign_id: str) -> CampaignWalks:
        return CampaignWalks(stores.campaigns, campaign_id)

    def filed(campaign_id: str) -> frozenset[str]:
        return scan_ledger_answers(walks(campaign_id).spans())

    bank = stores.tenant_datasets.labelled(DATASET)
    assert bank is not None, "every arm ran on the labelled bank"

    def memory(campaign_id: str) -> set[str]:
        with scoped(memory=ArmOf(walks(campaign_id), ruler_version=0)):
            return {
                answer.answer
                for entry in list_populations(stores, bank)
                for answer in load_population(stores, entry, bank)
            }

    foreign_only = set().union(*(filed(c) for c in subjects)) - set().union(
        *(filed(c) for _, c in arms.values())
    )
    for name, (_, cid) in arms.items():
        seen = memory(cid)
        walked = filed(cid)
        if seen & foreign_only or not seen <= walked:
            raise SystemExit(f"offline run: arm {name}'s memory reads an answer it did not walk")
        # The filings, never the distinct cells memory stands: a cell coalesced with a twin in flight
        # files nothing, so how many twins coalesce is a race, and a count of them moves run to run.
        print(
            f"arm {name}: memory holds only its own {len(walked)} filed answers, "
            f"none of {len(foreign_only)} foreign"
        )
    record = stores.campaigns.load_head_to_head("h2h")
    assert record is not None, "the arms joined no head-to-head"
    for name, (cycle, cid) in arms.items():
        hop = CycleHop(campaign_id=cid, cycle_id=cycle.name)
        standing = stores.campaigns.standing_rounds(hop, moment=None).rounds.values()
        own = RulerOwn(campaign=cid, since=record.ruler_version)
        read = {None if (s := r.close.ruler) is None else s.own for r in standing}
        if read != {own}:
            raise SystemExit(
                f"offline run: arm {name} read ruler reaches {read}, its head-to-head froze "
                f"{record.ruler_version} for {cid}"
            )
        print(f"arm {name}: every election read its head-to-head's freeze plus its own lines")
    ev = subject_evidence(stores, [Subject(c) for c in subjects])
    table = ev.head_to_head
    assert table is not None
    (workspace / "head_to_head.json").write_text(table.model_dump_json(indent=1), encoding="utf-8")
    print(
        f"head-to-head {table.guard.head_to_head_id}: scorer {ev.scorer_id}, differs_on "
        f"{[d.value for d in table.guard.differs_on]}, contrasts "
        f"{[(c.a.optimizer, c.b.optimizer, c.guard.state.value) for c in table.contrasts]}, "
        f"guard {[(r.optimizer, r.guard.state.value) for r in table.rows]}"
    )
    # An act on an arm is admitted, and takes its campaign out (decisions.md § 0009).
    name, (cycle, cid) = next(iter(arms.items()))
    forked = await CommandDispatcher(stores, inline=Inline()).dispatch(
        CommandCall(
            ForkCyclePayload(campaign_id=cid, cycle_id=cycle.name, round=1, keep_rounds=True),
            door=Door.TERMINAL,
        )
    )
    launched = forked.result
    assert isinstance(launched, Launched) and launched.held is not None
    await (await launched.held).run_inline(readout_sink=print)
    after = subject_evidence(stores, [Subject(c) for c in subjects]).head_to_head
    assert after is not None
    row = next(r for r in after.rows if r.campaign_id == cid)
    if row.guard.state is not GuardState.UNCONTROLLED:
        raise SystemExit(f"offline run: arm {name} forked and still reads {row.guard.state}")
    print(f"arm {name}: forked, and left its comparison: {row.guard.sentence}")


def _graded(bench: dict[str, Any] | None) -> bool:
    return bench is not None and bench["state"] == "read"


FLOAT_DP = 6


_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMBEDDED_HEX32 = re.compile(r"[0-9a-f]{32}")
_EMBEDDED_HEX16 = re.compile(r"(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])")
_SHORT_HEX = re.compile(r"\b[0-9a-f]{6,12}\b")
_CYCLE_ID = re.compile(r"cycle_[0-9a-f]{8,}")
_FORK_ID = re.compile(r"cycle_[0-9a-f]{8,}_fork_[0-9a-f]{8}")
_CAMPAIGN_ID = re.compile(
    # A root campaign's 6 hex, an inner one's 12 (`spawn.py::inner_campaign_id`).
    f"(?:{'|'.join(re.escape(dataset_id_prefix(d)) for d in (DATASET, L4_DATASET))})__[0-9a-f]{{6}}(?:[0-9a-f]{{6}})?"
)
_SESSION_ID = re.compile(r"\bs_[0-9a-f]{8}\b")
_WIN_PATH = re.compile(r"(?:\\\\\?\\)?[A-Za-z]:[\\/][^\s'\"<>|]*")


class Canon:
    def __init__(self, ids: dict[str, str], forks: dict[str, str]) -> None:
        self.ids = ids
        self.forks = forks
        # Prose names a candidate by an id prefix ("crossover 2dda4b+917e53", "parent c27b1862").
        self.short = {k[:n]: v for k, v in ids.items() for n in range(6, 13)}
        self.hashes: dict[str, str] = {}

    def s(self, v: str) -> str:
        if v in self.ids:
            return self.ids[v]
        if _HEX32.match(v) or _UUID.match(v):
            return "<id>"
        if _HEX16.match(v):
            # A content hash (a ruler over fitted δ, a digest) carries a platform's float noise, so
            # the trace keeps only which hashes are one; a behaviour key then reads alike anywhere.
            return self.hashes.setdefault(v, f"<hash {len(self.hashes)}>")
        v = _EMBEDDED_HEX32.sub(lambda m: self.ids.get(m.group(0), "<id>"), v)
        # An L4 instrument id embeds the cell estimator, which hashes this very trace.
        v = _EMBEDDED_HEX16.sub(
            lambda m: self.hashes.setdefault(m.group(0), f"<hash {len(self.hashes)}>"), v
        )
        v = _SHORT_HEX.sub(lambda m: self.short.get(m.group(0), m.group(0)), v)
        v = _CAMPAIGN_ID.sub("<campaign>", v)
        v = _FORK_ID.sub(lambda m: self.forks[m.group(0)], v)
        v = _CYCLE_ID.sub("<cycle>", v)
        v = _SESSION_ID.sub("<session>", v)
        return _WIN_PATH.sub("<path>", v)

    def __call__(self, o: Any) -> Any:
        if isinstance(o, dict):
            return {self.s(str(k)): self(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [self(v) for v in o]
        if isinstance(o, bool) or o is None or isinstance(o, int):
            return o
        if isinstance(o, float):
            if o != o or o in (float("inf"), float("-inf")):
                return str(o)
            r = round(o, FLOAT_DP)
            return 0.0 if r == 0 else r
        return self.s(str(o))


def _proposed(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in ledger
        if r.get("record_type") == "arm" and (r.get("state") or {}).get("state") == "proposed"
    ]


def _id_map(ledger: list[dict[str, Any]], rounds: list[dict[str, Any]]) -> dict[str, str]:
    """Each arm key by the label of its FIRST walk; a parent no round walked by the round reading it."""
    ids: dict[str, str] = {}
    for d in rounds:
        for row in _ranked(d):
            if row.reading.arm.label != "parent":
                ids.setdefault(row.reading.arm.key, row.reading.arm.label)
    for r in _proposed(ledger):
        lineage = r["state"]["individual"].get("lineage") or {}
        for pid in lineage.get("parent_ids") or []:
            ids.setdefault(pid, f"parent@R{r.get('round')}")
    for d in rounds:
        for row in _ranked(d):
            ids.setdefault(row.reading.arm.key, f"parent@R{d.get('round')}")
    return ids


def _cycle_index(cycle: Path) -> CycleIndex:
    """A cycle the world left as a bare tree, folded off its chain as the store folds a hop's."""
    index = fold_cycle_index(ledger_chain(CycleDir(cycle), moment=None))
    assert index is not None, f"{cycle.name} ran without minting its cycle"
    return index


def _fork_labels(cycle: Path) -> dict[str, str]:
    """Each fork of *cycle*'s campaign by the rounds its line was cut at: its id hashes the clock."""
    parents: dict[str, tuple[str | None, int | None]] = {}
    for minted in cycle.parent.iterdir():
        index = _cycle_index(minted)
        parents[minted.name] = (index.parent_cycle_id, index.cut_round)

    def label(cycle_id: str) -> str:
        parent, cut = parents[cycle_id]
        return "<cycle>" if parent is None else f"{label(parent)}/R{cut}"

    labels = {c: label(c) for c, (parent, _) in parents.items() if parent is not None}
    if len(set(labels.values())) < len(labels):
        raise SystemExit(f"offline run: two forks of one parent cut at one round: {labels}")
    return labels


@dataclass(frozen=True)
class One:
    """The sub-document at *path* (``""``: the document itself), read by *pick*."""

    path: str
    pick: Pick


@dataclass(frozen=True)
class Each:
    """Each item of the map (by its data key) or the list at *path*, read by *pick*, a list's
    sorted *by* one of its picks where it lands in arrival order."""

    path: str
    pick: Pick
    by: str | None = None


# A decision the trace reads: a leaf by its model path, or a map of the trace's own names. A model
# rename moves only its path here, never a name the trace hashes (`behaviour_keys`).
type Pick = str | One | Each | Mapping[str, Pick]


def _at(doc: Any, path: str) -> Any:
    for step in path.split(".") if path else ():
        doc = doc.get(step) if isinstance(doc, dict) else None
    return doc


def picked(doc: Any, pick: Pick) -> Any:
    """What *pick* names in *doc*: absent where *doc* is."""
    if isinstance(pick, str):
        return _at(doc, pick)
    if doc is None:
        return None
    if isinstance(pick, One):
        return picked(_at(doc, pick.path), pick.pick)
    if isinstance(pick, Each):
        items = _at(doc, pick.path)
        if isinstance(items, dict):
            return {k: picked(v, pick.pick) for k, v in items.items()}
        if items is None:
            return None
        out = [picked(v, pick.pick) for v in items]
        return out if pick.by is None else sorted(out, key=lambda c: str(c[pick.by]))
    return {name: picked(doc, p) for name, p in pick.items()}


ESTIMATE: Pick = {"value": "value", "lo": "ci_lo", "hi": "ci_hi", "n": "n"}
PAIR: Pick = {
    "a": "a.address.arm.label", "b": "b.address.arm.label", "read": "state",
    "cells": "cell_set.name", "scored": "coverage.scored", "coverage": "coverage.state",
    "lift": One("headline.estimate", ESTIMATE), "side": "headline.side", "p": "headline.p_value",
}  # fmt: skip
ELECTION: Pick = {
    "leading": "leading", "ending": "ending", "parent": "parent.arm", "electable": "electable",
    "elects_on": "elects_on", "reason": "verdict_reason", "deciding": One("deciding_pair", PAIR),
    "fit": Each("fit", {
        "theta": "theta", "se": "theta_se", "caveat": "theta_caveat",
        "lift": One("lift", ESTIMATE), "vs_parent": One("vs_reference", PAIR),
    }),
}  # fmt: skip
# A cell row's `cached` is which of a round's cells a launch paid and which it replayed (`ROW_SPLIT`).
ROW: Pick = {
    "position": "position", "predicted": "predicted", "ground_truth": "ground_truth",
    "fitness": "fitness", "objective": "objective", "cached": "cached", "error": "error_category",
}  # fmt: skip
ROUND: Pick = {
    "round": "round",
    "election": One("election", ELECTION),
    "health": {"grade": "health.grade", "cause": "health.cause"},
    "origin_panel": One("origin_panel", {
        "reading": One("reading", {
            "advance": "advance", "stall": "stall", "positions": "positions",
            "lead": One("lead", PAIR), "vs_previous_pick": One("vs_previous_pick", PAIR),
            "earlier": Each("earlier", PAIR),
            "triggered": Each("triggered", {"trigger": "trigger", "reading": One("reading", PAIR)}),
        }),
        "raced": "raced",
    }),
    "arms": Each("arms", {
        "state": "state", "role": "role", "reason": "reason.kind",
        "eliminated": "reason.elimination.reason", "eliminated_at": "reason.elimination.context.gate",
        "locked_at": "locked_in.context.gate", "position": "position.idx",
        "expected_samples": "expected_samples", "overlay": "overlay", "params": "resolved_params",
        "failures": Each("validation_failures", "reason"), "prompts": "individual.prompts",
    }),
    "folds": Each("folds", {
        "fitness": "fitness", "composite": "composite_fitness", "band": One("fitness_band", ESTIMATE),
        "total": "total", "walked": "walked", "degraded": "degraded", "deprecated": "deprecated",
    }),
    "cells": Each("cells", Each("", ROW, by="position")),
    "panel_cells": Each("panel_rows", Each("", ROW)),
    "population": Each("optimizer_state.population", {
        "parents": "lineage.parent_ids", "changes": "lineage.changes_description",
    }),
    "l1_parse_failure": "optimizer_state.outputs.l1_readout.l1_parse_failure",
}  # fmt: skip
INDEX: Pick = {
    "stop_reason": "stop_reason",
    "rounds_to_separable": "final.rounds_to_separable",
    "rounds_to_improved": "final.rounds_to_improved",
    "rounds_to_ceiling": "final.rounds_to_ceiling",
    "mode": "final.mode",
    "prompt_fields": "final.result_prompt_fields",
    "pipeline_params": "final.result_pipeline_params",
    "rounds": Each("rounds", {"round": "round", "fitness": "fitness", "health": "health.grade"}),
}
# What the held-out passes read and how far each got, never the served answer around them
# (can-run, the control, cost).
LEVEL: Pick = {
    "round": "round", "n": "n", "headline": "headline", "level": One("level", ESTIMATE),
    "fitness": One("fitness", ESTIMATE), "composite": One("composite", ESTIMATE),
}  # fmt: skip
HELD_OUT: Pick = {
    "state": "status.state", "stop": "status.stop", "scored": "status.scored",
    "expected": "status.expected", "origin": One("origin", LEVEL),
    "selected": One("selected", LEVEL), "vs_origin": One("vs_origin", PAIR),
}  # fmt: skip
LEDGER: dict[str, tuple[str, Pick]] = {
    "decision": ("decisions", {"round": "round", "kind": "kind", "outcome": "outcome", "inputs": "inputs"}),
    "election": ("elections", {"round": "round", "leading": "leading", "ending": "ending", "parent": "parent.arm"}),
    "arm": ("arms", {"round": "round", "arm": "arm", "state": "state.state", "position": "state.position.idx"}),
    "phase": ("phases", {"round": "round", "phase": "phase", "step": "step"}),
    "round_entered": ("rounds_entered", {"round": "round"}),
    "round_closed": ("rounds_closed", {
        "round": "round", "ending": "election.ending", "ruler_excluded": "election.ruler.excluded",
        "levels": Each("level_series.levels", {"theta": "theta", "se": "se", "cells": "cells", "caveat": "caveat"}),
    }),
    "run_phase": ("run_phases", {"run_phase": "state.run_phase", "stop_reason": "state.stop_reason"}),
    "llm_call": ("optimizer_calls", {
        "round": "round", "node": "node", "candidate_idx": "candidate_idx",
        "repairs": "payload.schema_repair_errors",
    }),
    "round_warning": ("round_warnings", {"round": "round", "kind": "kind", "severity": "severity", "detail": "detail"}),
    # The origin's reserve is the selection pass's set-aside: what the run holds back is decided.
    "pass_graded": ("passes_graded", {
        "subject": "subject", "tolerance": "tolerance", "reserve_usd": "reserve_usd",
        "reserve_tokens": "reserve_tokens", "state": "state",
    }),
    "race_standing": ("standings", {
        "round": "round", "member": "member", "current": "current", "n_samples": "n_samples",
        "p_best": "p_best", "paired_breakdown": "paired_breakdown",
    }),
}  # fmt: skip


def _as_sets(o: Any) -> Any:
    # A decision lists the arms it raced in the order they ARRIVED (CAPO's `raced_against`).
    if isinstance(o, dict):
        return {k: _as_sets(v) for k, v in o.items()}
    if isinstance(o, list) and all(isinstance(v, str) for v in o):
        return sorted(o)
    return [_as_sets(v) for v in o] if isinstance(o, list) else o


def _ledger(ledger: list[dict[str, Any]], canon: Canon) -> dict[str, Any]:
    out: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in LEDGER.values()}
    for r in ledger:
        if (kind := r.get("record_type")) in LEDGER:
            name, pick = LEDGER[kind]
            entry = canon(picked(r, pick))
            out[name].append(_as_sets(entry) if kind == "decision" else entry)
    # Concurrent arms append in arrival order; the SET per round is what a comparison reads.
    for name in ("decisions", "round_warnings", "optimizer_calls", "standings", "arms"):
        out[name].sort(key=lambda x: (x.get("round") or 0, json.dumps(x, sort_keys=True)))
    # Each launch grades the line's held passes again: one reading of a pass is one decision.
    graded = {json.dumps(x, sort_keys=True): x for x in out["passes_graded"]}
    out["passes_graded"] = [graded[k] for k in sorted(graded)]
    return out


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _ranked(d: dict[str, Any]) -> list[ScoreboardRow]:
    """The rank a round file's arms read in: it stores facts, the rank is `RoundOutcome.ranked`."""
    return RoundOutcome.model_validate({k: d[k] for k in RoundOutcome.model_fields}).ranked()


def extract(cycle: Path, bench: BenchScore | None) -> dict[str, Any]:
    """The decisions *cycle* took, each by the trace's own name (`Pick`), canonical."""
    rounds = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((cycle / "rounds").glob("*.json"))
    ]
    ledger = _jsonl(cycle / ".runtime" / "ledger.jsonl")
    ids = _id_map(ledger, rounds)
    canon = Canon(ids, _fork_labels(cycle))
    run = picked(_cycle_index(cycle).model_dump(mode="json"), INDEX)
    run["bench"] = None if bench is None else picked(bench.model_dump(mode="json"), HELD_OUT)
    return {
        "run": canon(run),
        "rounds": [
            canon(
                {
                    **picked(d, ROUND),
                    "scoreboard_order": [
                        ids.get(r.reading.arm.key, r.reading.arm.label) for r in _ranked(d)
                    ],
                }
            )
            for d in rounds
        ],
        "ledger": _ledger(ledger, canon),
    }


def digests() -> dict[str, str]:
    check_every_treatment()
    out = {"estimator": BehaviourKeys.held().cell_estimator}
    for name in sorted(optimizers.MANIFESTS.table):
        selected = select_optimizer(bench_choice(name))
        out[f"treatment:{name}"] = selected.treatment(
            frozenset(Presumption), PackShelf(account=None)
        ).digest
    return out


def behaviour_keys(home: Path, names: Sequence[str]) -> bool:
    """Regenerates `nodes.py::BEHAVIOUR_KEYS_FILE` from each built-in's straight-run trace in
    *home*, naming it on a `wrote` or `unchanged` line (`gate.py::_rewrote`); `True` where it moved."""
    builtin = {
        n for n in optimizers.MANIFESTS.table
        if optimizers.MANIFESTS.get(n).directory.is_relative_to(PACKAGE_ROOT)
    }  # fmt: skip
    held = BehaviourKeys.held()
    fingerprints = {n: f for n, f in held.fingerprints.items() if n in builtin}
    if held.world != WORLD:
        fingerprints = {}
    for name in sorted(builtin.intersection(names)):
        trace = BehaviourTrace.read(home / name)
        fingerprints[name] = stable_hash([trace.decisions, trace.requests, unrun(name, home)])
    fingerprint = held.estimator
    if "potter" in names and (nested := home / "potter" / L4 / TRACE).is_file():
        fingerprint = stable_hash(BehaviourTrace.read(nested.parent).inner_half())
    kept = BehaviourKeys(
        world=WORLD, fingerprints=dict(sorted(fingerprints.items())), estimator=fingerprint
    )
    text = json.dumps(kept.model_dump(mode="json"), indent=1) + "\n"
    wrote = replace_changed(BEHAVIOUR_KEYS_FILE, text)
    print(f"{'wrote' if wrote else 'unchanged'} {BEHAVIOUR_KEYS_FILE.relative_to(REPO).as_posix()}")
    return wrote


def unrun(name: str, home: Path) -> dict[str, list[str]]:
    """Per file of *name*'s member packages, by package-relative path, the code its straight run
    cannot vouch for: what the world ran is keyed by its behaviour, the rest by its AST
    (`_unvouched`). Raises where the run's ran-lines name none of those files, which would read
    as "nothing ran" (a trace filed by another tree, a leg that filed nothing)."""
    ran = ran_lines(home / name, recursive=False)
    roster = select_optimizer(bench_choice(name)).member_nodes
    files = {
        path.relative_to(PACKAGE_ROOT).as_posix(): path
        for package in {member_package_dir(NODE_MEMBERS.table[n]) for n in roster}
        for path in package.rglob("*.py")
    }
    if not ran.keys() & files.keys():
        raise LookupError(f"{home / name}'s ran-lines name no file of {name}'s member packages")
    return {
        rel: _unvouched(
            ast.parse(path.read_text(encoding="utf-8")).body, ran[rel], "", inside=False
        )
        for rel, path in sorted(files.items())
    }


_BODIES = ("body", "orelse", "finalbody", "handlers", "cases")


def _unvouched(nodes: list[Any], ran: set[int], path: str, *, inside: bool) -> list[str]:
    """Each statement's `ast.dump` (no positions: formatting and comments never key) under its
    nesting path, its bodies walked apart: every statement outside a function body, which runs at
    import whatever the run reached, and each one inside that the run never reached. A def keys
    its decorators and defaults, never its name or parameters: its body's behaviour is the trace."""
    keyed: list[str] = []
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            header = [*node.decorator_list, *args.defaults, *filter(None, args.kw_defaults)]
            keyed += [f"{path}def {ast.dump(e)}" for e in header]
            keyed += _unvouched(node.body, ran, f"{path}def/", inside=True)
            continue
        bodies = [f for f in _BODIES if isinstance(getattr(node, f, None), list)]
        first = getattr(node, "lineno", None) or node.pattern.lineno
        last = getattr(node, "end_lineno", None) or node.body[-1].end_lineno
        if not inside or not any(at in ran for at in range(first, last + 1)):
            head = copy.copy(node)
            for field in bodies:
                setattr(head, field, [])
            keyed.append(path + ast.dump(head))
        for field in bodies:
            keyed += _unvouched(
                getattr(node, field), ran, f"{path}{type(node).__name__}.{field}/", inside=inside
            )
    return keyed


def traced(out: Path) -> None:
    """Files into *out* at exit each package line a function of this process ran, as PEP 669
    reports it (once a line, then disabled, so the run pays for each line once), by
    package-relative POSIX path, so a trace reads the same from every tree."""
    package = f"{PACKAGE_ROOT}{os.sep}"
    ran: dict[str, set[int]] = defaultdict(set)
    monitoring = sys.monitoring

    def line(code: types.CodeType, at: int) -> object:
        if code.co_filename.startswith(package):
            ran[code.co_filename].add(at)
        return monitoring.DISABLE

    def filed() -> None:
        lines = {
            Path(name).relative_to(PACKAGE_ROOT).as_posix(): sorted(at) for name, at in ran.items()
        }
        (out / f"{RAN_LINES}-{os.getpid()}.json").write_text(json.dumps(lines), encoding="utf-8")

    monitoring.use_tool_id(monitoring.COVERAGE_ID, OFFLINE_RUN)
    monitoring.register_callback(monitoring.COVERAGE_ID, monitoring.events.LINE, line)
    monitoring.set_events(monitoring.COVERAGE_ID, monitoring.events.LINE)
    atexit.register(filed)


def _function_lines(path: Path) -> set[int]:
    """A module or class body runs at import, which says nothing of what the run reached."""
    todo = [(compile(path.read_text(encoding="utf-8"), str(path), "exec"), False)]
    lines: set[int] = set()
    while todo:
        code, inside = todo.pop()
        inside = inside or bool(code.co_flags & inspect.CO_NEWLOCALS)
        if inside:
            lines.update(at for _, _, at in code.co_lines() if at is not None)
        todo.extend(
            (const, inside) for const in code.co_consts if isinstance(const, types.CodeType)
        )
    return lines


def ran_lines(workspace: Path, *, recursive: bool) -> defaultdict[str, set[int]]:
    """The lines the legs run in *workspace* ran, by package-relative path; its own process alone
    unless *recursive*."""
    ran: defaultdict[str, set[int]] = defaultdict(set)
    pattern = f"{RAN_LINES}-*.json"
    for filed in workspace.rglob(pattern) if recursive else workspace.glob(pattern):
        for name, lines in json.loads(filed.read_text(encoding="utf-8")).items():
            ran[name].update(lines)
    return ran


def coverage_by_package(home: Path) -> list[str]:
    ran = ran_lines(home, recursive=True)
    counted: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for path in PACKAGE_ROOT.rglob("*.py"):
        parts = path.relative_to(PACKAGE_ROOT).parts
        package = "/".join(parts[: min(2, len(parts) - 1)]) or "promptpotter"
        body = _function_lines(path)
        counted[package][0] += len(body & ran["/".join(parts)])
        counted[package][1] += len(body)
    total = [sum(hit for hit, _ in counted.values()), sum(n for _, n in counted.values())]
    return [
        f"coverage {package}: {hit}/{n} function-body lines ran ({100 * hit // max(n, 1)}%)"
        for package, (hit, n) in sorted({**counted, "(all)": total}.items())
    ]


def named_home() -> Path:
    named = os.environ.get("PROMPTPOTTER_HOME")
    if not named:
        raise SystemExit("offline run: set PROMPTPOTTER_HOME to the directory it may write into")
    return Path(named.removeprefix("\\\\?\\")).expanduser().resolve()


def unreached(names: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Per optimizer, the member packages and manifest directories it neither imports nor resolves."""

    def owned(directory: Path) -> str | None:
        full = directory.resolve()
        return f"{full.relative_to(REPO).as_posix()}/" if full.is_relative_to(REPO) else None

    packages = {
        node: owned(Path(inspect.getfile(type(member))).parent)
        for node, member in NODE_MEMBERS.table.items()
    }
    owners = {d for d in packages.values() if d} | {
        d for n in optimizers.MANIFESTS.table if (d := owned(optimizers.MANIFESTS.get(n).directory))
    }

    def imports(files: Iterable[Path]) -> set[str]:
        hit: set[str] = set()
        for path in files:
            for target in imported_files(path):
                hit.update(d for d in owners if target.startswith(d))
        return hit

    # Only the optimizers package imports a member package (`gate.py::_IMPORTS_OPTIMIZER`).
    shared = imports(Path(optimizers.__file__).parent.glob("*.py"))
    out = {}
    for name in names:
        roster = select_optimizer(bench_choice(name)).member_nodes
        named = [packages[n] for n in roster] + [owned(d) for d in manifest_directories(name)]
        reached = shared | {d for d in named if d}
        todo = list(reached)
        while todo:
            fresh = imports((REPO / todo.pop()).rglob("*.py")) - reached
            reached |= fresh
            todo.extend(fresh)
        out[name] = tuple(sorted(owners - reached))
    return out


def optimizer_keys(kept: LastGreen, names: Sequence[str], *, controlled: bool) -> dict[str, str]:
    # A controlled workspace runs several optimizers, so it reads every one.
    skipped = {} if controlled else unreached(names)
    # Its own output is no source: regenerating the keys reruns no optimizer.
    keyed = BEHAVIOUR_KEYS_FILE.relative_to(REPO).as_posix()
    return {
        name: kept.key(OFFLINE_RUN_READS, without=(*skipped.get(name, ()), keyed)) for name in names
    }


def still_green(home: Path, keys: dict[str, str]) -> set[str]:
    # In the workspace, never the clone's store: another tree rewriting the shared home takes it.
    stamps = {n: home / n / GREEN for n in keys}
    return {n for n, k in keys.items() if stamps[n].is_file() and stamps[n].read_text("utf-8") == k}


def holds_run(home: Path, run: str) -> bool:
    try:
        return bool(json.loads((home / STAMP).read_text(encoding="utf-8")).get("run") == run)
    except (OSError, ValueError):
        return False


def claim_home(home: Path, stamp: dict[str, object]) -> Path:
    """Stamped before anything else is written, so a killed run leaves a claimable home."""
    if home.is_dir() and any(home.iterdir()):
        if not (home / STAMP).is_file():
            raise SystemExit(f"offline run: {home} holds files and no {STAMP}; refusing to write")
        rmtree_robust(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP).write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
    return home


def prepared_home(home: Path, stamp: dict[str, object], due: Sequence[str], *, held: bool) -> None:
    """*home* cleared for *due*'s workspaces."""
    if held:
        for name in due:
            if (home / name).exists():
                rmtree_robust(home / name)
    else:
        claim_home(home, stamp)


def child_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in [*env, *Settings.model_fields]:
        if key.endswith(("_KEY", "_TOKEN")):
            env[key] = ""
    # The SDKs refuse an EMPTY key before sending; every transport they could reach is faked.
    env.update(dict.fromkeys(PROVIDER_KEYS, NOT_A_KEY))
    env.update(PROMPTPOTTER_HOME=long_path(root), PYTHONUTF8="1")
    return env


def decided(workspace: Path, *, after: int) -> dict[str, Any]:
    """Less what a resume legitimately moves: its init records, the checkouts it deleted, the round
    it re-enters, and which of a round's cells it paid and which it replayed, by path (`CELL_ROWS`): a
    cell answered before a pause inside a round replays on the resume. The harness's call counts
    stay, so a resume that re-walked a banked round reads MOVED."""
    doc = BehaviourTrace.read(workspace).decisions
    kept = {k: v for k, v in doc["ledger"].items() if k not in RESUME_APPENDS}

    def less(node: Any, split: tuple[str, ...]) -> Any:
        return {k: v for k, v in node.items() if k not in split} if isinstance(node, dict) else node

    def unsplit(node: Any, key: str = "") -> Any:
        if isinstance(node, list):
            return [unsplit(v) for v in node]
        if not isinstance(node, dict):
            return node
        if key in CELL_ROWS:
            return {arm: [less(row, ROW_SPLIT) for row in rows] for arm, rows in node.items()}
        return {k: unsplit(v, k) for k, v in node.items()}

    rounds = [r for r in doc["rounds"] if r["round"] > after]
    out: dict[str, Any] = unsplit({**doc, "ledger": kept, "rounds": rounds})
    return out


def recombined(workspace: Path, host: str) -> int:
    """Distinct lineages naming two parents whose *host* step moved a locus."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            steps = node.get("variations")
            if (
                len(node.get("parent_ids") or ()) == 2
                and isinstance(steps, list)
                and any(s["node"].endswith(f":{host}") and s["loci"] for s in steps)
            ):
                found.add(json.dumps(node, sort_keys=True))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in data_root(workspace).glob("projects/*/campaigns/*/cycles/*/rounds/*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return len(found)


def crossed_legs(
    potter: Path, legs: tuple[Future[int], Future[int], Future[int]], pause_after: int
) -> bool:
    """Prints the crossover legs' reading; True where they ran green."""
    straight, paused, by_l2 = (
        potter / leg for leg in (CROSSED, f"{CROSSED}-{RESUMED}", f"{CROSSED}-l2")
    )
    if any(exits := [leg.result() for leg in legs]):
        print(f"potter: crossover on FAILED (exit {exits}) -- {straight / 'run.log'}")
        return False
    hosted = {
        "locus_crossover": recombined(straight, "locus_crossover"),
        "l1_generate": recombined(straight, "l1_generate"),
        "l2_context": recombined(by_l2, "l2_context"),
    }
    twins = [decided(at, after=pause_after) for at in (straight, paused)]
    # A move of the bill alone is spend, read beside the record: an errored cell never replays.
    # A stopped pass the resume sends again is bought twice, so each reading counts once.
    cells = [twin.pop("harness")["backend_cells"] for twin in twins]
    for twin in twins:
        passes = twin["ledger"]["passes_graded"]
        for graded in passes:
            del graded["reserve_tokens"], graded["reserve_usd"]
        twin["ledger"]["passes_graded"] = sorted({json.dumps(g, sort_keys=True) for g in passes})
    same = twins[0] == twins[1]
    # The origin's charged errors stand in the bar its first election read, which the resume replays.
    charged = sum(
        r["record_type"] == "arm" and r["round"] == 0 and '"PIPELINE"' in json.dumps(r)
        for r in leg_ledger(straight)
    )
    print(
        f"potter: crossover on, two-parent arms recombined by host {hosted}; its origin charged "
        f"{charged} pipeline errors; paused after round {pause_after} and resumed, decisions "
        f"{'UNMOVED' if same else 'MOVED'} (backend cells {cells[0]} -> {cells[1]})"
    )
    return all(hosted.values()) and same and charged > 0


def less_heals(workspace: Path) -> dict[str, Any]:
    """Less what a heal adds to a call: its sends, and the failed attempts the call records."""
    doc = BehaviourTrace.read(workspace).decisions
    calls = sorted(
        ({**c, "repairs": None} for c in doc["ledger"]["optimizer_calls"]),
        key=lambda c: json.dumps(c, sort_keys=True),
    )
    harness = {k: v for k, v in doc["harness"].items() if k != "llm_calls"}
    return {**doc, "ledger": {**doc["ledger"], "optimizer_calls": calls}, "harness": harness}


def healed_leg(potter: Path, leg: Future[int]) -> bool:
    """Prints the healed leg's reading; True where it decided what potter's straight run did."""
    healed = potter / HEALED
    if rc := leg.result():
        print(f"potter: scripted heals FAILED (exit {rc}) -- {healed / 'run.log'}")
        return False
    calls = BehaviourTrace.read(healed).decisions["ledger"]["optimizer_calls"]
    moved = _moved(less_heals(healed), less_heals(potter), "decisions", 2)
    print(
        f"potter: {len(HEALS)} calls answered invalid healed, {sum(len(c['repairs'] or ()) for c in calls)} "
        f"failed attempts recorded; decisions {'MOVED ' + ', '.join(moved) if moved else 'UNMOVED'}"
    )
    return not moved


def wound_marks(workspace: Path) -> Counter[str]:
    """Each parse failure the run recorded: L1's by round, a layer's warning, a child's refusal."""
    doc = BehaviourTrace.read(workspace).decisions
    marks = Counter(w["kind"] for w in doc["ledger"]["round_warnings"])
    for r in doc["rounds"]:
        if r["l1_parse_failure"] is not None:
            marks[r["l1_parse_failure"]] += 1
        for arm in r["arms"].values():
            marks.update(arm["failures"] or [])
    parse_failures = (PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING, "layer_parse_failure")
    return Counter({mark: n for mark, n in marks.items() if mark in parse_failures})


def wounded_leg(name: str, workspace: Path, leg: Future[int]) -> bool:
    """Prints the wounded leg's reading; True where the run recorded each wound's mark once."""
    wounded = workspace / WOUNDED
    if rc := leg.result():
        print(f"{name}: scripted wounds FAILED (exit {rc}) -- {wounded / 'run.log'}")
        return False
    marks, owed = wound_marks(wounded), Counter(mark for _, mark in WOUNDS[name].values())
    print(
        f"{name}: {len(WOUNDS[name])} calls answered invalid on every send, recorded "
        f"{dict(sorted(marks.items()))}{'' if marks == owed else f' (owed {dict(owed)})'}"
    )
    return marks == owed


def moved_against(home: Path, earlier: Path, names: Sequence[str]) -> list[str]:
    """Per trace every leg of *names* wrote, which of its fields moved against *earlier*'s home;
    a leg the earlier run lacks has no base."""
    said = []
    for name in names:
        legs = [home / name / TRACE, *(home / name).glob(f"*/{TRACE}")]
        for trace in sorted(t for t in legs if t.is_file()):
            leg = trace.parent.relative_to(home).as_posix()
            if not (then := earlier / leg / TRACE).is_file():
                said.append(f"{leg}: against {earlier}: no base")
                continue
            moved = BehaviourTrace.read(trace.parent).moved(BehaviourTrace.read(then.parent))
            said.append(
                f"{leg}: against {earlier}: {'MOVED ' + ', '.join(moved) if moved else 'UNMOVED'}"
            )
    return said


def read_straight(name: str, workspace: Path) -> int:
    ran = BehaviourTrace.read(workspace).decisions["run"]
    (cycle,) = (data_root(workspace) / PROJECTS_DIR).glob("*/campaigns/*/cycles/*")
    bench = ran["bench"]
    lift = None if bench is None else bench["vs_origin"]["lift"]
    headline = (
        f"{lift['value']:+.3f}"
        if lift is not None
        else "NO BENCH HEADLINE"
        if bench is None
        else f"none ({bench['vs_origin']['read']})"
    )
    print(f"{name}: {ran['stop_reason']}, bench lift {headline} -- {cycle}")
    return int(not _graded(bench))


def _the_record(workspace: Path) -> dict[str, Any]:
    """A trace's decisions less the harness's counts: the cells a press sent past a close are the
    bill, and the record is everything else."""
    return {k: v for k, v in BehaviourTrace.read(workspace).decisions.items() if k != "harness"}


def read_warm(
    name: str,
    home: Path,
    warm: Future[int],
    pressed: Future[int],
    resumed: Future[int],
    pressed_resumed: Future[int],
    pause_after: int,
) -> int:
    """*name*'s warm run against the same pressed, and each paused inside a walk then resumed:
    each reads the record the warm run filed, on a ruler that holds a level past round 1."""
    failed = 0
    for leg, rc in (
        (WARM, warm.result()),
        (PRESSED, pressed.result()),
        (RESUMED, resumed.result()),
        (PRESSED_RESUMED, pressed_resumed.result()),
    ):
        if rc:
            print(f"{name}: {leg} FAILED (exit {rc}) -- {home / leg / 'run.log'}")
            failed += 1
    if failed:
        return failed
    rows = {leg: rows_read(home / leg) for leg in (WARM, PRESSED, RESUMED, PRESSED_RESUMED)}
    # Past the origin's round a ruler stands, so a race choosing rows by it never reads cold.
    if cold := [leg for leg, read in rows.items() if "cold" in read[1:]]:
        print(f"{name}: a warm leg chose its rows on a cold ruler past round 1: {cold} {rows}")
        failed += 1
    same_pressed = _the_record(home / PRESSED) == _the_record(home / WARM)
    failed += not same_pressed
    # The press reached the walk, and a cut wasted at most the press; both counts say one bill.
    bill = PressBill.read(home / PRESSED)
    billed = bill.depth == PRESS.cells and bill.most_wasted <= PRESS.cells
    billed = billed and bill.discarded == bill.served
    failed += not billed
    sent = {leg: BehaviourTrace.read(home / leg) for leg in (WARM, PRESSED)}
    # A press moves the bill, never the record: an optimizer request reading a cell the press bought
    # past a close read the running campaign's banked spend as its memory.
    asked = [n for n, h in sent[PRESSED].requests.items() if sent[WARM].requests.get(n) != h]
    failed += bool(asked)
    print(
        f"{name}: pressed {PRESS.cells} cells x {PRESS.abreast} on a warm ruler "
        f"(rows {sorted(set(rows[WARM]))}), backend cells "
        f"{sent[WARM].decisions['harness']['backend_cells']} -> "
        f"{sent[PRESSED].decisions['harness']['backend_cells']}, "
        f"record {'UNMOVED' if same_pressed else 'MOVED'}, at depth {bill.depth} a walk wasted "
        f"at most {bill.most_wasted}, {bill.discarded} discarded (dashboard {bill.served})"
        f"{'' if billed else ' (NOT the press it was handed)'}"
        f"{f', requests MOVED {sorted(asked)}' if asked else ', requests UNMOVED'}"
    )
    failed += resumed_leg(name, home, WARM, RESUMED, pause_after)
    return failed + resumed_leg(name, home, PRESSED, PRESSED_RESUMED, pause_after)


def resumed_leg(name: str, home: Path, straight: str, leg: str, pause_after: int) -> int:
    """*leg* paused inside a walk and resumed against its *straight* twin: the warm run's requests,
    and its twin's decisions, backend cells, bills, priced keys and filed answers."""
    again = decided(home / leg, after=pause_after)
    same = decided(home / straight, after=pause_after) == again
    traces = {at: BehaviourTrace.read(home / at) for at in (WARM, straight, leg)}
    # A cell the press bought and no turn took is no memory of its campaign's, however it ended.
    asked = sorted(n for n, h in traces[leg].requests.items() if traces[WARM].requests.get(n) != h)
    # A cell the race took before the pause, past its opening draw included (potter's `informative`
    # rows), is never bought again, and each is billed and priced once.
    cells = [
        (traces[at].decisions["harness"]["backend_cells"], *bought(home / at))
        for at in (straight, leg)
    ]
    # A cell filed as it arrived and banked at the pause is filed once, as its twin files it.
    answers = [filed_answers(home / at) for at in (straight, leg)]
    refiled = sorted(
        f"{key[1][:8]} {answers[0][key]}->{answers[1][key]}"
        for key in {*answers[0], *answers[1]}
        if answers[0][key] != answers[1][key]
    )
    same = same and cells[0] == cells[1] and not refiled and not asked
    at, inside = paused_at(home / leg)
    # Inside a round past the boundary, whose walk banked.
    landed = inside and at > pause_after
    where = f"inside round {at} past its banked walk" if inside else f"after round {at}"
    print(
        f"{name}: {leg} paused {where}{'' if landed else ' (NOT where its leg pauses)'} and "
        f"resumed, decisions, requests, backend cells, bills, priced keys and filed answers "
        f"{'UNMOVED' if same else 'MOVED'} ({cells[0]} -> {cells[1]}"
        f"{f', requests MOVED {asked}' if asked else ''}"
        f"{f', {len(refiled)} cells filed otherwise {refiled[:3]}' if refiled else ''})"
    )
    return int(not _graded(again["run"]["bench"]) or not same) + int(not landed)


def arrived_leg(workspace: Path, leg: Future[int], pause_after: int) -> bool:
    """Potter's rolling race read in arrival order, paused inside a walk and resumed: before the
    pause some walk took a row ahead of one it sent earlier, and the resumed round reads first
    every row the run filed, in that order (`query_loop.py::_ScoringPhase.filed_reads`). Its
    record is never held to the unpaused run's: the pause cuts the walk where it was pressed."""
    if rc := leg.result():
        print(f"potter: arrival order: FAILED (exit {rc}) -- {workspace / 'run.log'}")
        return False
    at, inside = paused_at(workspace)
    sent: defaultdict[tuple[Any, ...], list[int]] = defaultdict(list)
    took: defaultdict[tuple[Any, ...], list[int]] = defaultdict(list)
    filed: list[tuple[str, int]] = []
    reread: list[tuple[str, int]] | None = None
    for record in leg_ledger(workspace):
        match record.get("record_type"):
            case "run_phase" if record["state"].get("stop_reason") == StopReason.PAUSED:
                reread = []
            case "sample_started" if reread is None and record.get("round"):
                sent[record["round"], record["individual_id"]].append(record["position"])
            case "sample_scored" if record.get("round") == at and record.get("role") != "bench":
                (filed if reread is None else reread).append(
                    (record["individual_id"], record["position"])
                )
            case "sample_scored" if reread is None and record.get("round"):
                took[record["round"], record["individual_id"]].append(record["position"])
    for one, position in filed:
        took[at, one].append(position)
    out_of_order = sum([p for p in sent[w] if p in took[w]] != took[w] for w in took)
    replayed = reread is not None and reread[: len(filed)] == filed
    landed = inside and at > pause_after
    # A rolling race decides on each row returned, so a cut with calls out never waited for them.
    cut_early = PressBill.read(workspace).discarded
    print(
        f"potter: arrival order, {out_of_order} walks read out of launch order, {cut_early} cells "
        f"out at a cut, paused {'inside' if inside else 'after'} round {at} with {len(filed)} "
        f"reads filed, the resume {'replayed them in order' if replayed else 'read them OTHERWISE'}"
    )
    return replayed and landed and out_of_order > 0 and cut_early > 0


def bought(workspace: Path) -> tuple[int, int]:
    """A leg's bills (each call's `money`) and the keys it priced, off its ledger."""
    ledger = leg_ledger(workspace)
    bills = sum((record.get("money") or {}).get("record_type") == "bill" for record in ledger)
    return bills, sum(record.get("record_type") == "priced_key" for record in ledger)


def filed_answers(workspace: Path) -> Counter[tuple[str, str]]:
    """How many answers a leg's archive files per (configuration, sample)."""
    cells = data_root(workspace).glob(f"**/{MEASUREMENTS_DIR}/{ArchiveFile.CELLS.folder}/*")
    return Counter((row["config_key"], row["sample_key"]) for path in cells for row in _jsonl(path))


def leg_ledger_path(workspace: Path) -> Path:
    """The one cycle's ledger a leg ran, off its own data root."""
    (ledger,) = data_root(workspace).glob("projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl")
    return ledger


def leg_ledger(workspace: Path) -> list[dict[str, Any]]:
    return _jsonl(leg_ledger_path(workspace))


def graded_line(workspace: Path) -> tuple[list[str], Counter[str]]:
    """A leg's line, the origin round 0 adopted and the individual each crowning election moved
    to, and the bench cells it graded per individual, off its ledger."""
    line: list[str] = []
    graded: Counter[str] = Counter()
    for record in leg_ledger(workspace):
        match record.get("record_type"):
            case "election" if record.get("ending") in (RoundEnding.ADOPTED, RoundEnding.CROWNED):
                line.append(record["leading"])
            case "sample_scored" if record.get("role") == "bench":
                graded[record["individual_id"]] += 1
    return line, graded


def adopted_leg(name: str, home: Path, leg: Future[int]) -> bool:
    """*name*'s warm line graded under `every_adoption`: one pass, of one width, per adoption of
    the line, the origin's included, on the line `warm/` adopted, where `at_end` grades the origin
    and the final selection alone."""
    workspace = home / name / EVERY
    if rc := leg.result():
        print(f"{name}: every adoption graded: FAILED (exit {rc}) -- {workspace / 'run.log'}")
        return False
    (line, graded), (warm_line, warm_graded) = (
        graded_line(workspace),
        graded_line(home / name / WARM),
    )
    once = set(graded) == set(line) and len(set(graded.values())) == 1
    held = line == warm_line and set(warm_graded) == {line[0], line[-1]}
    print(
        f"{name}: every adoption graded, {len(line)} adoptions, {len(graded)} passes of "
        f"{sorted(set(graded.values()))} cells{'' if once else ' (NOT one per adoption)'}, at_end "
        f"{len(warm_graded)}{'' if held else ' (NOT the origin and the final selection)'}"
    )
    return once and held


def replicated_leg(name: str, workspace: Path, leg: Future[int] | None) -> bool:
    """*name*'s origin re-scored `REPLICATES` times at round 0: one noise band, filed once, over
    exactly the rows round 0 recorded, each re-scored by every replicate, and stood beside the
    recorded level of those rows."""
    if leg is None:
        return True
    if rc := leg.result():
        print(f"{name}: a replicated origin: FAILED (exit {rc}) -- {workspace / 'run.log'}")
        return False
    ledger = leg_ledger(workspace)
    bands = [
        r
        for r in ledger
        if r.get("record_type") == "pass_graded" and r["verification"]["kind"] == "noise_floor"
    ]
    origin = bands[0]["verification"]["arm"]["key"] if bands else None
    recorded = {
        r["result"]["sample_key"]
        for r in ledger
        if r.get("record_type") == "sample_scored"
        and r.get("round") == 0
        and r.get("role") == "origin"
        and r.get("individual_id") == origin
    }
    held = len(bands) == 1 and bool(recorded)
    if held:
        (band,) = bands
        rows, reading = band["verification"]["sample_keys"], band["reading"] or {}
        held = (
            sorted(rows) == sorted(recorded)
            and len(band["verification"]["cells"]) == REPLICATES * len(rows)
            and len(reading.get("raw", ())) == REPLICATES
            and (reading.get("recorded") or {}).get("n") == len(rows)
        )
    print(
        f"{name}: a replicated origin filed {len(bands)} noise bands over {len(recorded)} recorded "
        f"rows, {REPLICATES} replicates {'each' if held else 'NOT each'} re-scoring them"
    )
    return held


def bench_paused_leg(home: Path, leg: Future[int]) -> bool:
    """Potter's warm run paused inside its end pass and resumed: the pass banks pending, stopped
    by the pause on the cells it walked, and the resumed pass replays each of them and reads the
    bench `warm/` read."""
    workspace = home / "potter" / BENCHED
    if rc := leg.result():
        print(f"potter: a pause inside a bench pass: FAILED (exit {rc}) -- {workspace / 'run.log'}")
        return False
    passes = [
        (r["state"], r["verification"])
        for r in leg_ledger(workspace)
        if r.get("record_type") == "pass_graded" and r["verification"]["kind"] == "bench"
    ]
    paused: dict[str, Any] = next((v for state, v in passes if state == "pending"), {})
    width = max((len(v["cells"]) for _, v in passes), default=0)
    banked = {c[0] for c in paused.get("cells", ())}
    stopped = (paused.get("stop") or {}).get("cause") == StopReason.PAUSED and 0 < len(
        banked
    ) < width
    resumed = stopped and any(
        state == "read"
        and v["arm"] == paused["arm"]
        and banked <= {c[0] for c in v["cells"] if c[2]}
        for state, v in passes
    )
    # It names an arm by its label, never by the id first appearance on the ledger names.
    bench = [decided(at, after=0)["run"]["bench"] for at in (workspace, home / "potter" / WARM)]
    same = bench[0] == bench[1]
    print(
        f"potter: a pause inside a bench pass banked "
        f"{len(banked)} of {width} cells "
        f"{'stopped by it' if stopped else 'NOT as stopped by it'}, the resume "
        f"{'replayed them' if resumed else 'bought them AGAIN'}, its bench "
        f"{'UNMOVED' if same else 'MOVED'} from warm/"
    )
    return resumed and same


def skipped_leg(name: str, home: Path, straight: Future[int], twin: Future[int]) -> bool:
    """*name*'s skip stands at the depth it was pressed (`SKIP_AT`), and its twin, paused inside the
    skipped walk and resumed, replays it there: the same standing skip, decisions, backend cells,
    bills and priced keys. A replay recording no depth leaves the next resume skipping at zero."""
    legs = (home / name / SKIPPED, home / name / f"{SKIPPED}-{RESUMED}")
    if rcs := [rc for leg in (straight, twin) if (rc := leg.result())]:
        print(f"{name}: a skip and its resumed twin: FAILED (exit {rcs}) -- {legs[0] / 'run.log'}")
        return False
    complete_registries()
    skips = [scan_standing_rounds([LedgerSpan(leg_ledger_path(leg))]).skipped for leg in legs]
    cells = [
        (BehaviourTrace.read(leg).decisions["harness"]["backend_cells"], *bought(leg))
        for leg in legs
    ]
    same = decided(legs[0], after=0) == decided(legs[1], after=0)
    at, inside = paused_at(legs[1])
    held = list(skips[0].values()) == [SKIP_AT]
    replayed = skips[0] == skips[1] and cells[0] == cells[1] and same
    print(
        f"{name}: a skip stands at {list(skips[0].values())} cells"
        f"{'' if held else f' (NOT {SKIP_AT})'}, its twin paused "
        f"{'inside' if inside else 'after'} round {at} and resumed, skip, decisions and cells "
        f"{'UNMOVED' if replayed else f'MOVED ({skips[1]}, {cells[0]} -> {cells[1]})'}"
    )
    return held and replayed and inside


def rows_read(workspace: Path) -> list[str]:
    """The row choice each round's measure opened on, in ledger order (`MeasureOpened.rows`)."""
    return [
        act["rows"]
        for record in leg_ledger(workspace)
        if (act := record.get("act")) and act.get("act") == "measure_opened"
    ]


@dataclass(frozen=True)
class PressBill:
    """What a press bought past the record, off a leg's ledger and its dashboard: the deepest
    look-ahead a round's walk sent at, the most cells one walk's cut discarded (sent, never
    taken), and the discards the ledger and the dashboard each count."""

    depth: int
    most_wasted: int
    discarded: int
    served: int

    @classmethod
    def read(cls, workspace: Path) -> PressBill:
        started: defaultdict[tuple[Any, ...], set[int]] = defaultdict(set)
        scored: defaultdict[tuple[Any, ...], set[int]] = defaultdict(set)
        depth = 0
        for record in leg_ledger(workspace):
            kind = record.get("record_type")
            if kind not in ("sample_started", "sample_scored") or record.get("round") is None:
                continue
            walk = (record["round"], record.get("role"), record["individual_id"])
            (started if kind == "sample_started" else scored)[walk].add(record["position"])
            # The origin's walks and a bench pass race nothing, so they send at the backend's ceiling.
            raced = record["round"] > 0 and record.get("role") != "bench"
            if kind == "sample_started" and raced:
                depth = max(depth, record["sample_lookahead"])
        wasted = [len(sent - scored[walk]) for walk, sent in started.items()]
        (cycle,) = (data_root(workspace) / PROJECTS_DIR).glob("*/campaigns/*/cycles/*")
        dashboard = json.loads((cycle / "dashboard.json").read_text(encoding="utf-8"))
        return cls(
            depth, max(wasted, default=0), sum(wasted), dashboard["sample_lookahead_discards"]
        )


def paused_at(workspace: Path) -> tuple[int, bool]:
    """The round its campaign's pause stood in, off its cycle's own ledger, and whether the pause
    lay inside it: entered and not yet closed when the pause was declared."""
    entered = closed = 0
    for record in leg_ledger(workspace):
        match record.get("record_type"):
            case "round_entered":
                entered = record["round"]
            case "round_closed":
                closed = record["round"]
            case "run_phase" if record["state"].get("stop_reason") == StopReason.PAUSED:
                return entered, closed != entered
    raise SystemExit(f"offline run: {workspace} declares no pause")


def said_leg(
    failed: dict[str, int], name: str, workspace: Path, leg: Future[int] | None, what: str
) -> None:
    """A leg of *name*'s saying its verdict on its log's last line that opens with its directory's
    name; one never spawned says nothing."""
    if leg is None:
        return
    rc = leg.result()
    failed[name] += bool(rc)
    log = workspace / "run.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    ended = f"FAILED (exit {rc}) -- {log}" if rc else ""
    ended = ended or next(ln for ln in reversed(lines) if ln.startswith(f"{workspace.name} "))
    print(f"{name}: {what}: {ended}")


def controlled_reading(home: Path, arm_names: Sequence[str]) -> str:
    beside, bare = (home / name for name in CONTROLLED)
    said = []
    for arm in (f"arm-{name}" for name in arm_names):
        # A reading: the foreign campaign's rows are in the version the head-to-head froze.
        same = BehaviourTrace.read(beside / arm).decisions == (
            BehaviourTrace.read(bare / arm).decisions
        )
        said.append(f"{arm}: decisions {'UNMOVED' if same else 'MOVED'} by the foreign campaign")
    lines = (beside / "run.log").read_text(encoding="utf-8").splitlines()
    return "\n".join([*said, *(line for line in lines if line.startswith(("arm ", "head-to-")))])


_PROVEN_HOMES = """
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from promptpotter.application.maintenance.landing import plan_landing
for at, root in enumerate(sys.argv[2:]):
    landing = plan_landing(Path(root), reset=False)
    unknown = list(landing.unknown)
    refused = [f"{u.path}: {u.why}" for u in (*landing.unloadable, *landing.caches, *landing.runs)]
    refused += [f"{u.path}: {u.why}" for u in landing.torn]
    refused += [one.said for one in landing.rows]
    if unknown or refused or landing.staged:
        print(json.dumps([at, (
            f"the run wrote what no data-root row names {unknown}, what its row's reader refuses "
            f"{refused}, or a shape a migration rewrites {[one.said for one in landing.staged]}"
        )]))
"""


def proven_homes(failed: dict[str, int], planned: Mapping[Path, str]) -> None:
    """Once every leg ran green, a landing's plan over what each wrote (*planned*: a projects root
    and the optimizer its leg ran): every file named by a row, loading as it shapes, and nothing for
    a migration to stage or the landing to drop. One process of its own plans them all, as a landing
    does, so a state kind only an optimizer's import registers is refused here, its import paid once."""
    if any(failed.values()):
        return
    roots = list(planned)
    repo = str(Path(__file__).resolve().parents[1])
    proof = [sys.executable, "-c", _PROVEN_HOMES, repo, *map(long_path, roots)]
    proved = subprocess.run(proof, capture_output=True, text=True, check=False)
    if proved.returncode:
        raise SystemExit(f"offline run: the landing's own process failed: {proved.stderr.strip()}")
    for line in proved.stdout.splitlines():
        at, said = json.loads(line)
        failed[planned[roots[at]]] += 1
        print(f"{planned[roots[at]]}: a landing refuses {roots[at]}: {said}")


def read_children(
    home: Path,
    children: Mapping[str, Future[int]],
    warm: Mapping[str, tuple[Future[int], Future[int], Future[int], Future[int]]],
    beside_warm: Mapping[str, list[Callable[[], bool]]],
    pause_after: int,
    *,
    controlled: bool,
) -> dict[str, int]:
    """Per optimizer, its straight run's failed checks, its warm legs', and each leg's held beside
    them, where they ran."""
    failed = dict.fromkeys(children, 0)
    for name, child in children.items():
        workspace = home / name
        if rc := child.result():
            failed[name] += 1
            print(f"{name}: FAILED (exit {rc}) -- {workspace / 'run.log'}")
        elif not controlled:
            failed[name] += read_straight(name, workspace)
        if name in warm:
            failed[name] += read_warm(name, workspace, *warm[name], pause_after)
        failed[name] += sum(not held() for held in beside_warm.get(name, ()))
    return failed


def beside_warm(
    home: Path, spawn: Callable[..., Future[int]], names: Sequence[str]
) -> dict[str, list[Callable[[], bool]]]:
    """The legs each optimizer runs on `warm/`'s ruler beside it, each spawned and read by its
    check: a skip and its resumed twin, every adoption graded, potter's pause inside a bench pass."""
    warmly = (f"--hardness={REBASE_HARDNESS}",)
    skip = (*warmly, f"--skip-at={SKIP_AT}")
    checks: dict[str, list[Callable[[], bool]]] = {}
    for name in names:
        skipped = (
            spawn(name, home / name / SKIPPED, *skip),
            spawn(name, home / name / f"{SKIPPED}-{RESUMED}", *skip, "--skip-resumed"),
        )
        every = spawn(name, home / name / EVERY, *warmly, "--bench-trigger=every_adoption")
        checks[name] = [
            functools.partial(skipped_leg, name, home, *skipped),
            functools.partial(adopted_leg, name, home, every),
        ]
    if "potter" in checks:
        # Potter's warm line adopts past its origin, so its end pass grades a selection to pause in.
        benched = spawn("potter", home / "potter" / BENCHED, *warmly, "--pause-in-bench")
        checks["potter"].append(functools.partial(bench_paused_leg, home, benched))
    return checks


def run_child_leg(args: argparse.Namespace) -> None:
    if any(os.environ[k] != NOT_A_KEY for k in PROVIDER_KEYS):
        raise SystemExit("offline run: --child runs only under the env its parent builds")
    # Its outputs land in its directory, its run in the data root it was handed.
    workspace = Path.cwd()
    traced(workspace)
    if args.terminal:
        run_terminal(
            args.child,
            workspace,
            args.terminal,
            rounds=args.rounds,
            rows=args.rows,
            interrupts=args.interrupts,
        )
        return
    if args.child in CONTROLLED:
        asyncio.run(
            run_controlled(
                workspace,
                args.optimizer,
                rounds=args.rounds,
                rows=args.rows,
                foreign=args.child == CONTROLLED[0],
            )
        )
    elif args.rebase:
        asyncio.run(run_rebasing(workspace, rounds=args.rounds, rows=args.rows))
    elif args.gated:
        asyncio.run(run_gated(args.child, workspace, rows=args.rows))
    elif args.l4:
        asyncio.run(run_l4(workspace, rows=args.rows))
    else:
        one = run_one(
            args.child,
            workspace,
            rounds=args.rounds,
            rows=args.rows,
            pause_after=args.pause_after,
            pause_in_walk=args.pause_in_walk,
            bench_trigger=args.bench_trigger,
            diverge=args.diverge,
            crossover=args.crossover,
            heal=args.heal,
            wound=args.wound,
            hardness=args.hardness,
            press=args.press,
            arrival=args.arrival,
            charged=args.charged,
            skip_at=args.skip_at,
            skip_resumed=args.skip_resumed,
            origin_replicates=args.origin_replicates,
            pause_in_bench=args.pause_in_bench,
        )
        print(asyncio.run(one)[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--optimizer",
        action="append",
        help="default: every installed optimizer; under --controlled, the arms (potter and capo)",
    )
    ap.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="budget.rounds per campaign; the interrupted legs run one",
    )
    ap.add_argument("--rows", type=int, default=200, help="synthetic bank size")
    ap.add_argument("--digests", action="store_true", help="print the L4 identity digests")
    ap.add_argument(
        "--controlled",
        action="store_true",
        help="run two arms of one head-to-head beside a foreign campaign, and without it",
    )
    ap.add_argument(
        "--straight",
        action="store_true",
        help="each optimizer's straight run alone: no resumed, interrupted, gated or forked leg",
    )
    ap.add_argument(
        "--bench-trigger",
        choices=get_args(BenchTrigger),
        default="at_end",
        help="`manual` runs every campaign as the default config does, fails on a bench cell "
        "sent before the pass is asked for, then asks for it",
    )
    ap.add_argument(
        "--against",
        type=Path,
        help="an earlier run's home: print per optimizer which fields of its trace moved",
    )
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="print, per package, the function-body lines every leg's process ran",
    )
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--pause-after", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--pause-in-walk", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--diverge", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--rebase", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--gated", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--crossover", choices=CROSSED_HOSTS, help=argparse.SUPPRESS)
    ap.add_argument("--heal", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--wound", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--hardness", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--press", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--arrival", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--charged", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--skip-at", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--skip-resumed", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--origin-replicates", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--pause-in-bench", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument(
        "--l4",
        action="store_true",
        help="under --straight: an L4 campaign over inner runs, into potter's data root after its "
        "straight run (the browser walk's world)",
    )
    ap.add_argument("--terminal", choices=("new", "resume"), help=argparse.SUPPRESS)
    ap.add_argument("--interrupts", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.digests:
        print(json.dumps(digests(), indent=1))
        return 0
    if args.child:
        run_child_leg(args)
        return 0
    if args.l4 and not (args.straight and "potter" in (args.optimizer or ["potter"])):
        ap.error("--l4 nests into potter's straight run: pass it with --straight")

    asked = [f"--{k}={v}" for k, v in sorted(vars(args).items()) if v != ap.get_default(k)]
    run = " ".join([OFFLINE_RUN, *(a for a in asked if not a.startswith("--against="))])
    home = named_home()
    check_every_treatment()
    arm_names = args.optimizer or ["potter", "capo"]
    names = (
        list(CONTROLLED)
        if args.controlled
        else args.optimizer or sorted(optimizers.MANIFESTS.table)
    )
    kept = LastGreen.open()
    keys = {} if kept is None else optimizer_keys(kept, names, controlled=args.controlled)
    held = holds_run(home, run)
    green = still_green(home, keys) if held else set()
    for name in sorted(green):
        print(f"{name}: unchanged since green -- {home / name}")
    due = [name for name in names if name not in green]
    # Only a run under the default flags fingerprints the world, and only a controlled one runs no
    # optimizer alone.
    keying = not args.controlled and all(
        a.startswith(("--optimizer=", "--against=")) for a in asked
    )
    if not due:
        if args.against is not None:
            print("\n".join(moved_against(home, args.against, names)))
        return int(keying and behaviour_keys(home, names))
    stamp: dict[str, object] = {"offline": True, "note": LABEL, "run": run, "workspaces": names}
    queue_behind_siblings("offline_run")
    prepared_home(home, stamp, due, held=held)
    end_children_with_this_process()
    # One workspace each: a shared archive and δ ruler let one optimizer's run move another's.
    child_args = ["--rows", str(args.rows), "--bench-trigger", args.bench_trigger]
    if args.controlled:
        child_args += [f"--optimizer={name}" for name in arm_names]
    t0 = time.monotonic()

    # The first optimizer's workspace holds the CLI's legs, and potter's its own: they run with it.
    first = names[0]
    legs = not (args.controlled or args.straight)
    firsts = legs and first in due
    pause_after = min(1, args.rounds - 1)
    # Potter's own legs need rounds past the one they pause at.
    potter_legs = legs and "potter" in due and pause_after > 0
    # Each script wounds a call of the first two rounds.
    wounded = [name for name in due if name in WOUNDS] if legs and args.rounds > 1 else []
    tasks = (
        8 * len(due) + (len(INTERRUPTED) + 3) * firsts + 8 * potter_legs + len(wounded)
        if legs
        else len(due)
    )
    pool = ThreadPoolExecutor(RamBudget.read().offline_run_children(tasks))

    def run_child(
        name: str, workspace: Path, log: str, rounds: int, *extra: str, root: Path
    ) -> int:
        child = [sys.executable, __file__, "--child", name, f"--rounds={rounds}", *child_args]
        with (workspace / log).open("w", encoding="utf-8") as out:
            return subprocess.run(
                [*child, *extra],
                cwd=workspace,
                env=child_env(root),
                stdout=out,
                stderr=subprocess.STDOUT,
            ).returncode

    # Each leg's projects root, by the optimizer it runs, for one landing plan once all have run.
    planned: dict[Path, str] = {}

    def spawn(name: str, workspace: Path, *extra: str, into: Path | None = None) -> Future[int]:
        """*into* is the leg whose data root it runs in: its own, unless named."""
        workspace.mkdir()
        root = data_root(into or workspace)
        planned.setdefault(root / PROJECTS_DIR, name)
        return pool.submit(run_child, name, workspace, "run.log", args.rounds, *extra, root=root)

    def terminal(workspace: Path, verb: str, interrupts: int) -> int:
        flags = (f"--terminal={verb}", f"--interrupts={interrupts}")
        return run_child(
            first, workspace, f"{verb}.log", TERMINAL_ROUNDS, *flags, root=data_root(workspace)
        )

    children = {name: spawn(name, home / name) for name in due}
    stopped = (
        {
            count: pool.submit(interrupted, terminal, home / first / leg, count)
            for count, leg in INTERRUPTED.items()
        }
        if firsts
        else {}
    )
    # A resume checks divergence only once a round past the origin closed; one leg stands for all.
    diverged = (
        spawn(first, home / first / DIVERGED, f"--pause-after={pause_after}", "--diverge")
        if firsts and pause_after
        else None
    )
    gating = spawn(first, home / first / GATED, "--gated") if firsts else None
    replicating = (
        spawn(first, home / first / REPLICATED, "--rounds=1", f"--origin-replicates={REPLICATES}")
        if firsts
        else None
    )
    # Only potter's L2 proposes a fork.
    rebasing = spawn("potter", home / "potter" / REBASED, "--rebase") if potter_legs else None
    # Only potter's nodes answer structured, so only its calls climb the client's repair ladder.
    healing = spawn("potter", home / "potter" / HEALED, "--heal") if potter_legs else None
    wounding = {name: spawn(name, home / name / WOUNDED, "--wound") for name in wounded}
    # The L4 fixture's outer and inner optimizer are potter's.
    nesting = spawn("potter", home / "potter" / L4, "--l4") if potter_legs else None
    warmly = (f"--hardness={REBASE_HARDNESS}",)
    # Its pool is read off the closed rounds, so a resume must hand it the pool a straight run had;
    # only potter's race rolls, so only its calls are read as they return, pressed so several are out.
    l1_host, l2_host = (f"--crossover={host}" for host in CROSSED_HOSTS)
    arrival = (*warmly, "--arrival", "--press", f"--pause-in-walk={pause_after + 1}")
    paired = (
        None
        if not potter_legs
        else (
            (
                spawn("potter", home / "potter" / CROSSED, l1_host, "--charged"),
                spawn(
                    "potter",
                    home / "potter" / f"{CROSSED}-{RESUMED}",
                    l1_host,
                    "--charged",
                    f"--pause-after={pause_after}",
                ),
                spawn("potter", home / "potter" / f"{CROSSED}-l2", l2_host),
            ),
            spawn("potter", home / "potter" / ARRIVED, *arrival),
        )
    )
    warm = {
        name: (
            spawn(name, home / name / WARM, *warmly),
            spawn(name, home / name / PRESSED, *warmly, "--press"),
            spawn(name, home / name / RESUMED, *warmly, f"--pause-in-walk={pause_after + 1}"),
            spawn(
                name,
                home / name / PRESSED_RESUMED,
                *warmly,
                "--press",
                f"--pause-in-walk={pause_after + 1}",
            ),
        )
        for name in (due if legs else ())
    }
    failed = read_children(
        home,
        children,
        warm,
        beside_warm(home, spawn, due if legs else []),
        pause_after,
        controlled=args.controlled,
    )
    # After the straight run, never beside it: two processes would land one root at entry.
    if args.l4 and failed.get("potter") == 0:
        nesting = spawn("potter", home / "potter" / L4, "--l4", into=home / "potter")
    for count, leg in stopped.items():
        ok, said = leg.result()
        failed[first] += not ok
        print(f"{first}: `new` under {count} SIGINT: {said if ok else 'FAILED -- ' + said}")
    if diverged is not None:
        rc = diverged.result()
        failed[first] += bool(rc)
        log = home / first / DIVERGED / "run.log"
        print(
            f"{first}: a resume diverged at run init "
            f"{f'FAILED (exit {rc}) -- {log}' if rc else 'forked, its parent REBASED'}"
        )
    said_leg(failed, first, home / first / GATED, gating, "the origin gate")
    failed[first] += not replicated_leg(first, home / first / REPLICATED, replicating)
    said_leg(failed, "potter", home / "potter" / REBASED, rebasing, "a line forking at every L2")
    if paired is not None:
        crossing, arriving = paired
        failed["potter"] += not crossed_legs(home / "potter", crossing, pause_after)
        failed["potter"] += not arrived_leg(home / "potter" / ARRIVED, arriving, pause_after)
    if healing is not None:
        failed["potter"] += not healed_leg(home / "potter", healing)
    for name, wound in wounding.items():
        failed[name] += not wounded_leg(name, home / name, wound)
    said_leg(failed, "potter", home / "potter" / L4, nesting, "an L4 campaign over inner runs")
    proven_homes(failed, planned)
    if args.against is not None:
        print("\n".join(moved_against(home, args.against, names)))
    if args.controlled and not any(failed.values()):
        print(controlled_reading(home, arm_names))
    print(f"wall clock {time.monotonic() - t0:.0f}s")
    if args.coverage:
        print("\n".join(coverage_by_package(home)))
    # Under the home its flags stamped, and only where the sources read the same as before it.
    if kept is not None:
        now = optimizer_keys(kept, due, controlled=args.controlled)
        for name in (n for n in due if not failed[n] and keys[n] == now[n]):
            (home / name / GREEN).write_text(keys[name], "utf-8")
    if any(failed.values()):
        return 1
    return int(keying and behaviour_keys(home, names))


if __name__ == "__main__":
    sys.exit(main())
