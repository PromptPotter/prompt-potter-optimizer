"""The one supported OFFLINE run: a real campaign per installed optimizer on ``justlogic-d234``,
every network edge answered in-process, zero spend. What it is for, what it pins and how to read
it: ``docs/developer/offline-run.md``.

    PROMPTPOTTER_HOME=<dir> python scripts/offline_run.py [--optimizer NAME ...]
    python scripts/offline_run.py --digests
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    load_campaign_config,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.embedded_run import open_session, run_campaign
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.optimizer_manifest import running_prompt, select_optimizer
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.runner.entry import RunMode
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.config.settings import Settings
from promptpotter.connectors.promptpotter import measurement_modules
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.terminal.live.display import LiveDisplay
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.identity import default_identity

DATASET = "justlogic-d234"
BACKEND_URL = "http://127.0.0.1:8000"
STAMP = "offline-run.json"
NOT_A_KEY = "offline-run-not-a-key"
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
LABEL = "OFFLINE — fake optimizer LLM and fake backend; every number is synthetic"
LABELS = ("TRUE", "FALSE", "Uncertain")

# Bench knobs every optimizer runs under; each optimizer's own knobs are its manifest's.
BENCH: dict[str, Any] = {
    "sp_budget_origin": 14,
    "dataset_split": {"bench": 10, "demo": 10},
}
# A round's panel, where the optimizer's sampler sizes it with one knob (`Sampler.size_knob`).
ROUND_CELLS = 20
# Scale-downs of a paper configuration to a bank of a few hundred rows, so a race cuts and an
# archive fills within a few rounds. An optimizer absent here runs as its manifest declares.
SCALED: dict[str, dict[str, dict[str, Any]]] = {
    "capo": {
        "blocks": {"config": {"block_size": 5, "max_blocks": 4}},
        "paired_t": {"config": {"alpha": 0.2}},
        "population": {"config": {"size": 4}},
        "capo_crossover": {"config": {"crossovers": 2}},
        "few_shot": {"config": {"k_max": 2}},
    },
    "levi": {
        "proxy_css": {"config": {"size": 10}},
        "levi_paradigm_shift": {"config": {"interval": 4, "n_diverse_seeds": 2}},
        "map_elites": {"config": {"centroids": 8, "cvt_samples": 400}},
    },
}
# A listed answer carries this many prompts.
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


# ---------------------------------------------------------------------------------------------
# Fake optimizer LLM (OpenAI chat-completions wire, any host)
# ---------------------------------------------------------------------------------------------

_MARKER = re.compile(r"Variant ([\w.]+):")


def _variant(tag: str, rng: random.Random) -> str:
    return f"Variant {tag}: " + " ".join(rng.sample(WORDS, 18)) + "."


class SchemaFiller:
    """A deterministic MINIMAL instance of a JSON schema: required properties only, first enum
    value, minItems items."""

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
    """An answer is a function of its node's CALL ORDINAL (and the marker a rephrase keeps), never
    of the prompt's wording, so a change to how a prompt renders moves the request capture and not
    what the run decides. A structured call names its node in its response schema; a paper
    preset's text call is the llm node whose manifest template its prompt opens with, answered in
    the form that template asks for."""

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
            # A template rephrasing the individual's own instruction keeps its parent's marker,
            # so the rephrase keeps the parent's skill.
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


# ---------------------------------------------------------------------------------------------
# Fake TermNorm backend
# ---------------------------------------------------------------------------------------------

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
    # P(correct) = k/10 off the prompt's marker. Potter's scripted: a round-1 win, two stalls that
    # fire L2 and L3, a round-4 win; any other marker draws k from its own name.
    SKILL: ClassVar[dict[str | None, int]] = {
        None: 3,
        "g0v0": 1, "g0v1": 7, "g0v2": 2, "g1v0": 2, "g1v1": 4, "g1v2": 3,
        "g2v0": 3, "g2v1": 1, "g2v2": 5, "g3v0": 9, "g3v1": 2, "g3v2": 1,
    }  # fmt: skip

    def __init__(self, samples: list[Sample]) -> None:
        self.truth = {s.query: s.ground_truth for s in samples}
        self.cells = 0

    def matches(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.cells += 1
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


# ---------------------------------------------------------------------------------------------
# The network, answered in-process
# ---------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------
# One optimizer's campaign (a child process: the patches and the home are process-wide)
# ---------------------------------------------------------------------------------------------


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


def campaign_config(optimizer: str, rounds: int) -> CampaignConfig:
    raw = dict(
        read_campaign_config_file(dataset_campaign_path(benchmark_datasets_root() / DATASET))
    )
    raw.update(BENCH)
    opt = raw["optimization"]
    # The template's node overlay is written for the optimizer it selects, and only that one.
    templated = opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
    opt.update(max_rounds=rounds, spend_budget_usd=1000.0, origin_gate="off", optimizer=optimizer)
    if optimizer != templated:
        opt["nodes"] = SCALED.get(optimizer, {})
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


async def run_one(optimizer: str, workspace: Path, *, rounds: int, rows: int) -> Path:
    (workspace / "requests").mkdir()
    config = campaign_config(optimizer, rounds)
    samples = synthetic_rows(rows)
    llm = FakeLLM(workspace / "requests", text_templates(config))
    backend = FakeBackend(samples)
    router = Router(llm, backend)
    install_network(router)

    stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    stores.tenant_datasets.save_benchmark_rows(DATASET, samples)
    session = await open_session(
        DATASET, backend_url=BACKEND_URL, backend_id=DATASET, stores=stores, on_status=print
    )
    configure_and_apply_pipeline(session, config, log=print)
    result = await run_campaign(
        session,
        list(session.samples),
        config,
        display=LiveDisplay.for_campaign(session, config),
        limits=LaunchLimits(),
        mode=RunMode(),
    )
    await session.backend_client.aclose()
    stores.campaigns.update_campaign(session.campaign_id, {"label": LABEL})
    cycle = Path(stores.campaigns.cycle_dir(session.hop))
    dashboard = json.loads((cycle / "dashboard.json").read_text(encoding="utf-8"))
    if billed := SpendRollup.model_validate(dashboard["spend"]).total_used_usd:
        raise SystemExit(f"offline run billed ${billed}: a fake answered with a cost")
    decisions = extract(cycle)
    decisions["harness"] = {
        "stop_reason": str(result.stop_reason),
        "unrouted": sorted(router.unrouted),
        "llm_calls": dict(sorted(llm.calls.items())),
        "backend_cells": backend.cells,
    }
    (workspace / "decisions.json").write_text(
        json.dumps(decisions, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return cycle


# ---------------------------------------------------------------------------------------------
# decisions.json — every DECISION a run made, run-independent
# ---------------------------------------------------------------------------------------------
# Read as dicts with `.get`, deliberately: a dump taken before a reshape must still load after
# it, so a moved field reads as a diff rather than a crash.

FLOAT_DP = 6
DROP_KEYS = {
    "timestamp", "created_at", "updated_at", "started_at", "finished_at", "at_offset", "offset",
    "wall_clock", "duration_s", "elapsed_s", "call_id", "run_id", "sp_hash", "content_hash",
    "rendered_prompt_hash", "prompt_fields_id", "ruler_id", "anchor_id", "prompt_hashes",
    "optimizer_prompt_hashes", "version", "parent_session_id", "session_id", "sample_key",
    "backend_url", "latency", "latency_ms", "total_time", "step_timings",
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
    """Ids to candidate labels; timestamps, offsets, paths and hashes out; floats rounded."""

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
    # A search point a LAYER re-mints without a label (L2/L3 rewrap the parent the next round
    # mutates) is named by its lineage: "<parent label>~<source node>".
    for _ in range(3):
        for d in rounds:
            lin = (d.get("opt_sp") or {}).get("lineage") or {}
            oid = lin.get("id")
            if not isinstance(oid, str) or oid in ids:
                continue
            parents = lin.get("parent_ids") or []
            plabel = next((ids[p] for p in parents if p in ids), None)
            if plabel is None and parents:
                continue
            ids[oid] = f"{plabel or 'root'}~{str(lin.get('source') or '').split(':')[-1] or '?'}"
    # A rewrapped parent no persisted document names is named by the round it parents.
    for r in ledger:
        if r.get("record_type") == "candidate_minted":
            for pid in r.get("parent_ids") or []:
                ids.setdefault(pid, f"parent@R{r.get('round')}")
    for d in rounds:
        for c in d.get("candidate_scores") or []:
            if c.get("reference_id"):
                ids.setdefault(c["reference_id"], f"parent@R{d.get('round')}")
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
    "reference_id", "reference_accuracy", "reference_composite", "reference_lift",
    "reference_lift_ci_lo", "reference_lift_ci_hi", "theta", "theta_se", "theta_caveat",
    "mean_fitness_ci_lo", "mean_fitness_ci_hi",
)  # fmt: skip
ROUND_KEYS = (
    "round", "label", "accuracy", "composite_fitness", "total", "improved", "p_value",
    "verdict_reason", "separable", "degraded_samples", "not_attempted", "unscored", "deprecated",
    "candidates_scored", "electable_count", "status", "prompt_fields", "pipeline_params",
    "evaluators", "overlap", "overlap_results", "selected_labels", "optimizer_state",
)  # fmt: skip


def _round(d: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {k: d.get(k) for k in ROUND_KEYS}
    ability = d.get("ability") or {}
    out["ability"] = {k: ability.get(k) for k in ("theta", "se", "ruler_n", "caveat")}
    health = d.get("health") or {}
    out["health"] = {k: health.get(k) for k in ("grade", "cause", "suggested_action")}
    board = {r.get("candidate_id") or r.get("label"): r for r in d.get("scoreboard") or []}
    cands = []
    for c in d.get("candidate_scores") or []:
        row = board.get(c.get("candidate_id")) or board.get(c.get("label")) or {}
        cands.append(
            {**{k: c.get(k) for k in CANDIDATE_KEYS}, "is_selected": row.get("is_selected")}
        )
    out["candidates"] = sorted(cands, key=lambda c: str(c["label"]))
    out["scoreboard_order"] = [
        ids.get(r.get("candidate_id") or "", r.get("label")) for r in d.get("scoreboard") or []
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
        ("round", "idx", "label", "parent_ids", "source", "changes_description"),
    ),
    "phase": ("phases", ("round", "phase", "event")),
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
        payload = r.get("payload") or {}
        if kind == "phase" and r.get("event") == "terminal":
            entry["payload"] = {k: v for k, v in payload.items() if k != "view"}
        elif kind == "llm_call":
            entry["repairs"] = len(payload.get("schema_repair_errors") or [])
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


def extract(cycle: Path) -> dict[str, Any]:
    rounds = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((cycle / "rounds").glob("*.json"))
    ]
    ledger = _jsonl(cycle / ".runtime" / "ledger.jsonl")
    index = json.loads((cycle / "index.json").read_text(encoding="utf-8"))
    ids = _id_map(ledger, rounds)
    canon = Canon(ids)
    final = index.get("final") or {}
    run = {
        **{k: index.get(k) for k in ("status", "stop_reason", "n_rounds", "best_round")},
        **{
            k: final.get(k)
            for k in (
                "rounds_to_separable", "rounds_to_improved", "rounds_to_ceiling",
                "origin_composite_fitness", "mode", "result_prompt_fields",
                "result_pipeline_params", "bench",
            )
        },
        "round_index": index.get("rounds"),
    }  # fmt: skip
    return {
        "run": canon(run),
        "rounds": [canon(_round(d, ids)) for d in rounds],
        "ledger": _ledger(ledger, canon),
    }


# ---------------------------------------------------------------------------------------------
# The entry: guard the home, then one child process per optimizer
# ---------------------------------------------------------------------------------------------


def digests() -> dict[str, str]:
    """The L4 identity digests: the estimator's source, and each optimizer's prompt source."""
    complete_registries()
    out = {"estimator": module_source_digest(*measurement_modules())}
    for name, runtime in sorted(optimizers.runtimes().items()):
        out[f"prompt:{name}"] = runtime.source_digest(*measurement_modules())
    return out


def claim_home() -> Path:
    """The home named explicitly, empty or an earlier offline run's — never a real workspace."""
    named = os.environ.get("PROMPTPOTTER_HOME")
    if not named:
        raise SystemExit("offline run: set PROMPTPOTTER_HOME to the directory it may write into")
    home = Path(named.removeprefix("\\\\?\\")).expanduser().resolve()
    if home.is_dir() and any(home.iterdir()):
        if not (home / STAMP).is_file():
            raise SystemExit(f"offline run: {home} holds files and no {STAMP}; refusing to write")
        rmtree_robust(home)
    home.mkdir(parents=True, exist_ok=True)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--optimizer", action="append", help="default: every installed optimizer")
    ap.add_argument("--rounds", type=int, default=4, help="max_rounds per campaign")
    ap.add_argument("--rows", type=int, default=200, help="synthetic bank size")
    ap.add_argument("--digests", action="store_true", help="print the L4 identity digests")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.digests:
        print(json.dumps(digests(), indent=1))
        return 0
    if args.child:
        if any(os.environ[k] != NOT_A_KEY for k in PROVIDER_KEYS):
            raise SystemExit("offline run: --child runs only under the env its parent builds")
        workspace = Path(os.environ["PROMPTPOTTER_HOME"])
        print(asyncio.run(run_one(args.child, workspace, rounds=args.rounds, rows=args.rows)))
        return 0

    home = claim_home()
    complete_registries()
    names = args.optimizer or sorted(optimizers.runtimes())
    stamp = {"offline": True, "note": LABEL, "workspaces": names}
    (home / STAMP).write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
    # One workspace each: the archive and the δ ruler pool across campaigns in a workspace, so a
    # shared one would let one optimizer's run move another's decisions.
    sizes = ["--rounds", str(args.rounds), "--rows", str(args.rows)]
    t0 = time.monotonic()
    children = {}
    for name in names:
        (home / name).mkdir()
        with (home / name / "run.log").open("w", encoding="utf-8") as log:
            children[name] = subprocess.Popen(
                [sys.executable, __file__, "--child", name, *sizes],
                cwd=home / name,
                env=child_env(home / name),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    failed = 0
    for name, child in children.items():
        workspace = home / name
        if rc := child.wait():
            failed += 1
            print(f"{name}: FAILED (exit {rc}) -- {workspace / 'run.log'}")
            continue
        run = json.loads((workspace / "decisions.json").read_text(encoding="utf-8"))["run"]
        (cycle,) = (workspace / "projects").glob("*/campaigns/*/cycles/*")
        failed += run["bench"] is None
        headline = "NO BENCH HEADLINE" if run["bench"] is None else f"{run['bench']['lift']:+.3f}"
        print(f"{name}: {run['stop_reason']}, bench lift {headline} -- {cycle}")
    print(f"wall clock {time.monotonic() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
