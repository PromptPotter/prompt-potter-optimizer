"""The one supported OFFLINE run: a real campaign per installed optimizer on ``justlogic-d234``,
every network edge answered in-process, zero spend. What it is for, what it pins and how to read
it: ``docs/developer/offline-run.md``.

    PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py [--optimizer NAME ...]
    python scripts/offline_run.py --digests
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import io
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, get_args
from urllib.parse import urlsplit

import httpx
import yaml
from gate import (
    OFFLINE_CHILD_MB,
    OFFLINE_CHILDREN,
    end_children_with_this_process,
    memory_budget_mb,
)
from kept_verdict import OFFLINE_RUN, OFFLINE_RUN_READS, KeptVerdicts

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    load_campaign_config,
)
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
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
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.config.settings import Settings
from promptpotter.domain.bench import BenchScore, BenchTrigger
from promptpotter.domain.campaign import ArmRequest
from promptpotter.domain.command_kinds import SkipSearchpointPayload
from promptpotter.domain.launch_limits import LaunchLimits, RunMode
from promptpotter.domain.phases import RunPhase, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.archive_queries import (
    list_populations,
    load_population,
    scope_memory_to_own_answers,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_answers
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.identity import default_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.paired_reading import PairedReading
    from promptpotter.infrastructure.store.stores import Stores

DATASET = "justlogic-d234"
BACKEND_URL = "http://127.0.0.1:8000"
STAMP = "offline-run.json"
CONTROLLED = ("controlled", "controlled-bare")
RESUMED = "resumed"
INTERRUPTED = {1: "interrupted", 2: "interrupted-twice"}
# Past the origin's panel, inside the first round's walk.
INTERRUPT_AT_CELL = 25
RESUME_APPENDS = ("phases", "run_phases", "candidates_minted", "rulers")
NOT_A_KEY = "offline-run-not-a-key"
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
LABEL = "OFFLINE — fake optimizer LLM and fake backend; every number is synthetic"
LABELS = ("TRUE", "FALSE", "Uncertain")

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
        "levi_paradigm_shift": {"config": {"interval": 4, "n_diverse_seeds": 2}},
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


def h(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("\x1f".join(map(str, parts)).encode()).digest()[:8], "big")


def long_path(p: Path) -> str:
    # A cycle tree nests past Windows MAX_PATH=260 under a deep home; the prefix lifts it.
    s = str(p.resolve())
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\?\\") else s


_MARKER = re.compile(r"Variant ([\w.]+):")


def _variant(tag: str, rng: random.Random) -> str:
    return f"Variant {tag}: " + " ".join(rng.sample(WORDS, 18)) + "."


class SchemaFiller:
    def __init__(self, schema: dict[str, Any], *, node: str, idx: int) -> None:
        self.node = node
        self.idx = idx
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
            text = f"{'.'.join(path) or 'value'} {self.node} {self.idx}".ljust(
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


class FakeLLM:
    """An answer follows its node's CALL ORDINAL, never the prompt's wording."""

    def __init__(self, capture: Path, templates: dict[str, str]) -> None:
        self.capture = capture
        self.templates = templates
        self.prefixes = {node: template.split("{{")[0] for node, template in templates.items()}
        self.calls: dict[str, int] = {}
        self.n = 0

    def _text_node(self, prompt: str) -> str:
        opened = [node for node, prefix in self.prefixes.items() if prompt.startswith(prefix)]
        if not opened:
            raise KeyError(f"offline run: no llm node's template opens {prompt[:80]!r}")
        return max(opened, key=lambda node: len(self.prefixes[node]))

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        prompt = "\n".join(str(m["content"]) for m in body["messages"])
        schema = body.get("response_format", {}).get("json_schema")
        node = schema["name"] if schema else self._text_node(prompt)
        idx = self.calls.get(node, 0)
        self.calls[node] = idx + 1
        if schema:
            filler = SchemaFiller(schema["schema"], node=node, idx=idx)
            obj = filler.fill(schema["schema"], ())
            content = json.dumps(self._shape(node, idx, obj, filler, schema["schema"], prompt))
        else:
            content = self._text(node, idx, prompt)
        self.n += 1
        (self.capture / f"{self.n:04d}_{node}_{idx}.json").write_text(
            json.dumps(
                {"request": body, "response": content}, indent=1, sort_keys=True, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        return {
            "id": f"offline-{node}-{idx}",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "provider": "OfflineHost",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "cost": 0.0,
            },
        }

    def _text(self, node: str, idx: int, prompt: str) -> str:
        rng = random.Random(f"{node}:{idx}")
        fresh = _variant(f"{node}.{idx}", rng)
        template = self.templates[node]
        if "<prompt>" in template:
            # Rephrasing the individual's own instruction keeps its parent's marker, so its skill.
            parents = _MARKER.findall(prompt) if "{{instruction}}" in template else []
            return f"<prompt>{_variant(parents[-1], rng) if parents else fresh}</prompt>"
        if "```" in template:
            return f"```\n{fresh}\n```"
        if "array" in template:
            return json.dumps([_variant(f"{node}.{idx}.{i}", rng) for i in range(LISTED)])
        raise KeyError(
            f"offline run: llm node {node!r}'s template asks for no form the fake writes"
        )

    def _shape(
        self,
        node: str,
        idx: int,
        obj: dict[str, Any],
        filler: SchemaFiller,
        schema: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        if node.startswith("L1Generate"):
            item = filler.resolve(filler.resolve(schema)["properties"]["variants"])
            obj["variants"] = [
                self._variant_edit(idx, i, filler, filler.resolve(item["items"]), prompt)
                for i in range(item.get("minItems") or item["maxItems"])
            ]
        elif node.startswith("L1Critique"):
            obj["failure_highlights"] = [
                f"critique {idx}: sample chain broke at a negation; predicted vs GT differ",
                f"critique {idx}: quantifier scope misread in a conditional premise",
            ]
            obj["priority_fix"] = f"instruction: track every negation explicitly (critique {idx})"
            obj["suggested_axes"] = ["instruction", "thinking_style"]
        elif node.startswith("L2Context"):
            obj["axis_targeted"] = "instruction"
            obj["rationale"] = f"L2 fire {idx}: L1 stalled on the instruction axis."
            obj.pop("fork_proposal", None)
            obj.pop("terminate_proposal", None)
        elif node.startswith("L3Plan"):
            obj["plan"] = f"Replan {idx}: L1 rewrites the instruction to track negation explicitly."
            obj["note"] = f"l3 note {idx}"
            obj["rationale"] = f"L2 stalled twice (replan {idx})"
            obj.pop("fork_proposal", None)
            obj.pop("terminate_proposal", None)
        return obj

    def _variant_edit(
        self, idx: int, i: int, filler: SchemaFiller, item: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        v: dict[str, Any] = filler.fill(item, ("variants", str(i)))
        grounding = filler.resolve(item["properties"]["evidence_grounding"])
        cited = filler.resolve(grounding["properties"]["field"])["enum"]
        # A citation must quote the prompt; the offset is a function of the call, not the text.
        start = h("cite", idx, i) % max(len(prompt) - 60, 1)
        v["evidence_grounding"] = {
            "field": cited[i % len(cited)],
            "citation": prompt[start : start + 40],
        }
        v["prompt_fields_updates"] = {
            "instruction": _variant(f"g{idx}v{i}", random.Random(f"l1:{idx}:{i}"))
        }
        v["targets_cluster"] = f"cluster-{idx}-{i}"
        v["changes_description"] = f"Rewrite the instruction (generation {idx}, variant {i})."
        return v


LLM_ONLY_NODE: dict[str, Any] = {
    "type": "llm",
    "runtime": "backend",
    "node_role": "ranker",
    "description": "Generic LLM call (offline TermNorm).",
    "prompt_info": {"family": "llm_only", "template_variables": [], "description": "offline"},
    "config": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "temperature": 0.0,
        "max_tokens": 8192,
        "input_bytes": 31000,
        "max_price": None,
        "reasoning_effort": "medium",
        "response_format": "text",
    },
    "optimizer": {
        "param_keys": [
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "reasoning_effort",
            "persona",
            "task_intent",
            "problem_description",
            "instruction",
            "thinking_style",
            "answer_format",
        ],
        "param_allowed_values": {
            "provider": ["openai", "groq", "openrouter", "anthropic"],
            "reasoning_effort": ["none", "default", "low", "medium", "high"],
        },
        "observation_name": "llm_only",
        "observation_mappings": [
            {"pipeline_key": "final_ranking", "output_field": "final_ranking", "is_llm": True}
        ],
    },
    "spend_bound": {"kind": "llm", "attempts": 3, "input_bytes": 31000, "max_tokens": 8192},
}


class FakeBackend:
    # P(correct) = k/10 by marker. Potter's script: round-1 win, two stalls (L2, L3), round-4 win.
    SKILL: ClassVar[dict[str | None, int]] = {
        None: 3,
        "g0v0": 1, "g0v1": 7, "g0v2": 2, "g1v0": 2, "g1v1": 4, "g1v2": 3,
        "g2v0": 3, "g2v1": 1, "g2v2": 5, "g3v0": 9, "g3v1": 2, "g3v2": 1,
    }  # fmt: skip

    def __init__(self, samples: list[Sample], *, interrupts: int = 0) -> None:
        self.truth = {s.query: s.ground_truth for s in samples}
        self.cells = 0
        self.interrupts = interrupts

    def matches(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.cells += 1
        if self.cells == INTERRUPT_AT_CELL:
            for _ in range(self.interrupts):
                signal.raise_signal(signal.SIGINT)
        query = payload["query"]
        node_cfg = payload["node_config"]["llm_only"]
        cfg_key = json.dumps(node_cfg, sort_keys=True, ensure_ascii=False)
        prompt = str(node_cfg["prompt"])
        marks = _MARKER.findall(prompt)
        tag = marks[-1] if marks else None
        k = self.SKILL.get(tag, 1 + h("skill", tag) % 9)
        gt = self.truth[query]
        if h("cell", cfg_key, query) % 10 < k:
            label = gt
        else:
            wrong = [lab for lab in LABELS if lab != gt]
            label = wrong[h("wrong", cfg_key, query) % len(wrong)]
        return {
            "status": "success",
            "message": "ok",
            "data": {
                "final_ranking": [{"candidate": label, "relevance_score": 1.0}],
                "reasoning_trace": f"offline trace for k={k}",
                "diagnostics": {"step_statuses": {"llm_only": "success"}, "warnings": []},
                "step_timings": {"llm_only": 1.0},
                "step_tokens": {
                    "llm_only": {
                        "input": 400 + len(prompt) // 4,
                        "output": 200 + h("out", cfg_key) % 400,
                        "cost_usd": 0.0,
                        "model": str(node_cfg["model"]),
                        "served_by": "OfflineHost",
                        "finish_reason": "stop",
                        "reasoning": 0,
                    }
                },
                "total_time": 1.0,
                "terminal_node": "llm_only",
            },
        }


OPENROUTER_ENDPOINTS = {
    "data": {
        "endpoints": [
            {
                "tag": "offlinehost",
                "pricing": {"prompt": "0.0000001", "completion": "0.0000004", "request": "0"},
                "max_completion_tokens": 32000,
                "context_length": 131072,
            }
        ]
    }
}


class Router:
    def __init__(self, llm: FakeLLM, backend: FakeBackend) -> None:
        self.llm = llm
        self.backend = backend
        self.unrouted: set[str] = set()

    def route(self, method: str, url: str, body: bytes) -> tuple[int, dict[str, Any]]:
        u = urlsplit(url)
        if u.netloc == "127.0.0.1:8000":
            match u.path:
                case "/pipeline":
                    return 200, {
                        "status": "success",
                        "message": "Pipeline configuration",
                        "data": {
                            "name": "TermNorm",
                            "version": "offline",
                            "nodes": {"llm_only": LLM_ONLY_NODE},
                            "pipelines": {"llm_only": ["llm_only"]},
                        },
                    }
                case "/status":
                    return 200, {"status": "ok"}
                case "/sessions":
                    return 200, {"status": "success", "data": {"terms_count": 0}}
                case "/matches":
                    return 200, self.backend.matches(json.loads(body))
        if u.path.endswith("/chat/completions"):
            return 200, self.llm.answer(json.loads(body))
        self.unrouted.add(f"{method} {u.scheme}://{u.netloc}{u.path}")
        return 404, {"error": "offline run: no route"}


class _Body(io.BytesIO):
    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _refuse_resolve(*_: object, **__: object) -> Any:
    raise OSError("offline run: no name resolution, so no request leaves this process")


def install_network(router: Router) -> None:
    def respond(request: httpx.Request, body: bytes) -> httpx.Response:
        status, payload = router.route(request.method, str(request.url), body)
        return httpx.Response(
            status,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            request=request,
        )

    async def handle_async_request(_: Any, request: httpx.Request) -> httpx.Response:
        return respond(request, await request.aread())

    def handle_request(_: Any, request: httpx.Request) -> httpx.Response:
        return respond(request, request.read())

    def urlopen(url: Any, *_: Any, **__: Any) -> Any:
        target = url if isinstance(url, str) else url.full_url
        if "openrouter.ai/api/v1/models/" in target and target.endswith("/endpoints"):
            return _Body(json.dumps(OPENROUTER_ENDPOINTS).encode())
        router.unrouted.add(f"urlopen {target}")
        raise urllib.error.URLError("offline run")

    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign,assignment]
    httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign,assignment]
    urllib.request.urlopen = urlopen
    # Every client above is answered before it resolves a name; one that is not fails here.
    socket.getaddrinfo = _refuse_resolve


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
                ground_truth=LABELS[h("label", i) % len(LABELS)],
            )
        )
    return rows


def campaign_config(
    optimizer: str, rounds: int, *, bench_trigger: BenchTrigger, pinned: bool = False
) -> CampaignConfig:
    """*pinned* seeds every optimizer's draws alike, which arms of one head-to-head must share."""
    raw = dict(
        read_campaign_config_file(dataset_campaign_path(benchmark_datasets_root() / DATASET))
    )
    raw.update(BENCH, bench_trigger=bench_trigger)
    opt = raw["optimization"]
    # The template's node overlay is written for the optimizer it selects, and only that one.
    templated = opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
    opt.update(max_rounds=rounds, ceiling={"usd": 1000.0}, origin_gate="off", optimizer=optimizer)
    if optimizer != templated:
        opt["nodes"] = SCALED.get(optimizer, {})
    if optimizer != templated or pinned:
        # Its draws follow the campaign's id unless a clamp seeds them, and every run mints an id.
        opt["determinism"] = {"seed": 0}
    sampler = select_optimizer(load_campaign_config(raw).optimization).sampler
    if sampler.size_knob is not None:
        nodes = opt.setdefault("nodes", {})
        block = nodes[sampler.name] = dict(nodes.get(sampler.name) or {})
        block["config"] = {**(block.get("config") or {}), sampler.size_knob: ROUND_CELLS}
    return load_campaign_config(raw)


def text_templates(config: CampaignConfig) -> dict[str, str]:
    selected = select_optimizer(config.optimization)
    return {
        node: running_prompt(node, selected.node_config(node), selected.document).render()
        for node in selected.llm_nodes
    }


async def rebound_session(stores: Stores, hop: CycleHop) -> tuple[Session, CampaignConfig]:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    assert campaign is not None
    session = await open_session(
        DATASET, backend_url=BACKEND_URL, backend_id=DATASET, stores=stores
    )
    config = resolve_campaign_config(stores, campaign, hop)
    configure_and_apply_pipeline(session, config)
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id
    return session, config


async def run_one(
    optimizer: str,
    workspace: Path,
    *,
    rounds: int,
    rows: int,
    out: Path | None = None,
    arm: ArmRequest | None = None,
    framing: bool = True,
    pause_after: int | None = None,
    bench_trigger: BenchTrigger = "at_end",
) -> tuple[Path, str]:
    """The archive is the workspace's, never *out*'s: campaigns in one process share it."""
    out = workspace if out is None else out
    (out / "requests").mkdir(parents=True)
    config = campaign_config(optimizer, rounds, bench_trigger=bench_trigger, pinned=arm is not None)
    if not framing:
        config = config.model_copy(update={"task_framing": "off"})
    samples = synthetic_rows(rows)
    llm = FakeLLM(out / "requests", text_templates(config))
    backend = FakeBackend(samples)
    router = Router(llm, backend)
    install_network(router)

    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    stores.tenant_datasets.save_benchmark_rows(DATASET, samples)
    session = await open_session(
        DATASET, backend_url=BACKEND_URL, backend_id=DATASET, stores=stores
    )
    configure_and_apply_pipeline(session, config)
    result = await run_campaign(
        session,
        list(session.samples),
        config,
        readout_sink=print,
        limits=LaunchLimits(step_rounds=pause_after),
        mode=RunMode(),
        arm=arm,
    )
    await session.backend_client.aclose()
    if pause_after is not None:
        if result.stop_reason != StopReason.PAUSED:
            raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}, never paused")
        # The resume reads its rounds off the ledger and the archive: their checkouts are gone.
        shutil.rmtree(CycleLayout(stores.campaigns.cycle_dir(session.hop)).rounds)
        session, config = await rebound_session(stores, session.hop)
        result = await run_campaign(
            session,
            list(session.samples),
            config,
            readout_sink=print,
            limits=LaunchLimits(),
            mode=RunMode(),
        )
        await session.backend_client.aclose()
    if stop_reason_outcome(result.stop_reason).exit_code:
        raise SystemExit(f"offline run: {optimizer} ended {result.stop_reason}")
    searched = {key for node in session.pipeline_schema.declared_nodes for key in node.param_keys}
    if arm is not None and (beside := sorted(searched - set(PROMPT_STRING_FIELDS))):
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
    llm = FakeLLM(requests, text_templates(config))
    install_network(Router(llm, FakeBackend(samples, interrupts=interrupts)))
    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    stores.tenant_datasets.save_benchmark_rows(DATASET, samples)
    declared = workspace / "campaign.yaml"
    declared.write_text(
        yaml.safe_dump({"campaign_config": config.model_dump(mode="json", exclude_unset=True)}),
        encoding="utf-8",
    )
    new = ["new", DATASET, f"--config={declared}", f"--backend-url={BACKEND_URL}"]
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
    if not mint["campaign_config"] or mint["backend_url"] != BACKEND_URL:
        return False, f"the mint-campaign record carries no config or backend: {sorted(mint)}"
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
            arm=ArmRequest(head_to_head_id="h2h", arm_key=name),
        )
        arms[name] = (cycle, cid)
        subjects.append(cid)
    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    cycle, cid = arms[arm_names[0]]
    try:
        await CommandDispatcher(stores).dispatch_cycle_command(
            CommandCall(SkipSearchpointPayload(campaign_id=cid, cycle_id=cycle.name), "skip-arm"),
            expected_version=None,
        )
    except ConflictError as exc:
        print(f"skip on an arm: {exc.http_status} {exc}")
    else:
        raise SystemExit("offline run: a skip on a controlled arm was applied")

    def filed(campaign_id: str) -> set[str]:
        campaign = stores.campaigns.load_campaign(campaign_id)
        assert campaign is not None
        return scan_ledger_answers(
            CycleLayout(stores.campaigns.cycle_dir(hop)).ledger
            for hop in stores.campaigns.line(campaign.root_hop)
        )

    def memory(campaign_id: str) -> set[str]:
        scope_memory_to_own_answers(filed(campaign_id))
        return {
            answer.answer
            for entry in list_populations(stores, dataset_name=DATASET)
            for answer in load_population(stores, entry)
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
    "rendered_prompt_hash", "prompt_fields_id", "ruler_id", "anchor_id", "prompt_hashes",
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
_CAMPAIGN_ID = re.compile(re.escape(DATASET) + r"__[0-9a-f]{6}")
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


def _id_map(ledger: list[dict[str, Any]], rounds: list[dict[str, Any]]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for r in ledger:
        if r.get("record_type") == "candidate_minted" and r.get("label"):
            ids.setdefault(r["candidate_id"], r["label"])
    for d in rounds:
        for c in d.get("candidate_scores") or []:
            if c.get("candidate_id") and c.get("label"):
                ids.setdefault(c["candidate_id"], c["label"])
    for r in ledger:
        if r.get("record_type") == "candidate_minted":
            for pid in (r.get("lineage") or {}).get("parent_ids") or []:
                ids.setdefault(pid, f"parent@R{r.get('round')}")
    for d in rounds:
        for c in d.get("candidate_scores") or []:
            if reference := ((c.get("vs_reference") or {}).get("a") or {}).get("address"):
                ids.setdefault(reference["individual_id"], f"parent@R{d.get('round')}")
    return ids


def _cells(rows: Any) -> list[dict[str, Any]]:
    keys = ("sample_id", "predicted", "ground_truth", "fitness", "objective", "cached")
    out = [{k: r.get(k) for k in (*keys, "error_category")} for r in rows or []]
    return sorted(out, key=lambda c: str(c["sample_id"]))


CANDIDATE_KEYS = (
    "label", "changes_description", "prompt_fields", "resolved_pipeline_params",
    "pipeline_overlay", "accuracy", "composite_fitness", "total", "evaluators", "scored_samples",
    "expected_samples", "cached_samples", "input_tokens", "output_tokens", "outcome",
    "validation_failures", "runtime_failures", "elimination_context", "degradation_context",
    "vs_reference", "theta", "theta_se", "theta_caveat", "mean_fitness_ci_lo",
    "mean_fitness_ci_hi",
)  # fmt: skip
ROUND_KEYS = (
    "round", "label", "accuracy", "composite_fitness", "total", "improved", "verdict_reason",
    "degraded_samples", "not_attempted", "unscored", "deprecated", "candidates_scored",
    "electable_count", "status", "prompt_fields", "pipeline_params", "evaluators", "overlap",
    "overlap_results", "selected_labels", "optimizer_state",
)  # fmt: skip


def _round(d: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {k: d.get(k) for k in ROUND_KEYS}
    ability = d.get("ability") or {}
    out["ability"] = {k: ability.get(k) for k in ("theta", "se", "ruler_n", "caveat")}
    health = d.get("health") or {}
    out["health"] = {k: health.get(k) for k in ("grade", "cause", "suggested_action")}
    readings = [r["reading"] for r in d.get("scoreboard") or []]
    board = {r["arm"]["candidate_id"] or r["arm"]["label"]: r for r in readings}
    cands = []
    for c in d.get("candidate_scores") or []:
        row = board.get(c.get("candidate_id")) or board.get(c.get("label"))
        cands.append(
            {
                **{k: c.get(k) for k in CANDIDATE_KEYS},
                "is_selected": None if row is None else row["election"]["selected"],
            }
        )
    out["candidates"] = sorted(cands, key=lambda c: str(c["label"]))
    out["scoreboard_order"] = [
        ids.get(r["arm"]["candidate_id"], r["arm"]["label"]) for r in readings
    ]
    out["parent_cells"] = _cells(d.get("results"))
    out["arm_cells"] = {
        ids.get(k, k): _cells(v) for k, v in (d.get("all_candidate_results") or {}).items()
    }
    out["reference_cells"] = {
        ids.get(k, k): _cells(v) for k, v in (d.get("reference_results") or {}).items()
    }
    return out


LEDGER_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "decision": ("decisions", ("round", "kind", "outcome", "inputs_ref", "data")),
    "election": ("elections", ("round", "selected_labels", "fit")),
    "candidate_minted": (
        "candidates_minted",
        ("round", "idx", "label", "lineage"),
    ),
    "phase": ("phases", ("round", "phase", "event")),
    "round_entered": ("rounds_entered", ("round",)),
    "round_closed": (
        "rounds_closed",
        ("round", "label", "accuracy", "composite_fitness", "improved", "electable_count"),
    ),
    "run_phase": ("run_phases", ("run_phase", "stop_reason")),
    "llm_call": ("optimizer_calls", ("round", "node", "candidate_idx")),
    "round_warning": ("round_warnings", ("round", "kind", "severity", "detail")),
    "ruler": ("rulers", ("round",)),
}
RULER_KEYS = ("delta", "discrimination", "mu_delta", "sigma_delta", "sigma_theta")


def _as_sets(o: Any) -> Any:
    # A decision lists the arms it raced in the order they ARRIVED (CAPO's `raced_against`).
    if isinstance(o, dict):
        return {k: _as_sets(v) for k, v in o.items()}
    if isinstance(o, list) and all(isinstance(v, str) for v in o):
        return sorted(o)
    return [_as_sets(v) for v in o] if isinstance(o, list) else o


def _ledger(ledger: list[dict[str, Any]], canon: Canon) -> dict[str, Any]:
    out: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in LEDGER_KEYS.values()}
    for r in ledger:
        if (kind := r.get("record_type")) not in LEDGER_KEYS:
            continue
        name, keys = LEDGER_KEYS[kind]
        entry = {k: r.get(k) for k in keys}
        if kind == "llm_call":
            entry["repairs"] = len((r.get("payload") or {}).get("schema_repair_errors") or [])
        elif kind == "ruler":
            entry.update({k: (r.get("ruler") or {}).get(k) for k in RULER_KEYS})
        out[name].append(_as_sets(canon(entry)) if kind == "decision" else canon(entry))
    # Concurrent arms append in arrival order; the SET per round is what a comparison reads.
    for name in ("decisions", "round_warnings"):
        out[name].sort(key=lambda x: (x.get("round") or 0, json.dumps(x, sort_keys=True)))
    out["candidates_minted"].sort(key=lambda x: (x.get("round") or 0, x.get("idx") or 0))
    return out


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def extract(cycle: Path, bench: BenchScore | None) -> dict[str, Any]:
    rounds = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((cycle / "rounds").glob("*.json"))
    ]
    ledger = _jsonl(cycle / ".runtime" / "ledger.jsonl")
    folded = read_cycle_index(cycle)
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
        out[f"treatment:{name}"] = resolve_optimizer(name, {}).treatment().digest
    return out


def named_home() -> Path:
    named = os.environ.get("PROMPTPOTTER_HOME")
    if not named:
        raise SystemExit("offline run: set PROMPTPOTTER_HOME to the directory it may write into")
    return Path(named.removeprefix("\\\\?\\")).expanduser().resolve()


def stands_green(home: Path, run: str, key: str | None) -> bool:
    try:
        stamp = json.loads((home / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return key is not None and (stamp.get("run"), stamp.get("green")) == (run, key)


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
    env.update(dict.fromkeys(("LANGFUSE_ENABLED", "MLFLOW_ENABLED"), "false"))
    env.update(PROMPTPOTTER_HOME=long_path(home), PYTHONUTF8="1")
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
    ap.add_argument("--rounds", type=int, default=4, help="max_rounds per campaign")
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
    kept = KeptVerdicts.open()
    key = None if kept is None else kept.key(OFFLINE_RUN_READS)
    home = named_home()
    if args.against is None and stands_green(home, run, key):
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
    child_args = [
        "--rounds", str(args.rounds), "--rows", str(args.rows),
        "--bench-trigger", args.bench_trigger,
    ]  # fmt: skip
    if args.controlled:
        child_args += [f"--optimizer={name}" for name in arm_names]
    t0 = time.monotonic()

    width = min(OFFLINE_CHILDREN, memory_budget_mb() // OFFLINE_CHILD_MB)
    pool = ThreadPoolExecutor(max(1, width))

    def run_child(name: str, workspace: Path, log: str, *extra: str) -> int:
        with (workspace / log).open("w", encoding="utf-8") as out:
            return subprocess.run(
                [sys.executable, __file__, "--child", name, *child_args, *extra],
                cwd=workspace,
                env=child_env(workspace),
                stdout=out,
                stderr=subprocess.STDOUT,
            ).returncode

    def spawn(name: str, workspace: Path, *extra: str) -> Future[int]:
        workspace.mkdir()
        return pool.submit(run_child, name, workspace, "run.log", *extra)

    def terminal(workspace: Path, verb: str, interrupts: int) -> int:
        flags = (f"--terminal={verb}", f"--interrupts={interrupts}")
        return run_child(names[0], workspace, f"{verb}.log", *flags)

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
        run = json.loads((workspace / "decisions.json").read_text(encoding="utf-8"))["run"]
        (cycle,) = (workspace / "projects").glob("*/campaigns/*/cycles/*")
        bench = run["bench"]
        failed += not _graded(bench)
        lift = None if bench is None else bench["vs_origin"]["headline"]
        headline = (
            f"{lift['estimate']['value']:+.3f}"
            if lift is not None
            else "NO BENCH HEADLINE"
            if bench is None
            else f"none ({bench['vs_origin']['state']})"
        )
        print(f"{name}: {run['stop_reason']}, bench lift {headline} -- {cycle}")
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
            same = (beside / arm / "decisions.json").read_bytes() == (
                bare / arm / "decisions.json"
            ).read_bytes()
            failed += not same
            print(f"{arm}: decisions {'UNMOVED' if same else 'MOVED'} by the foreign campaign")
        lines = (beside / "run.log").read_text(encoding="utf-8").splitlines()
        print("\n".join(line for line in lines if line.startswith(("skip on", "arm ", "head-to-"))))
    print(f"wall clock {time.monotonic() - t0:.0f}s")
    # Filed only where the sources read the same after the run as before it.
    if not failed and kept is not None and key == kept.key(OFFLINE_RUN_READS):
        (home / STAMP).write_text(
            json.dumps({**stamp, "green": key}, indent=1) + "\n", encoding="utf-8"
        )
        if run == OFFLINE_RUN:  # the gate's check of the same name stands on this run too
            kept.record({OFFLINE_RUN: key})
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
