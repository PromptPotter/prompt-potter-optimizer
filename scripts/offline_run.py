"""The one supported OFFLINE run: a real campaign per installed optimizer on ``justlogic/d234``,
every network edge answered in-process, zero spend. What it is for, what it pins and how to read
it: ``docs/developer/offline-run.md``.

    PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py [--optimizer NAME ...]
    python scripts/offline_run.py --digests
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import dataclasses
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

import httpx
import yaml
from last_green import OFFLINE_RUN, OFFLINE_RUN_READS, LastGreen, imported
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
)
from processes import (
    REPO,
    RamBudget,
    end_children_with_this_process,
    long_path,
    queue_behind_siblings,
)

import promptpotter
from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_operator_fork
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.measuring import grade_bench
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import host_dispatcher, run_campaign, run_head_to_head
from promptpotter.application.evidence.head_to_head import GuardState
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.maintenance.landing import plan_landing
from promptpotter.application.optimizer_manifest import (
    manifest_directories,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.runner.inner.tasks import measurement_modules
from promptpotter.config.paths import (
    PROJECTS_DIR,
    default_projects_root,
    install_datasets_root,
    optimizer_manifest_path,
    user_data_root,
)
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import Settings
from promptpotter.domain.bench import BenchScore, BenchTrigger
from promptpotter.domain.campaign import Compare
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    OptimizerChoice,
)
from promptpotter.domain.command_events import WORKSPACE_SUBJECT, CommandRecord, subject_key
from promptpotter.domain.command_kinds import (
    ArmRequest,
    ForkCyclePayload,
    GradeBenchPayload,
    HeadToHeadRequest,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.phases import RunPhase, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.pipeline_manifest import prompt_key
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.ruler import RulerOwn
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import CycleSeed, OriginGatePass
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import producer_attached
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    list_populations,
    load_population,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_answers
from promptpotter.infrastructure.store.dataset_access import install_dataset, resolve_dataset
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.layout import dataset_id_prefix, inner_sandboxes_dir
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.identity import default_identity
from promptpotter.shared.measurement_context import instrument_depth
from promptpotter.shared.run_scope import run_scope, scoped

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import CycleResult
    from promptpotter.infrastructure.store.stores import Stores

DATASET = "justlogic/d234"
STAMP = "offline-run.json"
TRACE = "trace.json"
# Set in a leg's env under `--coverage`: the directory its process files the lines it ran into.
COVERAGE = "PROMPTPOTTER_OFFLINE_COVERAGE"
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


def data_root(leg: Path) -> Path:
    """A leg's outputs and the legs nested in it sit beside its run's data root, never in it: every
    process lands its root at entry, and a file no row names refuses the landing."""
    return leg / "data"


# The Ctrl+C contract is the pause and the resume, not the search: one round ends the cycle soonest.
TERMINAL_ROUNDS = 1
RESUME_APPENDS = ("phases", "run_phases", "arms", "rounds_entered", "standings")
# Which of a round's cells a launch paid and which it replayed, by where each count sits: a
# reading side's `bought`, a cell row's `cached`, a fold's `cached` and `tokens` — never a key of
# that name elsewhere, such as the fitness term `evaluators.tokens`.
READING_SIDES, SIDE_SPLIT = ("a", "b"), ("bought",)
CELL_ROWS, ROW_SPLIT = ("cells", "overlap_results"), ("cached",)
FOLDS, FOLD_SPLIT = "folds", ("cached", "tokens")
LABEL = "OFFLINE — fake optimizer LLM and fake backend; every number is synthetic"

BENCH: dict[str, Any] = {
    "sp_budget_origin": 14,
    "dataset_split": {"bench": 10, "demo": 10},
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
FRAMING = ("task_context", "TASK CONTEXT:\n")
TALLY = ("answer_distribution", "ANSWER DISTRIBUTION (")


def cited_panel(config: CampaignConfig) -> tuple[str, str]:
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

    def fill(self, s: dict[str, Any], path: tuple[str, ...]) -> Any:
        s = self.resolve(s)
        if "const" in s:
            return s["const"]
        if "enum" in s:
            return s["enum"][0]
        for key in ("anyOf", "oneOf"):
            if key in s:
                opts = [self.resolve(o) for o in s[key]]
                non_null = [o for o in opts if o.get("type") != "null"]
                return self.fill((non_null or opts)[0], path)
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
    slot = filler.resolve(props["recombinations"])
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
    """Empty for the run's own call; an L4 cell's inner campaign by its seat's seed and the arm
    laid into its overlay, never by its id, which hashes the code."""
    if not instrument_depth():
        return ""
    scope = run_scope()
    assert scope.optimizer is not None and scope.determinism is not None, "an inner run binds both"
    held = json.dumps(
        [scope.determinism.seed, scope.optimizer.overlay], sort_keys=True, default=str
    )
    return hashlib.sha256(held.encode()).hexdigest()[:8]


class ByCallIdentity:
    """The offline run's `FakeLLM` policy: an answer follows the CALL's identity, never the
    prompt's wording nor the arrival order. *rebasing*, every L2 fire proposes a fork. *crossing*,
    every variant rewrites a second field, so any two arms differ on two loci a crossover swaps."""

    def __init__(
        self,
        capture: Path,
        templates: dict[str, str],
        *,
        cites: tuple[str, str],
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
        self.sends: Counter[str] = Counter()
        # A structured node's call a heal left open, by asker: its next send retries it.
        self.healing: dict[tuple[str, str], tuple[str, int]] = {}

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
        script = self.heals.get(key, ())
        said: Said = completion(content, model=body["model"], id=f"offline-{node}-{call}")
        shown: Any = content
        if attempt < len(script):
            said = invalid(script[attempt], content, model=body["model"])
            shown = said.json() if isinstance(said, httpx.Response) else said
            if script[attempt] is not Heal.REFUSED:
                self.healing[asker, node] = (call, turn)
        (self.capture / f"{key}{f'~{attempt}' if attempt else ''}.json").write_text(
            json.dumps(
                {"request": body, "response": shown}, indent=1, sort_keys=True, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        return said

    def unhealed(self) -> list[str]:
        """Each scripted call not sent once per invalid answer plus the send that healed it; a
        refusal heals on its own send."""
        return [
            key
            for key, script in self.heals.items()
            if self.sends[key] != len(script) + (script[-1] is not Heal.REFUSED)
        ]

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
        grounding = filler.resolve(item["properties"]["evidence_grounding"])
        cited = filler.resolve(grounding["properties"]["field"])["enum"]
        # A citation quotes the panel it names, at an offset that is the call's, not the text's.
        field, heading = self.cites
        assert field in cited and heading in prompt, f"offline run: no {field} panel to cite"
        start = prompt.index(heading) + len(heading) + draw("cite", generation, i) % 20
        v["evidence_grounding"] = {"field": field, "citation": prompt[start : start + 40]}
        # Every prompt node the wire offers an `instruction` on: a model sees no "first" node.
        # An L4 outer edits the inner optimizer's fields as its pipeline params; there a short
        # instruction guts its contracts (`validators.py::l1_prompt_field_not_gutted`).
        props = item["properties"]
        slot, edited = (
            ("prompt_fields_updates", "instruction")
            if "prompt_fields_updates" in props
            else ("pipeline_overlay", "thinking_style")
        )
        offered = props[slot]["properties"]
        second = ("thinking_style",) if self.crossing else ()
        v[slot] = {
            node: {
                field: _variant(
                    f"g{generation}v{i}{node}{tag}",
                    random.Random(f"l1:{generation}:{i}:{node}{tag and f':{tag}'}"),
                )
                for field, tag in ((edited, ""), *((f, f) for f in second))
                if field in fields["properties"]
            }
            for node, fields in offered.items()
            if edited in fields["properties"]
        }
        # As a model would: one cluster line of the CRITIQUE panel, else the first rows any panel marks.
        clusters = [
            ids for line in prompt.splitlines() if " rows: " in line and (ids := _marks(line))
        ]
        v["targets_rows"] = (clusters[i % len(clusters)] if clusters else _marks(prompt))[:2]
        v["changes_description"] = (
            f"Rewrite the instruction (generation {generation}, variant {i})."
        )
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
    templated = opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
    # Draws follow the campaign's id unless a clamp seeds them, and every run mints an id.
    opt.update(
        budget={"usd": 1000.0, "rounds": rounds},
        origin_gate="off",
        optimizer=optimizer,
        determinism={"seed": 0},
    )
    if optimizer != templated:
        roster = select_optimizer(OptimizerChoice(optimizer=optimizer)).manifest.nodes
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
        if r.get("record_type") == "run_phase" and r.get("stop_reason") == StopReason.REBASED
    ]
    if [r["spend"] is not None for r in rebased] != [True]:
        raise SystemExit(f"offline run: the parent ended on {rebased}, not one REBASED with spend")
    if producer_attached(layout.cycle_dir):
        raise SystemExit("offline run: the fork left its parent's producer lock held")
    last = layout.readout.read_text(encoding="utf-8").rstrip().splitlines()[-1]
    if fork.cycle_id not in last:
        raise SystemExit(f"offline run: the parent's readout ends {last!r}, naming no fork")
    if not closed_a_round(stores, fork):
        raise SystemExit("offline run: the fork closed no round of its own")
    print(f"diverged at run init: {parent.cycle_id} REBASED onto {fork.cycle_id}, which ran on")


async def forked_twice(stores: Stores, fork: CycleHop, rounds: int) -> None:
    """An operator fork off the divergence fork, then one off that: one campaign, four courses."""
    # Each cut leaves the fork a round of its own to close under the campaign's rounds cap.
    for from_round in (rounds - 2, rounds - 1):
        cycle_id = cut_operator_fork(
            stores=stores,
            hop=fork,
            from_round=from_round,
            seed=CycleSeed(),
            keep_rounds=True,
        ).take(stores)
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
    ) -> Harness:
        (out / "requests").mkdir(parents=True)
        llm = ByCallIdentity(
            out / "requests", templates, cites=cited_panel(config), crossing=crossing
        )
        backend = FakeBackend(samples)
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
) -> tuple[Path, str]:
    """The archive is the workspace's, never *out*'s: campaigns in one process share it.
    *pause_after* pauses at that round's boundary; *pause_in_walk* at the first cell that round
    measures, its walk banked, so the resume replays the walk and runs no proposer."""
    out = workspace if out is None else out
    config = campaign_config(optimizer, rounds, bench_trigger=bench_trigger, crossover=crossover)
    if not framing:
        config = config.model_copy(update={"task_framing": "off"})
    samples = synthetic_rows(rows)
    harness = Harness.installed(
        out, config, text_templates(config), samples, crossing=crossover is not None
    )
    if heal:
        harness.llm.heals = HEALS

    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    if pause_in_walk is not None:
        harness.backend.before_answer = pausing_in_walk(stores, pause_in_walk)
    session, result = await run_campaign(
        DATASET,
        config,
        stores=stores,
        readout_sink=print,
        budget=Budget(),
        step_rounds=pause_after,
        mode=RunMode(),
    )
    harness.backend.before_answer = None
    if pause_after is not None or pause_in_walk is not None:
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
                mode=RunMode(fork_on_divergence=diverge),
            )
            if diverge:
                forked_at_init(stores, paused, session.hop)
                await forked_twice(stores, session.hop, rounds)
    cycle = await banked(
        stores, session, result, harness, out, name=optimizer, bench_trigger=bench_trigger
    )
    if unhealed := harness.llm.unhealed():
        raise SystemExit(f"offline run: the scripted calls {unhealed} did not heal as scripted")
    return cycle, session.campaign_id


def pausing_in_walk(stores: Stores, round_num: int) -> Callable[[], Awaitable[None]]:
    """The pause an operator presses as the first cell is answered of a round from *round_num* on
    whose walk banked: a cell measured inside a walk, or replayed, presses nothing."""
    asked = False

    async def pause() -> None:
        nonlocal asked
        at = current_round()
        if asked or at is None or at < round_num:
            return
        (campaign,) = stores.campaigns.list_campaigns(lifecycle="all")
        hop = campaign.root_hop
        if stores.campaigns.round_proposals(hop, at) is None:
            return
        asked = True
        payload = PauseCyclePayload(
            campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="offline: inside a walk"
        )
        await host_dispatcher(stores).dispatch(CommandCall(payload))

    return pause


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
        for node in session.pipeline_schema.declared_nodes
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
        call = CommandCall(GradeBenchPayload(campaign_id=session.hop.campaign_id))
        bench = (await host_dispatcher(stores).answer(grade_bench, call)).result
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
    requests = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(harness.llm.capture.iterdir())
    }
    BehaviourTrace(world=WORLD, decisions=decisions, requests=requests).write(out)
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
    the hash of each optimizer request it sent, in the world `offline_world.py::WORLD` names."""

    world: str
    decisions: dict[str, Any]
    requests: dict[str, str]

    @classmethod
    def read(cls, workspace: Path) -> BehaviourTrace:
        return cls(**_kept(json.loads((workspace / TRACE).read_text(encoding="utf-8"))))

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        dumped = json.dumps(dataclasses.asdict(self), indent=1, sort_keys=True, ensure_ascii=False)
        (out / TRACE).write_text(dumped + "\n", encoding="utf-8")

    def moved(self, earlier: BehaviourTrace) -> list[str]:
        sent = [
            n
            for n in self.requests.keys() | earlier.requests.keys()
            if self.requests.get(n) != earlier.requests.get(n)
        ]
        return [
            *(["world"] * (self.world != earlier.world)),
            *_moved(self.decisions, earlier.decisions, "decisions", 2),
            *([f"requests ({len(sent)} of {len(self.requests)})"] if sent else []),
        ]


async def run_rebasing(workspace: Path, *, rounds: int, rows: int) -> None:
    """Potter resumed with `--from` under an L2 proposing the same fork at every fire: only the
    first changes something, and the line's forks spend one round budget between them."""
    # L2 fires every round, so the fork owes nothing to whether the fake replies happen to stall;
    # no arm stops short, so the ruler a rewind needs spans the rows whatever PoBB would cut.
    config = campaign_config(
        "potter",
        rounds,
        bench_trigger="manual",
        knobs={"escalation": EVERY_L2, "pobb": {"epsilon_stop": False, "lock_in": False}},
    )
    samples = synthetic_rows(rows)
    (workspace / "requests").mkdir()
    llm = ByCallIdentity(workspace / "requests", text_templates(config), cites=cited_panel(config))
    # A rewind lands on a round holding a θ level, which only a ruler spanning the rows gives.
    install_network(Router(FakeLLM(llm), FakeBackend(samples, hardness=REBASE_HARDNESS)))
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    # The first campaign banks the rows the second's ruler is fit on; the second pauses at 1.
    for step_rounds in (None, 1):
        session, _ = await run_campaign(
            DATASET,
            config,
            stores=stores,
            readout_sink=print,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(),
        )
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
    print(f"rebased {len(forks)} times, {spent} of {rounds} rounds spent, line ended {final}")


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
        return [d.inputs_ref.by for d in decided if isinstance(d.inputs_ref, OriginGatePass)]

    for fixed in (False, True):
        backend.down, backend.sent = True, []
        session, result = await run_campaign(
            DATASET,
            config,
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
    # One seat holds out no row, so no bench is ever asked for.
    raw.update(bench_trigger="at_end")
    opt = raw["optimization"]
    opt.update(budget={**opt["budget"], "usd": 1000.0}, determinism={"seed": 0})
    config = CampaignConfig.model_validate(raw)
    harness = Harness.installed(workspace, config, text_templates(config), samples, crossing=False)
    session, result = await run_campaign(
        L4_DATASET, config, stores=stores, readout_sink=print, budget=Budget(), mode=RunMode()
    )
    await banked(stores, session, result, harness, workspace, name=L4, bench_trigger="at_end")
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
    campaign_runner.main()


def _commands(stores: Stores, subject: str) -> dict[str, list[dict[str, Any]]]:
    """The payloads of what *subject* was commanded and applied, by kind, off the account's log."""
    book = stores.commands.book()
    out: dict[str, list[dict[str, Any]]] = {}
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
    projects = Path(long_path(data_root(workspace))) / PROJECTS_DIR
    stores = build_stores(default_identity(), projects_root=projects)
    (campaign_id,) = stores.campaigns.list_campaign_ids()
    (cycle,) = stores.campaigns.campaign_cycle_dirs(campaign_id)
    hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle.name)
    stopped = stores.run_state(hop)
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in stores.jobs_dir.glob("*.json")]
    if stopped.producer.attached or any(j["released_at"] is None for j in jobs):
        return False, f"`new` exited 130 still holding its cycle or its slot ({stopped})"
    if interrupts == 1 and stopped.run_phase is not RunPhase.PAUSED:
        return False, f"one SIGINT left the cycle {stopped.run_phase}, not paused"
    (mint,) = _commands(stores, WORKSPACE_SUBJECT)["mint-campaign"]
    if not mint["campaign_config"]:
        return False, f"the mint-campaign record carries no config: {sorted(mint)}"
    if rc := spawn_terminal(workspace, "resume", 0):
        return False, f"`resume` after {interrupts} SIGINT exited {rc}"
    ended = stores.run_state(hop)
    started = _commands(stores, subject_key(stores.subject(hop))).get("start-run", [])
    if ended.run_phase is not RunPhase.TERMINAL or len(started) != 1:
        return False, f"`resume` left the cycle {ended.run_phase} on {len(started)} start-run"
    return True, f"exit 130 {stopped.run_phase}, slot released; `resume` ran it to its end"


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
        CampaignConfig.model_validate(shared),
        request,
        stores=stores,
        mode=RunMode(),
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
            ForkCyclePayload(campaign_id=cid, cycle_id=cycle.name, round=1, keep_rounds=True)
        )
    )
    launched = forked.result
    assert isinstance(launched, Launched) and launched.held is not None
    await launched.held.run_inline(mode=RunMode(), readout_sink=print)
    after = subject_evidence(stores, [Subject(c) for c in subjects]).head_to_head
    assert after is not None
    row = next(r for r in after.rows if r.campaign_id == cid)
    if row.guard.state is not GuardState.UNCONTROLLED:
        raise SystemExit(f"offline run: arm {name} forked and still reads {row.guard.state}")
    print(f"arm {name}: forked, and left its comparison: {row.guard.sentence}")


def _graded(bench: dict[str, Any] | None) -> bool:
    return bench is not None and bench["status"]["state"] == "read"


# A ledger is read as dicts with `.get`: a dump taken before a reshape must load after it.
FLOAT_DP = 6
DROP_KEYS = {
    "timestamp", "created_at", "updated_at", "started_at", "finished_at", "at_offset", "offset",
    "wall_clock", "duration_s", "elapsed_s", "call_id", "answer", "answers", "sp_hash", "content_hash",
    "prompt_fields_id", "ruler_id", "anchor_id", "prompt_hashes",
    "optimizer_prompt_hashes", "version", "sample_key",
    "backend_url", "latency", "latency_ms", "total_time", "step_timings",
    # A fact about the holdout, which a campaign beside this one moves and no decision reads.
    "reads_before",
}  # fmt: skip
# A digest of keys dropped above, by the key that holds it: a re-key moves it, behaviour never does.
DROP_UNDER: dict[str, frozenset[str]] = {"cell_set": frozenset({"id"})}


def _dropped(key: str, under: str) -> bool:
    return key in DROP_KEYS or key in DROP_UNDER.get(under, frozenset())


def _kept(o: Any, under: str = "") -> Any:
    """*o* less every key a trace never compares, so a base written before a rule compares under it."""
    if isinstance(o, dict):
        return {k: _kept(v, k) for k, v in o.items() if not _dropped(k, under)}
    if isinstance(o, list):
        return [_kept(v, under) for v in o]
    return o


_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMBEDDED_HEX32 = re.compile(r"[0-9a-f]{32}")
_SHORT_HEX = re.compile(r"\b[0-9a-f]{6,12}\b")
_CYCLE_ID = re.compile(r"cycle_[0-9a-f]{8,}")
_FORK_ID = re.compile(r"cycle_[0-9a-f]{8,}_fork_[0-9a-f]{8}")
_CAMPAIGN_ID = re.compile(
    f"(?:{'|'.join(re.escape(dataset_id_prefix(d)) for d in (DATASET, L4_DATASET))})__[0-9a-f]{{6}}"
)
_SESSION_ID = re.compile(r"\bs_[0-9a-f]{8}\b")
_WIN_PATH = re.compile(r"(?:\\\\\?\\)?[A-Za-z]:[\\/][^\s'\"<>|]*")


class Canon:
    def __init__(self, ids: dict[str, str], forks: dict[str, str]) -> None:
        self.ids = ids
        self.forks = forks
        # Prose names a candidate by an id prefix ("crossover 2dda4b+917e53", "parent c27b1862").
        self.short = {k[:n]: v for k, v in ids.items() for n in range(6, 13)}

    def s(self, v: str) -> str:
        if v in self.ids:
            return self.ids[v]
        if _HEX32.match(v) or _UUID.match(v):
            return "<id>"
        v = _EMBEDDED_HEX32.sub(lambda m: self.ids.get(m.group(0), "<id>"), v)
        v = _SHORT_HEX.sub(lambda m: self.short.get(m.group(0), m.group(0)), v)
        v = _CAMPAIGN_ID.sub("<campaign>", v)
        v = _FORK_ID.sub(lambda m: self.forks[m.group(0)], v)
        v = _CYCLE_ID.sub("<cycle>", v)
        v = _SESSION_ID.sub("<session>", v)
        return _WIN_PATH.sub("<path>", v)

    def __call__(self, o: Any, under: str = "") -> Any:
        if isinstance(o, dict):
            return {
                self.s(str(k)): self(v, str(k)) for k, v in o.items() if not _dropped(str(k), under)
            }
        if isinstance(o, list | tuple):
            return [self(v, under) for v in o]
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
        for row in d.get("scoreboard") or []:
            arm = row["reading"]["arm"]
            if arm["label"] != "parent":
                ids.setdefault(arm["key"], arm["label"])
    for r in _proposed(ledger):
        lineage = r["state"]["individual"].get("lineage") or {}
        for pid in lineage.get("parent_ids") or []:
            ids.setdefault(pid, f"parent@R{r.get('round')}")
    for d in rounds:
        for row in d.get("scoreboard") or []:
            ids.setdefault(row["reading"]["arm"]["key"], f"parent@R{d.get('round')}")
    return ids


def _fork_labels(cycle: Path) -> dict[str, str]:
    """Each fork of *cycle*'s campaign by the rounds its line was cut at: its id hashes the clock."""
    parents: dict[str, tuple[str | None, int | None]] = {}
    for minted in cycle.parent.iterdir():
        index = read_cycle_index(minted, moment=None)
        assert index is not None, f"{minted.name} ran without minting its cycle"
        parents[minted.name] = (index.parent_cycle_id, index.cut_round)

    def label(cycle_id: str) -> str:
        parent, cut = parents[cycle_id]
        return "<cycle>" if parent is None else f"{label(parent)}/R{cut}"

    labels = {c: label(c) for c, (parent, _) in parents.items() if parent is not None}
    if len(set(labels.values())) < len(labels):
        raise SystemExit(f"offline run: two forks of one parent cut at one round: {labels}")
    return labels


def _cells(rows: Any) -> list[dict[str, Any]]:
    keys = ("position", "predicted", "ground_truth", "fitness", "objective", "cached")
    out = [{k: r.get(k) for k in (*keys, "error_category")} for r in rows or []]
    return sorted(out, key=lambda c: str(c["position"]))


ARM_KEYS = (
    "state", "role", "reason", "locked_in", "slot", "overlay", "resolved_params",
    "expected_samples", "validation_failures",
)  # fmt: skip
ROUND_KEYS = ("round", "overlap", "overlap_results", "optimizer_state")
ELECTION_KEYS = (
    "selected", "leading", "ended_on", "electable", "fit", "elects_on", "verdict_reason",
)  # fmt: skip


def _round(d: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {k: d.get(k) for k in ROUND_KEYS}
    election = d.get("election") or {}
    out["election"] = {k: election.get(k) for k in ELECTION_KEYS}
    health = d.get("health") or {}
    out["health"] = {k: health.get(k) for k in ("grade", "cause")}
    readings = [r["reading"] for r in d.get("scoreboard") or []]
    out["arms"] = {
        ids.get(key, key): {
            **{k: arm.get(k) for k in ARM_KEYS},
            "prompts": (arm.get("individual") or {}).get("prompts"),
        }
        for key, arm in (d.get("arms") or {}).items()
    }
    out["folds"] = {ids.get(k, k): v for k, v in (d.get("folds") or {}).items()}
    out["scoreboard_order"] = [ids.get(r["arm"]["key"], r["arm"]["label"]) for r in readings]
    out["cells"] = {ids.get(k, k): _cells(v) for k, v in (d.get("cells") or {}).items()}
    return out


LEDGER_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "decision": ("decisions", ("round", "kind", "outcome", "inputs_ref")),
    "election": ("elections", ("round", "selected", "ended_on", "fit")),
    "arm": ("arms", ("round", "arm", "state")),
    "phase": ("phases", ("round", "phase", "event")),
    "round_entered": ("rounds_entered", ("round",)),
    "round_closed": ("rounds_closed", ("round", "election")),
    "run_phase": ("run_phases", ("run_phase", "stop_reason")),
    "llm_call": ("optimizer_calls", ("round", "node", "candidate_idx")),
    "round_warning": ("round_warnings", ("round", "kind", "severity", "detail")),
    "race_standing": (
        "standings",
        ("round", "member", "current", "n_samples", "p_best", "paired_breakdown"),
    ),
}


def _as_sets(o: Any) -> Any:
    # A decision lists the arms it raced in the order they ARRIVED (CAPO's `raced_against`).
    if isinstance(o, dict):
        return {k: _as_sets(v) for k, v in o.items()}
    if isinstance(o, list) and all(isinstance(v, str) for v in o):
        return sorted(o)
    return [_as_sets(v) for v in o] if isinstance(o, list) else o


def _ledger(ledger: list[dict[str, Any]], canon: Canon) -> dict[str, Any]:
    out: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in LEDGER_KEYS.values()}
    out["level_series"] = []
    for r in ledger:
        if (kind := r.get("record_type")) not in LEDGER_KEYS:
            continue
        name, keys = LEDGER_KEYS[kind]
        entry = {k: r.get(k) for k in keys}
        if kind == "arm":
            state = r["state"]
            entry["state"] = {"state": state["state"], "slot": state.get("slot")}
        if kind == "round_closed":
            series = r["level_series"]
            levels = {
                rnd: None if lv is None else {k: lv[k] for k in ("theta", "se", "cells", "caveat")}
                for rnd, lv in series["levels"].items()
            }
            out["level_series"].append(
                canon(
                    {
                        "round": r["round"],
                        "level_series": {"caveat": series["caveat"], "levels": levels},
                    }
                )
            )
        if kind == "llm_call":
            entry["repairs"] = len((r.get("payload") or {}).get("schema_repair_errors") or [])
        out[name].append(_as_sets(canon(entry)) if kind == "decision" else canon(entry))
    # Concurrent arms append in arrival order; the SET per round is what a comparison reads.
    for name in ("decisions", "round_warnings", "optimizer_calls", "standings"):
        out[name].sort(key=lambda x: (x.get("round") or 0, json.dumps(x, sort_keys=True)))
    out["arms"].sort(key=lambda x: (x.get("round") or 0, json.dumps(x, sort_keys=True)))
    return out


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def extract(cycle: Path, bench: BenchScore | None) -> dict[str, Any]:
    rounds = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((cycle / "rounds").glob("*.json"))
    ]
    ledger = _jsonl(cycle / ".runtime" / "ledger.jsonl")
    folded = read_cycle_index(cycle, moment=None)
    assert folded is not None, f"{cycle.name} ran without minting its cycle"
    index = folded.model_dump(mode="json")
    ids = _id_map(ledger, rounds)
    canon = Canon(ids, _fork_labels(cycle))
    final = index["final"] or {}
    run = {
        "stop_reason": index["stop_reason"],
        "n_rounds": len(index["rounds"]),
        **{
            k: final.get(k)
            for k in (
                "rounds_to_separable", "rounds_to_improved", "rounds_to_ceiling",
                "origin_composite_fitness", "mode", "result_prompt_fields",
                "result_pipeline_params",
            )
        },
        "bench": None if bench is None else bench.model_dump(mode="json"),
        "round_index": index["rounds"],
    }  # fmt: skip
    return {
        "run": canon(run),
        "rounds": [canon(_round(d, ids)) for d in rounds],
        "ledger": _ledger(ledger, canon),
    }


def digests() -> dict[str, str]:
    complete_registries()
    out = {"estimator": module_source_digest(*measurement_modules())}
    for name in sorted(optimizers.MANIFESTS.table):
        selected = select_optimizer(OptimizerChoice(optimizer=name))
        out[f"treatment:{name}"] = selected.treatment(
            frozenset(Presumption), PackShelf(account=None)
        ).digest
    return out


def traced(out: Path) -> None:
    """Files into *out* at exit each package line a function of this process ran, as PEP 669
    reports it: once a line, then disabled, so the run pays for each line once."""
    package = str(Path(promptpotter.__file__).parent)
    ran: dict[str, set[int]] = defaultdict(set)
    monitoring = sys.monitoring

    def line(code: types.CodeType, at: int) -> object:
        if code.co_filename.startswith(package):
            ran[code.co_filename].add(at)
        return monitoring.DISABLE

    def filed() -> None:
        lines = {name: sorted(at) for name, at in ran.items()}
        (out / f"{os.getpid()}.json").write_text(json.dumps(lines), encoding="utf-8")

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


def coverage_by_package(out: Path) -> list[str]:
    ran: dict[str, set[int]] = defaultdict(set)
    for filed in out.glob("*.json"):
        for name, lines in json.loads(filed.read_text(encoding="utf-8")).items():
            ran[os.path.normcase(name)].update(lines)
    root = Path(promptpotter.__file__).parent
    counted: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        package = "/".join(parts[: min(2, len(parts) - 1)]) or "promptpotter"
        body = _function_lines(path)
        counted[package][0] += len(body & ran[os.path.normcase(str(path))])
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
        for node, member in optimizers.NODE_MEMBERS.table.items()
    }
    owners = {d for d in packages.values() if d} | {
        d for n in optimizers.MANIFESTS.table if (d := owned(optimizers.MANIFESTS.get(n).directory))
    }

    def imports(files: Iterable[Path]) -> set[str]:
        hit: set[str] = set()
        for path in files:
            for target in imported(path):
                hit.update(d for d in owners if target.startswith(d))
        return hit

    # Only the optimizers package imports a member package (`gate.py::_IMPORTS_OPTIMIZER`).
    shared = imports(Path(optimizers.__file__).parent.glob("*.py"))
    out = {}
    for name in names:
        roster = select_optimizer(OptimizerChoice(optimizer=name)).member_nodes
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
    return {name: kept.key(OFFLINE_RUN_READS, without=skipped.get(name, ())) for name in names}


def still_green(home: Path, run: str, kept: LastGreen, keys: dict[str, str]) -> set[str]:
    return {n for n, k in keys.items() if kept.holds(f"{run} {n}", k) and (home / n).is_dir()}


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


def prepared_home(
    home: Path, stamp: dict[str, object], due: Sequence[str], *, held: bool, coverage: bool
) -> Path | None:
    """*home* cleared for *due*'s workspaces, and under *coverage* the directory every leg's
    process files the lines it ran into, named in the env each leg inherits."""
    if held:
        for name in due:
            if (home / name).exists():
                rmtree_robust(home / name)
    else:
        claim_home(home, stamp)
    if not coverage:
        return None
    covered = home / "coverage"
    if covered.exists():
        rmtree_robust(covered)
    covered.mkdir()
    os.environ[COVERAGE] = str(covered)
    return covered


def child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in [*env, *Settings.model_fields]:
        if key.endswith(("_KEY", "_TOKEN")):
            env[key] = ""
    # The SDKs refuse an EMPTY key before sending; every transport they could reach is faked.
    env.update(dict.fromkeys(PROVIDER_KEYS, NOT_A_KEY))
    env.update(PROMPTPOTTER_HOME=long_path(data_root(home)), PYTHONUTF8="1")
    return env


def decided(workspace: Path, *, after: int) -> dict[str, Any]:
    """Less what a resume legitimately moves: its init records, the checkouts it deleted, the round
    it re-enters, and which of a round's cells it paid and which it replayed, by path (`READING_SIDES`, `CELL_ROWS`, `FOLDS`): a
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
        if key == FOLDS:
            return {arm: less(fold, FOLD_SPLIT) for arm, fold in node.items()}
        held = less(node, SIDE_SPLIT) if key in READING_SIDES else node
        return {k: unsplit(v, k) for k, v in held.items()}

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

    for path in Path(long_path(data_root(workspace))).glob(
        "projects/*/campaigns/*/cycles/*/rounds/*.json"
    ):
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
    same = decided(straight, after=pause_after) == decided(paused, after=pause_after)
    print(
        f"potter: crossover on, two-parent arms recombined by host {hosted}; paused after round "
        f"{pause_after} and resumed, decisions {'UNMOVED' if same else 'MOVED'}"
    )
    return all(hosted.values()) and same


def less_heals(workspace: Path) -> dict[str, Any]:
    """Less what a heal adds to a call: its sends, and the failed attempts the call records."""
    doc = BehaviourTrace.read(workspace).decisions
    calls = sorted(
        ({**c, "repairs": 0} for c in doc["ledger"]["optimizer_calls"]),
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
        f"potter: {len(HEALS)} calls answered invalid healed, {sum(c['repairs'] for c in calls)} "
        f"failed attempts recorded; decisions {'MOVED ' + ', '.join(moved) if moved else 'UNMOVED'}"
    )
    return not moved


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


def read_straight_and_resumed(
    name: str, workspace: Path, resumed: Future[int] | None, pause_after: int
) -> int:
    ran = BehaviourTrace.read(workspace).decisions["run"]
    (cycle,) = (data_root(workspace) / PROJECTS_DIR).glob("*/campaigns/*/cycles/*")
    bench = ran["bench"]
    failed = int(not _graded(bench))
    lift = None if bench is None else bench["vs_origin"]["headline"]
    headline = (
        f"{lift['estimate']['value']:+.3f}"
        if lift is not None
        else "NO BENCH HEADLINE"
        if bench is None
        else f"none ({bench['vs_origin']['state']})"
    )
    print(f"{name}: {ran['stop_reason']}, bench lift {headline} -- {cycle}")
    if resumed is None:
        return failed
    if rc := resumed.result():
        print(f"{name}: RESUME FAILED (exit {rc}) -- {workspace / RESUMED / 'run.log'}")
        return failed + 1
    again = decided(workspace / RESUMED, after=pause_after)
    same = decided(workspace, after=pause_after) == again
    failed += not _graded(again["run"]["bench"]) or not same
    at, inside = paused_at(workspace / RESUMED)
    # Inside a round past the boundary, whose walk banked.
    landed = inside and at > pause_after
    failed += not landed
    where = f"inside round {at} past its banked walk" if inside else f"after round {at}"
    print(
        f"{name}: paused {where}{'' if landed else ' (NOT where its leg pauses)'} and resumed, "
        f"decisions {'UNMOVED' if same else 'MOVED'}"
    )
    return failed


def paused_at(workspace: Path) -> tuple[int, bool]:
    """The round its campaign's pause stood in, off its cycle's own ledger, and whether the pause
    lay inside it: entered and not yet closed when the pause was declared."""
    (ledger,) = Path(long_path(data_root(workspace))).glob(
        "projects/*/campaigns/*/cycles/*/.runtime/ledger.jsonl"
    )
    entered = closed = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        match record.get("record_type"):
            case "round_entered":
                entered = record["round"]
            case "round_closed":
                closed = record["round"]
            case "run_phase" if record.get("stop_reason") == StopReason.PAUSED:
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


def proven_home(workspace: Path) -> None:
    """A landing's plan over what the run wrote: every file named by a row, loading as it shapes, and
    nothing for a migration to stage or the landing to drop."""
    landing = plan_landing(data_root(workspace) / PROJECTS_DIR, reset=False)
    unknown = list(landing.unknown)
    refused = [f"{u.path}: {u.why}" for u in (*landing.unloadable, *landing.caches, *landing.runs)]
    refused += [f"{u.path}: {u.why}" for u in landing.torn]
    if unknown or refused or landing.staged:
        raise SystemExit(
            f"offline run: the run wrote what no data-root row names {unknown}, what its row's "
            f"reader refuses {refused}, or a shape a migration rewrites "
            f"{[one.said for one in landing.staged]}"
        )


def refuse_another_tree() -> None:
    if not (imported := Path(promptpotter.__file__).resolve().parent).is_relative_to(REPO):
        raise SystemExit(
            f"offline run: this interpreter imports {imported}, not {REPO}'s package, so the "
            f"run would answer for that other tree; set PYTHONPATH={REPO}"
        )


def run_child_leg(args: argparse.Namespace) -> None:
    if any(os.environ[k] != NOT_A_KEY for k in PROVIDER_KEYS):
        raise SystemExit("offline run: --child runs only under the env its parent builds")
    if filed_into := os.environ.get(COVERAGE):
        traced(Path(filed_into))
    workspace = Path(os.environ["PROMPTPOTTER_HOME"]).parent
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
        )
        print(asyncio.run(one)[0])
    proven_home(workspace)


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
        help="print, per package, the function-body lines every leg's process ran; keeps no green",
    )
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--pause-after", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--pause-in-walk", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--diverge", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--rebase", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--gated", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--crossover", choices=CROSSED_HOSTS, help=argparse.SUPPRESS)
    ap.add_argument("--heal", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--l4", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--terminal", choices=("new", "resume"), help=argparse.SUPPRESS)
    ap.add_argument("--interrupts", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    refuse_another_tree()
    if args.digests:
        print(json.dumps(digests(), indent=1))
        return 0
    if args.child:
        run_child_leg(args)
        return 0

    asked = [f"--{k}={v}" for k, v in sorted(vars(args).items()) if v != ap.get_default(k)]
    run = " ".join([OFFLINE_RUN, *(a for a in asked if not a.startswith("--against="))])
    home = named_home()
    complete_registries()
    arm_names = args.optimizer or ["potter", "capo"]
    names = (
        list(CONTROLLED)
        if args.controlled
        else args.optimizer or sorted(optimizers.MANIFESTS.table)
    )
    kept = None if args.coverage else LastGreen.open()
    keys = {} if kept is None else optimizer_keys(kept, names, controlled=args.controlled)
    held = args.against is None and holds_run(home, run)
    green = still_green(home, run, kept, keys) if held and kept is not None else set()
    for name in sorted(green):
        print(f"{name}: unchanged since green -- {home / name}")
    due = [name for name in names if name not in green]
    if not due:
        return 0
    stamp: dict[str, object] = {"offline": True, "note": LABEL, "run": run, "workspaces": names}
    queue_behind_siblings("offline_run")
    if kept is not None:
        # Unfiled first, so a run killed while it rewrites a workspace leaves no green behind.
        kept.record(dict.fromkeys((f"{run} {name}" for name in due), None))
    covered = prepared_home(home, stamp, due, held=held, coverage=args.coverage)
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
    tasks = 2 * len(due) + (len(INTERRUPTED) + 2) * firsts + 6 * potter_legs if legs else len(due)
    pool = ThreadPoolExecutor(RamBudget.read().offline_run_children(tasks))

    def run_child(name: str, workspace: Path, log: str, rounds: int, *extra: str) -> int:
        child = [sys.executable, __file__, "--child", name, f"--rounds={rounds}", *child_args]
        with (workspace / log).open("w", encoding="utf-8") as out:
            return subprocess.run(
                [*child, *extra],
                cwd=workspace,
                env=child_env(workspace),
                stdout=out,
                stderr=subprocess.STDOUT,
            ).returncode

    def spawn(name: str, workspace: Path, *extra: str) -> Future[int]:
        workspace.mkdir()
        return pool.submit(run_child, name, workspace, "run.log", args.rounds, *extra)

    def terminal(workspace: Path, verb: str, interrupts: int) -> int:
        flags = (f"--terminal={verb}", f"--interrupts={interrupts}")
        return run_child(first, workspace, f"{verb}.log", TERMINAL_ROUNDS, *flags)

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
    # Only potter's L2 proposes a fork.
    rebasing = spawn("potter", home / "potter" / REBASED, "--rebase") if potter_legs else None
    # Only potter's nodes answer structured, so only its calls climb the client's repair ladder.
    healing = spawn("potter", home / "potter" / HEALED, "--heal") if potter_legs else None
    # The L4 fixture's outer and inner optimizer are potter's.
    nesting = spawn("potter", home / "potter" / L4, "--l4") if potter_legs else None
    # Its pool is read off the closed rounds, so a resume must hand it the pool a straight run had.
    l1_host, l2_host = (f"--crossover={host}" for host in CROSSED_HOSTS)
    crossing = (
        None
        if not potter_legs
        else (
            spawn("potter", home / "potter" / CROSSED, l1_host),
            spawn(
                "potter",
                home / "potter" / f"{CROSSED}-{RESUMED}",
                l1_host,
                f"--pause-after={pause_after}",
            ),
            spawn("potter", home / "potter" / f"{CROSSED}-l2", l2_host),
        )
    )
    resumed = (
        {}
        if not legs
        else {
            name: spawn(name, home / name / RESUMED, f"--pause-in-walk={pause_after + 1}")
            for name in due
        }
    )
    failed = dict.fromkeys(due, 0)
    for name, child in children.items():
        workspace = home / name
        if rc := child.result():
            failed[name] += 1
            print(f"{name}: FAILED (exit {rc}) -- {workspace / 'run.log'}")
            continue
        if not args.controlled:
            failed[name] += read_straight_and_resumed(
                name, workspace, resumed[name] if legs else None, pause_after
            )
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
    said_leg(failed, "potter", home / "potter" / REBASED, rebasing, "a line forking at every L2")
    if crossing is not None:
        failed["potter"] += not crossed_legs(home / "potter", crossing, pause_after)
    if healing is not None:
        failed["potter"] += not healed_leg(home / "potter", healing)
    said_leg(failed, "potter", home / "potter" / L4, nesting, "an L4 campaign over inner runs")
    if args.against is not None:
        print("\n".join(moved_against(home, args.against, due)))
    if args.controlled and not any(failed.values()):
        print(controlled_reading(home, arm_names))
    print(f"wall clock {time.monotonic() - t0:.0f}s")
    if covered is not None:
        print("\n".join(coverage_by_package(covered)))
    # Filed under the flags it ran with, and only where the sources read the same as before it.
    if kept is not None:
        now = optimizer_keys(kept, due, controlled=args.controlled)
        kept.record({f"{run} {n}": keys[n] for n in due if not failed[n] and keys[n] == now[n]})
    return 1 if any(failed.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
