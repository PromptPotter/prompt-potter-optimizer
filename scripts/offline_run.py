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
import contextvars
import inspect
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import tests_owning
import yaml
from gate import RamBudget
from last_green import OFFLINE_RUN, OFFLINE_RUN_READS, LastGreen
from offline_world import (
    LABELS,
    MARKER,
    NOT_A_KEY,
    PROVIDER_KEYS,
    FakeBackend,
    FakeLLM,
    Router,
    completion,
    draw,
    install_network,
)
from processes import REPO, end_children_with_this_process, queue_behind_siblings

from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_operator_fork
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.datasets.draft_campaign import split_overlay
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import (
    grade_bench,
    open_session,
    run_campaign,
    run_head_to_head,
)
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import (
    binding_prompt_overrides,
    manifest_directories,
    resolve_optimizer,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.runner.inner.connector import measurement_modules
from promptpotter.config.paths import default_projects_root, install_datasets_root
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import Settings
from promptpotter.domain.bench import BenchScore, BenchTrigger
from promptpotter.domain.campaign import Compare
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
)
from promptpotter.domain.command_kinds import ArmRequest, HeadToHeadRequest, SkipSearchpointPayload
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.phases import RunPhase, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.ruler import RulerOwn
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import CycleSeed
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    bind_memory_scope,
    list_populations,
    load_population,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_answers
from promptpotter.infrastructure.store.dataset_access import read_dataset_pipeline, resolve_dataset
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.layout import dataset_id_prefix
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.identity import default_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.paired_reading import PairedReading
    from promptpotter.domain.results import CycleResult
    from promptpotter.infrastructure.store.stores import Stores

DATASET = "justlogic/d234"
STAMP = "offline-run.json"
CONTROLLED = ("controlled", "controlled-bare")
RESUMED = "resumed"
DIVERGED = "diverged"
REBASED = "rebased"
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
# The Ctrl+C contract is the pause and the resume, not the search: one round ends the cycle soonest.
TERMINAL_ROUNDS = 1
RESUME_APPENDS = ("phases", "run_phases", "arms", "rulers")
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


def long_path(p: Path) -> str:
    # A cycle tree nests past Windows MAX_PATH=260 under a deep home; the prefix lifts it.
    s = str(p.resolve())
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\?\\") else s


_ROW_MARK = re.compile(r"\[#(\d+)\]")


def _marks(text: str) -> list[int]:
    return list(dict.fromkeys(int(m) for m in _ROW_MARK.findall(text)))


_AXIS_MENU = re.compile(r"EXACTLY one of: (.+)")
_TASK_CONTEXT = "TASK CONTEXT:\n"


def _axis_menu(prompt: str) -> list[str]:
    menu = _AXIS_MENU.search(prompt)
    assert menu is not None, "offline run: the critique prompt names no axis list"
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


class ByCallIdentity:
    """The offline run's `FakeLLM` policy: an answer follows the CALL's identity, never the
    prompt's wording nor the arrival order. *rebasing*, every L2 fire proposes a fork. *crossing*,
    an odd variant rewrites a second field, so two arms can differ on two loci a crossover swaps."""

    def __init__(
        self,
        capture: Path,
        templates: dict[str, str],
        *,
        rebasing: bool = False,
        crossing: bool = False,
    ) -> None:
        self.capture = capture
        self.templates = templates
        self.rebasing = rebasing
        self.crossing = crossing
        self.prefixes = {node: template.split("{{")[0] for node, template in templates.items()}
        self.calls: dict[str, int] = {}
        self.turns: dict[tuple[str, int | None], int] = {}

    def _text_node(self, prompt: str) -> str:
        opened = [node for node, prefix in self.prefixes.items() if prompt.startswith(prefix)]
        if not opened:
            raise KeyError(f"offline run: no llm node's template opens {prompt[:80]!r}")
        return max(opened, key=lambda node: len(self.prefixes[node]))

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        prompt = "\n".join(str(m["content"]) for m in body["messages"])
        schema = body.get("response_format", {}).get("json_schema")
        node = schema["name"] if schema else self._text_node(prompt)
        self.calls[node] = self.calls.get(node, 0) + 1
        # Concurrent sends of one node land in any order; their seeds (`Calls.ask`) tell
        # them apart, and sends sharing a seed are sequential, so their turn is theirs alone.
        seed: int | None = body.get("seed")
        turn = self.turns.get((node, seed), 0)
        self.turns[node, seed] = turn + 1
        call = str(turn) if seed is None else f"{seed}.{turn}"
        if schema:
            filler = SchemaFiller(schema["schema"], node=node, call=call)
            obj = filler.fill(schema["schema"], ())
            content = json.dumps(
                self._shape(node, call, turn, obj, filler, schema["schema"], prompt)
            )
        else:
            content = self._text(node, call, prompt)
        (self.capture / f"{node}_{call}.json").write_text(
            json.dumps(
                {"request": body, "response": content}, indent=1, sort_keys=True, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        return completion(content, model=body["model"], id=f"offline-{node}-{call}")

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
            ids = _marks(prompt)
            obj["failure_highlights"] = [
                {
                    "sample_ids": ids[k : k + 2],
                    "mechanism": f"negation tracking {call}.{k}",
                    "claim": f"critique {call}: the chain breaks at a negation; said and truth differ",
                }
                for k in range(0, min(len(ids), 4), 2)
            ]
            # Read off the prompt's axis list, as a model naming its axis verbatim would.
            axes = _axis_menu(prompt)
            lead = next((a for a in axes if a.endswith(".instruction")), axes[0])
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
        # A citation quotes the panel it names: the framing, at an offset that is the call's, not the text's.
        assert "task_context" in cited and _TASK_CONTEXT in prompt, (
            "offline run: no framing to cite"
        )
        start = prompt.index(_TASK_CONTEXT) + len(_TASK_CONTEXT) + draw("cite", generation, i) % 20
        v["evidence_grounding"] = {
            "field": "task_context",
            "citation": prompt[start : start + 40],
        }
        # Every prompt node the wire offers an `instruction` on: a model sees no "first" node.
        offered = item["properties"]["prompt_fields_updates"]["properties"]
        second = ("thinking_style",) if self.crossing and i % 2 else ()
        v["prompt_fields_updates"] = {
            node: {
                field: _variant(
                    f"g{generation}v{i}{node}{tag}",
                    random.Random(f"l1:{generation}:{i}:{node}{tag and f':{tag}'}"),
                )
                for field, tag in (("instruction", ""), *((f, f) for f in second))
                if field in fields["properties"]
            }
            for node, fields in offered.items()
            if "instruction" in fields["properties"]
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
    src = definition_source(resolve_dataset(stores, DATASET).directory)
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
    raw = dict(dataset_campaign_block(install_datasets_root() / DATASET))
    # The narrowing a check-in of the shipped definition declares, its `optimizer` blocks included.
    pipeline = read_dataset_pipeline(install_datasets_root() / DATASET)
    assert pipeline is not None, f"{DATASET} declares a pipeline"
    _, narrowing = split_overlay(pipeline["nodes"])
    raw.update(BENCH, bench_trigger=bench_trigger, optimizer_narrowing=narrowing)
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
        roster = resolve_optimizer(optimizer, {}, gate="off").manifest.nodes
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


def edited_proposer(config: CampaignConfig) -> dict[str, dict[str, Any]]:
    """Appended, so the fake still knows the node by its template's opening."""
    selected = select_optimizer(config.optimization)
    node = selected.proposer
    held = running_prompt(node, selected.node_config(node), selected.manifest)
    return {node: {"problem_description": f"{held.problem_description}\n\nEdited on resume."}}


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
    if derive_run_state(layout.cycle_dir).producer.attached:
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
        cycle_id = mint_operator_fork(
            stores=stores, hop=fork, from_round=from_round, seed=CycleSeed(), keep_rounds=True
        )
        fork = CycleHop(campaign_id=fork.campaign_id, cycle_id=cycle_id)
        session, result = await run_campaign(
            await rebound_session(stores, fork),
            readout_sink=print,
            budget=Budget(),
            mode=RunMode(),
        )
        await session.backend_client.aclose()
        ended = stop_reason_outcome(result.stop_reason).exit_code or session.hop != fork
        if ended or not closed_a_round(stores, fork):
            raise SystemExit(
                f"offline run: the fork from round {from_round} ended {result.stop_reason}"
            )
    print(f"forked twice: {fork.cycle_id} is a fork of a fork")


async def rebound_session(stores: Stores, hop: CycleHop) -> Session:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    assert campaign is not None
    session = await open_session(DATASET, stores=stores)
    return session.bound(hop, purpose=campaign.declaration.purpose)


@dataclass(frozen=True)
class Harness:
    """Every network edge one act's campaigns reach, answered in-process."""

    llm: ByCallIdentity
    backend: FakeBackend
    router: Router

    @classmethod
    def installed(
        cls, out: Path, templates: dict[str, str], samples: list[Sample], *, crossing: bool
    ) -> Harness:
        (out / "requests").mkdir(parents=True)
        llm = ByCallIdentity(out / "requests", templates, crossing=crossing)
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
    bench_trigger: BenchTrigger = "at_end",
    diverge: bool = False,
    crossover: str | None = None,
) -> tuple[Path, str]:
    """The archive is the workspace's, never *out*'s: campaigns in one process share it."""
    out = workspace if out is None else out
    config = campaign_config(optimizer, rounds, bench_trigger=bench_trigger, crossover=crossover)
    if not framing:
        config = config.model_copy(update={"task_framing": "off"})
    samples = synthetic_rows(rows)
    harness = Harness.installed(
        out, text_templates(config), samples, crossing=crossover is not None
    )

    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    session = await open_session(DATASET, stores=stores)
    session, result = await run_campaign(
        session,
        config,
        readout_sink=print,
        budget=Budget(),
        step_rounds=pause_after,
        mode=RunMode(),
    )
    await session.backend_client.aclose()
    if pause_after is not None:
        if result.stop_reason != StopReason.PAUSED:
            raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}, never paused")
        # The resume reads its rounds off the ledger and the archive: their checkouts are gone.
        shutil.rmtree(stores.campaigns.layout(session.hop).rounds)
        paused = session.hop
        session = await rebound_session(stores, paused)
        with binding_prompt_overrides(edited_proposer(config) if diverge else None):
            session, result = await run_campaign(
                session,
                readout_sink=print,
                budget=Budget(),
                mode=RunMode(fork_on_divergence=diverge),
            )
            if diverge:
                forked_at_init(stores, paused, session.hop)
                await forked_twice(stores, session.hop, rounds)
        await session.backend_client.aclose()
    cycle = await banked(
        stores, session, result, harness, out, name=optimizer, bench_trigger=bench_trigger
    )
    return cycle, session.campaign_id


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
    """Checks one campaign's run, then writes what it decided to *out*'s `decisions.json`."""
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
        if bench.status.state != "not_asked" or not bench.status.can_grade:
            raise SystemExit(f"offline run: a manual launch of {name} serves {bench}")
        bench = await grade_bench(stores, session.hop.campaign_id)
    decisions = extract(cycle, bench)
    decisions["harness"] = {
        "stop_reason": str(result.stop_reason),
        "unrouted": sorted(harness.router.unrouted),
        "llm_calls": dict(sorted(harness.llm.calls.items())),
        "backend_cells": searched_cells,
        "bench_cells_asked_for": backend.cells - searched_cells,
    }
    write_decisions(out, decisions)
    return cycle


def write_decisions(out: Path, decisions: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "decisions.json").write_text(
        json.dumps(decisions, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


async def run_rebasing(workspace: Path, *, rounds: int, rows: int) -> None:
    """Potter resumed with `--from` under an L2 proposing the same fork at every fire: only the
    first changes something, and the line's forks spend one round budget between them."""
    # L2 fires every round, so the fork owes nothing to whether the fake replies happen to stall.
    config = campaign_config(
        "potter", rounds, bench_trigger="manual", knobs={"escalation": EVERY_L2}
    )
    samples = synthetic_rows(rows)
    (workspace / "requests").mkdir()
    llm = ByCallIdentity(workspace / "requests", text_templates(config))
    # A rewind lands on a round holding a θ level, which only a ruler spanning the rows gives.
    install_network(Router(FakeLLM(llm), FakeBackend(samples, hardness=REBASE_HARDNESS)))
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    # The first campaign banks the rows the second's ruler is fit on; the second pauses at 1.
    for step_rounds in (None, 1):
        session, _ = await run_campaign(
            await open_session(DATASET, stores=stores),
            config,
            readout_sink=print,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(),
        )
        await session.backend_client.aclose()
    llm.rebasing = True
    session, _ = await run_campaign(
        await rebound_session(stores, session.hop),
        readout_sink=print,
        budget=Budget(),
        mode=RunMode(from_round=1),
    )
    await session.backend_client.aclose()
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


def run_terminal(
    optimizer: str, workspace: Path, verb: str, *, rounds: int, rows: int, interrupts: int
) -> None:
    config = campaign_config(optimizer, rounds, bench_trigger="at_end")
    samples = synthetic_rows(rows)
    requests = workspace / f"requests-{verb}"
    requests.mkdir()
    llm = FakeLLM(ByCallIdentity(requests, text_templates(config)))
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


def _commands(ledger: Path) -> dict[str, list[dict[str, Any]]]:
    records = _jsonl(ledger)
    applied = {
        r["command_id"]
        for r in records
        if r.get("record_type") == "command_ack" and r.get("status") == "applied"
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r.get("record_type") == "command" and r["command_id"] in applied:
            out.setdefault(r["kind"], []).append(r["payload"])
    return out


def interrupted(
    spawn_terminal: Callable[[Path, str, int], int], workspace: Path, interrupts: int
) -> tuple[bool, str]:
    workspace.mkdir()
    if (rc := spawn_terminal(workspace, "new", interrupts)) != StopOutcome.PAUSED.exit_code:
        return False, f"`new` under {interrupts} SIGINT exited {rc}, not as a pause does"
    (cycle,) = (Path(long_path(workspace)) / "projects").glob("*/campaigns/*/cycles/*")
    stopped = derive_run_state(cycle)
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in (workspace / "jobs").glob("*.json")]
    if stopped.producer.attached or any(j["released_at"] is None for j in jobs):
        return False, f"`new` exited 130 still holding its cycle or its slot ({stopped})"
    if interrupts == 1 and stopped.run_phase is not RunPhase.PAUSED:
        return False, f"one SIGINT left the cycle {stopped.run_phase}, not paused"
    (mint,) = _commands(cycle.parents[3] / ".workspace" / "events.jsonl")["mint-campaign"]
    if not mint["campaign_config"]:
        return False, f"the mint-campaign record carries no config: {sorted(mint)}"
    if rc := spawn_terminal(workspace, "resume", 0):
        return False, f"`resume` after {interrupts} SIGINT exited {rc}"
    ended = derive_run_state(cycle)
    started = _commands(cycle / ".runtime" / "ledger.jsonl").get("start-run", [])
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
    harness = Harness.installed(workspace / "arms", templates, samples, crossing=False)
    stores = build_stores(default_identity(), projects_root=default_projects_root())
    stand_in_fetch(stores, samples)
    session = await open_session(DATASET, stores=stores)
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
        session,
        CampaignConfig.model_validate(shared),
        request,
        mode=RunMode(),
        readout_sink=print,
    )
    await session.backend_client.aclose()
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
    cycle, cid = arms[arm_names[0]]
    try:
        await CommandDispatcher(stores).dispatch(
            CommandCall(SkipSearchpointPayload(campaign_id=cid, cycle_id=cycle.name), "skip-arm")
        )
    except ConflictError as exc:
        print(f"skip on an arm: {exc.http_status} {exc}")
    else:
        raise SystemExit("offline run: a skip on a controlled arm was applied")

    def walks(campaign_id: str) -> CampaignWalks:
        return CampaignWalks(stores.campaigns, campaign_id)

    def filed(campaign_id: str) -> frozenset[str]:
        return scan_ledger_answers(walks(campaign_id).spans())

    bank = stores.tenant_datasets.labelled(DATASET)
    assert bank is not None, "every arm ran on the labelled bank"

    def memory(campaign_id: str) -> set[str]:
        bind_memory_scope(ArmOf(walks(campaign_id), ruler_version=0))
        return {
            answer.answer
            for entry in list_populations(stores, bank)
            for answer in load_population(stores, entry, bank)
        }

    foreign_only = set().union(*(filed(c) for c in subjects)) - set().union(
        *(filed(c) for _, c in arms.values())
    )
    for name, (_, cid) in arms.items():
        seen = contextvars.copy_context().run(memory, cid)
        if seen & foreign_only or not seen <= filed(cid):
            raise SystemExit(f"offline run: arm {name}'s memory reads an answer it did not walk")
        print(
            f"arm {name}: memory holds {len(seen)} own answers, none of {len(foreign_only)} foreign"
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
    ev = subject_evidence(stores, [SubjectSpec("campaign", c) for c in subjects])
    table = ev.head_to_head
    assert table is not None
    (workspace / "head_to_head.json").write_text(table.model_dump_json(indent=1), encoding="utf-8")
    print(
        f"head-to-head {table.guard.head_to_head_id}: scorer {ev.scorer_id}, differs_on "
        f"{[d.value for d in table.guard.differs_on]}, pairs "
        f"{[(*_pair_ends(p.reading), p.guard.state.value) for p in table.pairs]}, "
        f"guard {[(r.optimizer, r.guard.state.value) for r in table.rows]}"
    )


def _pair_ends(pair: PairedReading) -> tuple[str, ...]:
    return tuple(m.address.path[-1].campaign_id for m in (pair.a, pair.b) if m is not None)


def _graded(bench: dict[str, Any] | None) -> bool:
    return bench is not None and bench["status"]["state"] == "read"


# decisions.json is read as dicts with `.get`: a dump taken before a reshape must load after it.
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
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMBEDDED_HEX32 = re.compile(r"[0-9a-f]{32}")
_SHORT_HEX = re.compile(r"\b[0-9a-f]{6,12}\b")
_CYCLE_ID = re.compile(r"cycle_[0-9a-f]{8,}")
_CAMPAIGN_ID = re.compile(re.escape(dataset_id_prefix(DATASET)) + r"__[0-9a-f]{6}")
_SESSION_ID = re.compile(r"\bs_[0-9a-f]{8}\b")
_WIN_PATH = re.compile(r"(?:\\\\\?\\)?[A-Za-z]:[\\/][^\s'\"<>|]*")


class Canon:
    def __init__(self, ids: dict[str, str]) -> None:
        self.ids = ids
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
        v = _CYCLE_ID.sub("<cycle>", v)
        v = _SESSION_ID.sub("<session>", v)
        return _WIN_PATH.sub("<path>", v)

    def __call__(self, o: Any) -> Any:
        if isinstance(o, dict):
            return {self.s(str(k)): self(v) for k, v in o.items() if str(k) not in DROP_KEYS}
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
    out["health"] = {k: health.get(k) for k in ("grade", "cause", "suggested_action")}
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
    "decision": ("decisions", ("round", "kind", "outcome", "inputs_ref", "data")),
    "election": ("elections", ("round", "selected", "ended_on", "fit")),
    "arm": ("arms", ("round", "arm", "state")),
    "phase": ("phases", ("round", "phase", "event")),
    "round_entered": ("rounds_entered", ("round",)),
    "round_closed": ("rounds_closed", ("round", "election")),
    "run_phase": ("run_phases", ("run_phase", "stop_reason")),
    "llm_call": ("optimizer_calls", ("round", "node", "candidate_idx")),
    "round_warning": ("round_warnings", ("round", "kind", "severity", "detail")),
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
    for name in ("decisions", "round_warnings", "optimizer_calls"):
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
    canon = Canon(ids)
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
        selected = resolve_optimizer(name, {}, gate="off")
        out[f"treatment:{name}"] = selected.treatment(
            frozenset(Presumption), PackShelf(account=None)
        ).digest
    return out


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
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO).as_posix()
            package = path.name == "__init__.py"
            for target in tests_owning.imported(tree, tests_owning.module_name(rel), package):
                hit.update(d for d in owners if target.startswith(d))
        return hit

    # Only the optimizers package imports a member package (`gate.py::_IMPORTS_OPTIMIZER`).
    shared = imports(Path(optimizers.__file__).parent.glob("*.py"))
    out = {}
    for name in names:
        roster = resolve_optimizer(name, {}, gate="off").member_nodes
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


def child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in [*env, *Settings.model_fields]:
        if key.endswith(("_KEY", "_TOKEN")):
            env[key] = ""
    # The SDKs refuse an EMPTY key before sending; every transport they could reach is faked.
    env.update(dict.fromkeys(PROVIDER_KEYS, NOT_A_KEY))
    env.update(LANGFUSE_ENABLED="false")
    # A developer's own registry must never receive a synthetic campaign's winner.
    env.update(PROMPTPOTTER_HOME=long_path(home), PYTHONUTF8="1", MLFLOW_TRACKING_URI="")
    return env


def decided(workspace: Path, *, after: int) -> dict[str, Any]:
    """Less what a resume legitimately moves: its init records and the checkouts it deleted."""
    doc: dict[str, Any] = json.loads((workspace / "decisions.json").read_text(encoding="utf-8"))
    kept = {k: v for k, v in doc["ledger"].items() if k not in RESUME_APPENDS}
    return {**doc, "ledger": kept, "rounds": [r for r in doc["rounds"] if r["round"] > after]}


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

    for path in Path(long_path(workspace)).glob("projects/*/campaigns/*/cycles/*/rounds/*.json"):
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


def moved_against(workspace: Path, earlier: Path) -> str:
    if not (earlier / "decisions.json").is_file():
        return f"no run in {earlier}"

    def held(home: Path) -> dict[str, object]:
        requests = {p.name: p.read_bytes() for p in sorted((home / "requests").iterdir())}
        return {"decisions": (home / "decisions.json").read_bytes(), "requests": requests}

    now, then = held(workspace), held(earlier)
    return ", ".join(f"{part} {'UNMOVED' if now[part] == then[part] else 'MOVED'}" for part in now)


def read_straight_and_resumed(
    name: str, workspace: Path, resumed: Future[int], pause_after: int, against: Path | None
) -> int:
    ran = json.loads((workspace / "decisions.json").read_text(encoding="utf-8"))["run"]
    (cycle,) = (workspace / "projects").glob("*/campaigns/*/cycles/*")
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
    if against is not None:
        print(f"{name}: against {against}: {moved_against(workspace, against / name)}")
    if rc := resumed.result():
        print(f"{name}: RESUME FAILED (exit {rc}) -- {workspace / RESUMED / 'run.log'}")
        return failed + 1
    again = decided(workspace / RESUMED, after=pause_after)
    same = decided(workspace, after=pause_after) == again
    # A pause at the origin's boundary replays round 0 on resume, which re-records it.
    failed += not _graded(again["run"]["bench"]) or (pause_after > 0 and not same)
    print(
        f"{name}: paused after round {pause_after} and resumed, decisions "
        f"{'UNMOVED' if same else 'MOVED'}"
    )
    return failed


def run_child_leg(args: argparse.Namespace) -> None:
    if any(os.environ[k] != NOT_A_KEY for k in PROVIDER_KEYS):
        raise SystemExit("offline run: --child runs only under the env its parent builds")
    workspace = Path(os.environ["PROMPTPOTTER_HOME"])
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
    elif args.terminal:
        run_terminal(
            args.child,
            workspace,
            args.terminal,
            rounds=args.rounds,
            rows=args.rows,
            interrupts=args.interrupts,
        )
    elif args.rebase:
        asyncio.run(run_rebasing(workspace, rounds=args.rounds, rows=args.rows))
    else:
        one = run_one(
            args.child,
            workspace,
            rounds=args.rounds,
            rows=args.rows,
            pause_after=args.pause_after,
            bench_trigger=args.bench_trigger,
            diverge=args.diverge,
            crossover=args.crossover,
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
        "--bench-trigger",
        choices=get_args(BenchTrigger),
        default="at_end",
        help="`manual` runs every campaign as the default config does, fails on a bench cell "
        "sent before the pass is asked for, then asks for it",
    )
    ap.add_argument(
        "--against",
        type=Path,
        help="an earlier run's home: print per optimizer whether its decisions and requests moved",
    )
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--pause-after", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--diverge", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--rebase", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--crossover", choices=CROSSED_HOSTS, help=argparse.SUPPRESS)
    ap.add_argument("--terminal", choices=("new", "resume"), help=argparse.SUPPRESS)
    ap.add_argument("--interrupts", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
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
    kept = LastGreen.open()
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
    if held:
        for name in due:
            if (home / name).exists():
                rmtree_robust(home / name)
    else:
        claim_home(home, stamp)
    end_children_with_this_process()
    # One workspace each: a shared archive and δ ruler let one optimizer's run move another's.
    child_args = ["--rows", str(args.rows), "--bench-trigger", args.bench_trigger]
    if args.controlled:
        child_args += [f"--optimizer={name}" for name in arm_names]
    t0 = time.monotonic()

    # The first optimizer's workspace holds the CLI's legs, and potter's its own: they run with it.
    first = names[0]
    firsts = not args.controlled and first in due
    pause_after = min(1, args.rounds - 1)
    # Potter's own legs need rounds past the one they pause at.
    potter_legs = not args.controlled and "potter" in due and pause_after > 0
    tasks = (
        len(due)
        if args.controlled
        else 2 * len(due) + (len(INTERRUPTED) + 1) * firsts + 4 * potter_legs
    )
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
    # Only potter's L2 proposes a fork.
    rebasing = spawn("potter", home / "potter" / REBASED, "--rebase") if potter_legs else None
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
        if args.controlled
        else {
            name: spawn(name, home / name / RESUMED, f"--pause-after={pause_after}") for name in due
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
                name, workspace, resumed[name], pause_after, args.against
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
    if rebasing is not None:
        rc = rebasing.result()
        failed["potter"] += bool(rc)
        log = home / "potter" / REBASED / "run.log"
        lines = log.read_text(encoding="utf-8").splitlines()
        ended = f"FAILED (exit {rc}) -- {log}" if rc else ""
        ended = ended or next(ln for ln in reversed(lines) if ln.startswith("rebased "))
        print(f"potter: a line proposing a fork at every L2 fire {ended}")
    if crossing is not None:
        failed["potter"] += not crossed_legs(home / "potter", crossing, pause_after)
    if args.controlled and not any(failed.values()):
        beside, bare = (home / name for name in CONTROLLED)
        for arm in (f"arm-{name}" for name in arm_names):
            # A reading: the foreign campaign's rows are in the version the head-to-head froze.
            same = (beside / arm / "decisions.json").read_bytes() == (
                bare / arm / "decisions.json"
            ).read_bytes()
            print(f"{arm}: decisions {'UNMOVED' if same else 'MOVED'} by the foreign campaign")
        lines = (beside / "run.log").read_text(encoding="utf-8").splitlines()
        print("\n".join(line for line in lines if line.startswith(("skip on", "arm ", "head-to-"))))
    print(f"wall clock {time.monotonic() - t0:.0f}s")
    # Filed under the flags it ran with, and only where the sources read the same as before it.
    if kept is not None:
        now = optimizer_keys(kept, due, controlled=args.controlled)
        kept.record({f"{run} {n}": keys[n] for n in due if not failed[n] and keys[n] == now[n]})
    return 1 if any(failed.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
