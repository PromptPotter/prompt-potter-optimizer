"""The one supported OFFLINE run: a real campaign per installed optimizer on ``justlogic/d234``,
every network edge answered in-process, zero spend. What it is for, what it pins and how to read
it: ``docs/developer/offline-run.md``.

    PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py [--optimizer NAME ...]
    python scripts/offline_run.py --digests
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
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
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

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
from processes import end_children_with_this_process

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
)
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import grade_line_bench, open_session, run_campaign
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import (
    resolve_optimizer,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolve_campaign_config,
)
from promptpotter.application.runner.inner.connector import measurement_modules
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, install_datasets_root
from promptpotter.config.settings import Settings
from promptpotter.domain.bench import BenchScore, BenchTrigger
from promptpotter.domain.campaign import Arm, Compare, Optimize, Purpose
from promptpotter.domain.command_kinds import SkipSearchpointPayload
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import RunPhase, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.run_mode import RunMode
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
from promptpotter.infrastructure.store.dataset_access import resolve_dataset
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.layout import CycleLayout, dataset_id_prefix
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.identity import default_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.paired_reading import PairedReading
    from promptpotter.infrastructure.store.stores import Stores

DATASET = "justlogic/d234"
STAMP = "offline-run.json"
CONTROLLED = ("controlled", "controlled-bare")
RESUMED = "resumed"
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
SCALED: dict[str, dict[str, dict[str, Any]]] = {
    "capo": {
        "blocks": {"config": {"block_size": 5, "max_blocks": 4}},
        "paired_t": {"config": {"alpha": 0.2}},
        "population": {"config": {"size": 4}},
        "capo_init": {"config": {"size": 4, "k_max": 2}},
        "mating": {"config": {"offspring": 2}},
        "few_shot": {"config": {"k_max": 2}},
    },
    "gepa": {"minibatch": {"config": {"pareto_size": 20}}},
    "levi": {
        "proxy_css": {"config": {"discovery": 40, "size": 10}},
        "levi_paradigm_shift": {"config": {"interval": 4}},
        "levi_seed": {"config": {"n_diverse_seeds": 2}},
        "map_elites": {"config": {"centroids": 8, "cvt_samples": 400}},
    },
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


class ByCallIdentity:
    """The offline run's `FakeLLM` policy: an answer follows the CALL's identity, never the
    prompt's wording nor the arrival order."""

    def __init__(self, capture: Path, templates: dict[str, str]) -> None:
        self.capture = capture
        self.templates = templates
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
        # Concurrent sends of one node land in any order; their seeds (`NodeContext.ask`) tell
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
            item = filler.resolve(filler.resolve(schema)["properties"]["variants"])
            obj["variants"] = [
                self._variant_edit(turn, i, filler, filler.resolve(item["items"]), prompt)
                for i in range(item.get("minItems") or item["maxItems"])
            ]
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
            obj["priority_fix"] = f"instruction: track every negation explicitly (critique {call})"
            obj["suggested_axes"] = ["instruction", "thinking_style"]
        elif node.startswith("L2Context"):
            obj["axis_targeted"] = "instruction"
            obj["rationale"] = f"L2 fire {call}: L1 stalled on the instruction axis."
            obj.pop("fork_proposal", None)
            obj.pop("terminate_proposal", None)
        elif node.startswith("L3Plan"):
            obj["plan"] = (
                f"Replan {call}: L1 rewrites the instruction to track negation explicitly."
            )
            obj["note"] = f"l3 note {call}"
            obj["rationale"] = f"L2 stalled twice (replan {call})"
            obj.pop("fork_proposal", None)
            obj.pop("terminate_proposal", None)
        return obj

    def _variant_edit(
        self, generation: int, i: int, filler: SchemaFiller, item: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        """*generation* is the call's turn whatever its seed: the `SKILL` trajectory is keyed on it."""
        v: dict[str, Any] = filler.fill(item, ("variants", str(i)))
        grounding = filler.resolve(item["properties"]["evidence_grounding"])
        cited = filler.resolve(grounding["properties"]["field"])["enum"]
        # A citation must quote the prompt; the offset is a function of the call, not the text.
        start = draw("cite", generation, i) % max(len(prompt) - 60, 1)
        v["evidence_grounding"] = {
            "field": cited[i % len(cited)],
            "citation": prompt[start : start + 40],
        }
        # Every prompt node the wire offers an `instruction` on: a model sees no "first" node.
        offered = item["properties"]["prompt_fields_updates"]["properties"]
        v["prompt_fields_updates"] = {
            node: {
                "instruction": _variant(
                    f"g{generation}v{i}{node}", random.Random(f"l1:{generation}:{i}:{node}")
                )
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
    optimizer: str, rounds: int, *, bench_trigger: BenchTrigger, pinned: bool = False
) -> CampaignConfig:
    """*pinned* seeds every optimizer's draws alike, which arms of one head-to-head must share."""
    raw = dict(dataset_campaign_block(install_datasets_root() / DATASET))
    raw.update(BENCH, bench_trigger=bench_trigger)
    opt = raw["optimization"]
    # The template's node overlay is written for the optimizer it selects, and only that one.
    templated = opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
    opt.update(budget={"usd": 1000.0, "rounds": rounds}, origin_gate="off", optimizer=optimizer)
    if optimizer != templated:
        opt["nodes"] = SCALED.get(optimizer, {})
    if optimizer != templated or pinned:
        # Its draws follow the campaign's id unless a clamp seeds them, and every run mints an id.
        opt["determinism"] = {"seed": 0}
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


async def rebound_session(stores: Stores, hop: CycleHop) -> Session:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    assert campaign is not None
    session = await open_session(DATASET, stores=stores)
    session = configure_and_apply_pipeline(session, resolve_campaign_config(stores, campaign, hop))
    return session.bound(hop, purpose=campaign.declaration.purpose)


async def run_one(
    optimizer: str,
    workspace: Path,
    *,
    rounds: int,
    rows: int,
    out: Path | None = None,
    purpose: Purpose = Optimize(),  # noqa: B008 — frozen
    framing: bool = True,
    pause_after: int | None = None,
    bench_trigger: BenchTrigger = "at_end",
) -> tuple[Path, str]:
    """The archive is the workspace's, never *out*'s: campaigns in one process share it."""
    out = workspace if out is None else out
    (out / "requests").mkdir(parents=True)
    arm = purpose.arm if isinstance(purpose, Compare) else None
    config = campaign_config(optimizer, rounds, bench_trigger=bench_trigger, pinned=arm is not None)
    if not framing:
        config = config.model_copy(update={"task_framing": "off"})
    samples = synthetic_rows(rows)
    llm = ByCallIdentity(out / "requests", text_templates(config))
    backend = FakeBackend(samples)
    router = Router(FakeLLM(llm), backend)
    install_network(router)

    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    stand_in_fetch(stores, samples)
    session = await open_session(DATASET, stores=stores)
    session = configure_and_apply_pipeline(session, config)
    session, result = await run_campaign(
        session,
        config,
        readout_sink=print,
        budget=Budget(),
        step_rounds=pause_after,
        mode=RunMode(),
        purpose=purpose,
    )
    await session.backend_client.aclose()
    if pause_after is not None:
        if result.stop_reason != StopReason.PAUSED:
            raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}, never paused")
        # The resume reads its rounds off the ledger and the archive: their checkouts are gone.
        shutil.rmtree(CycleLayout(stores.campaigns.cycle_dir(session.hop)).rounds)
        session = await rebound_session(stores, session.hop)
        session, result = await run_campaign(
            session,
            readout_sink=print,
            budget=Budget(),
            mode=RunMode(),
        )
        await session.backend_client.aclose()
    if stop_reason_outcome(result.stop_reason).exit_code:
        raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}")
    beside = sorted(
        f"{node.name}.{key}"
        for node in session.pipeline_schema.declared_nodes
        for key in node.param_keys
        if key not in node.prompt_fields
    )
    if arm is not None and beside:
        raise SystemExit(f"offline run: arm {arm.arm_key} searched {beside} beside its prompt")
    stores.campaigns.update_campaign(session.campaign_id, label=LABEL)
    cycle = Path(stores.campaigns.cycle_dir(session.hop))
    dashboard = json.loads((cycle / "dashboard.json").read_text(encoding="utf-8"))
    spend = SpendRollup.model_validate(dashboard["spend"])
    if billed := spend.total_used_usd:
        raise SystemExit(f"offline run billed ${billed}: a fake answered with a cost")
    bench, searched_cells = result.bench, backend.cells
    # An arm is minted `at_end` whatever its config says, so only an ordinary campaign waits.
    if bench_trigger == "manual" and arm is None:
        sent = spend.by_kind["bench"]
        if sent.incurred_usd or sent.input_tokens or sent.output_tokens:
            raise SystemExit(f"offline run: a manual launch of {optimizer} sent bench cells")
        if bench is None or bench.status.state != "not_asked" or not bench.status.can_grade:
            raise SystemExit(f"offline run: a manual launch of {optimizer} serves {bench}")
        bench = await grade_line_bench(stores=stores, hop=session.hop)
    decisions = extract(cycle, bench)
    decisions["harness"] = {
        "stop_reason": str(result.stop_reason),
        "unrouted": sorted(router.unrouted),
        "llm_calls": dict(sorted(llm.calls.items())),
        "backend_cells": searched_cells,
        "bench_cells_asked_for": backend.cells - searched_cells,
    }
    (out / "decisions.json").write_text(
        json.dumps(decisions, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return cycle, session.campaign_id


def run_terminal(
    optimizer: str, workspace: Path, verb: str, *, rounds: int, rows: int, interrupts: int
) -> None:
    config = campaign_config(optimizer, rounds, bench_trigger="at_end")
    samples = synthetic_rows(rows)
    requests = workspace / f"requests-{verb}"
    requests.mkdir()
    llm = FakeLLM(ByCallIdentity(requests, text_templates(config)))
    install_network(Router(llm, FakeBackend(samples, interrupts=interrupts)))
    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
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


async def run_controlled(
    workspace: Path, arm_names: list[str], *, rounds: int, rows: int, foreign: bool
) -> None:
    subjects = []
    if foreign:
        _, cid = await run_one(
            "potter", workspace, rounds=rounds, rows=rows, out=workspace / "foreign", framing=False
        )
        subjects.append(cid)
    arms = {}
    for name in arm_names:
        cycle, cid = await run_one(
            name,
            workspace,
            rounds=rounds,
            rows=rows,
            out=workspace / f"arm-{name}",
            purpose=Compare(arm=Arm(head_to_head_id="h2h", arm_key=name)),
        )
        arms[name] = (cycle, cid)
        subjects.append(cid)
    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
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
        read = {None if (s := r.close.ruler) is None else s.version for r in standing}
        if read != {record.ruler_version}:
            raise SystemExit(
                f"offline run: arm {name} read ruler versions {sorted(read, key=str)}, its head-to-head "
                f"froze {record.ruler_version}"
            )
        print(f"arm {name}: every election read the ruler its head-to-head froze")
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
    for name in sorted(optimizers.runtimes()):
        out[f"treatment:{name}"] = resolve_optimizer(name, {}, gate="off").treatment().digest
    return out


def named_home() -> Path:
    named = os.environ.get("PROMPTPOTTER_HOME")
    if not named:
        raise SystemExit("offline run: set PROMPTPOTTER_HOME to the directory it may write into")
    return Path(named.removeprefix("\\\\?\\")).expanduser().resolve()


def stands_green(home: Path, run: str, kept: LastGreen | None, key: str | None) -> bool:
    """Green in the gate's store under these flags, and this home still holds that run."""
    if kept is None or key is None or not kept.holds(run, key):
        return False
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


def moved_against(workspace: Path, earlier: Path) -> str:
    if not (earlier / "decisions.json").is_file():
        return f"no run in {earlier}"

    def held(home: Path) -> dict[str, object]:
        requests = {p.name: p.read_bytes() for p in sorted((home / "requests").iterdir())}
        return {"decisions": (home / "decisions.json").read_bytes(), "requests": requests}

    now, then = held(workspace), held(earlier)
    return ", ".join(f"{part} {'UNMOVED' if now[part] == then[part] else 'MOVED'}" for part in now)


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
    ap.add_argument("--terminal", choices=("new", "resume"), help=argparse.SUPPRESS)
    ap.add_argument("--interrupts", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.digests:
        print(json.dumps(digests(), indent=1))
        return 0
    if args.child:
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
            return 0
        if args.terminal:
            run_terminal(
                args.child,
                workspace,
                args.terminal,
                rounds=args.rounds,
                rows=args.rows,
                interrupts=args.interrupts,
            )
            return 0
        one = run_one(
            args.child,
            workspace,
            rounds=args.rounds,
            rows=args.rows,
            pause_after=args.pause_after,
            bench_trigger=args.bench_trigger,
        )
        print(asyncio.run(one)[0])
        return 0

    asked = [f"--{k}={v}" for k, v in sorted(vars(args).items()) if v != ap.get_default(k)]
    run = " ".join([OFFLINE_RUN, *(a for a in asked if not a.startswith("--against="))])
    kept = LastGreen.open()
    key = None if kept is None else kept.key(OFFLINE_RUN_READS)
    home = named_home()
    if args.against is None and stands_green(home, run, kept, key):
        print(f"offline run: unchanged since green -- {home}")
        return 0
    stamp: dict[str, object] = {"offline": True, "note": LABEL, "run": run}
    claim_home(home, stamp)
    end_children_with_this_process()
    complete_registries()
    arm_names = args.optimizer or ["potter", "capo"]
    names = list(CONTROLLED) if args.controlled else args.optimizer or sorted(optimizers.runtimes())
    stamp["workspaces"] = names
    (home / STAMP).write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
    # One workspace each: a shared archive and δ ruler let one optimizer's run move another's.
    child_args = ["--rows", str(args.rows), "--bench-trigger", args.bench_trigger]
    if args.controlled:
        child_args += [f"--optimizer={name}" for name in arm_names]
    t0 = time.monotonic()

    tasks = len(names) if args.controlled else 2 * len(names) + len(INTERRUPTED)
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
        return run_child(names[0], workspace, f"{verb}.log", TERMINAL_ROUNDS, *flags)

    children = {name: spawn(name, home / name) for name in names}
    stopped = (
        {}
        if args.controlled
        else {
            count: pool.submit(interrupted, terminal, home / names[0] / leg, count)
            for count, leg in INTERRUPTED.items()
        }
    )
    pause_after = min(1, args.rounds - 1)
    resumed = (
        {}
        if args.controlled
        else {
            name: spawn(name, home / name / RESUMED, f"--pause-after={pause_after}")
            for name in names
        }
    )
    failed = 0
    for name, child in children.items():
        workspace = home / name
        if rc := child.result():
            failed += 1
            print(f"{name}: FAILED (exit {rc}) -- {workspace / 'run.log'}")
            continue
        if args.controlled:
            continue
        ran = json.loads((workspace / "decisions.json").read_text(encoding="utf-8"))["run"]
        (cycle,) = (workspace / "projects").glob("*/campaigns/*/cycles/*")
        bench = ran["bench"]
        failed += not _graded(bench)
        lift = None if bench is None else bench["vs_origin"]["headline"]
        headline = (
            f"{lift['estimate']['value']:+.3f}"
            if lift is not None
            else "NO BENCH HEADLINE"
            if bench is None
            else f"none ({bench['vs_origin']['state']})"
        )
        print(f"{name}: {ran['stop_reason']}, bench lift {headline} -- {cycle}")
        if args.against is not None:
            print(
                f"{name}: against {args.against}: {moved_against(workspace, args.against / name)}"
            )
        if rc := resumed[name].result():
            failed += 1
            print(f"{name}: RESUME FAILED (exit {rc}) -- {workspace / RESUMED / 'run.log'}")
            continue
        again = decided(workspace / RESUMED, after=pause_after)
        same = decided(workspace, after=pause_after) == again
        # A pause at the origin's boundary replays round 0 on resume, which re-records it.
        failed += not _graded(again["run"]["bench"]) or (pause_after > 0 and not same)
        print(
            f"{name}: paused after round {pause_after} and resumed, decisions "
            f"{'UNMOVED' if same else 'MOVED'}"
        )
    for count, leg in stopped.items():
        ok, said = leg.result()
        failed += not ok
        print(f"{names[0]}: `new` under {count} SIGINT: {said if ok else 'FAILED -- ' + said}")
    if args.controlled and not failed:
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
    # Filed only where the sources read the same after the run as before it.
    # Filed under the flags it ran with: bare, that is the gate's check of the same name.
    if not failed and kept is not None and key == kept.key(OFFLINE_RUN_READS):
        kept.record({run: key})
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
