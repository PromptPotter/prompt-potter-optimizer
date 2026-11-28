"""The offline world: every network edge a run reaches, answered in-process — the optimizer
LLM's wire and the backend's. The offline run (``scripts/offline_run.py``) and the test journeys
run inside this one world; what it fakes and why: ``docs/developer/offline-run.md``."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import enum
import functools
import hashlib
import inspect
import io
import itertools
import json
import random
import re
import signal
import socket
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from promptpotter.application.jobs import spawning

# Its registries complete as it imports: the manifests `optimizer` renders read them.
from promptpotter.application.maintenance import landing  # noqa: F401
from promptpotter.application.optimizer_contract import MANIFESTS
from promptpotter.application.optimizer_manifest import (
    bench_choice,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.config.settings import settings
from promptpotter.connectors import termnorm
from promptpotter.domain.optimizer_template import PROMPT_FIELDS
from promptpotter.domain.pipeline_overlay import normalize_model_id
from promptpotter.infrastructure.llm import send_pacing
from promptpotter.infrastructure.llm.pricing import (
    BUNDLED_PATH,
    UPSTREAM_URL,
    RatesFile,
    compute_usd,
)
from promptpotter.infrastructure.llm.registry import (
    default_routes,
    keyed_specs,
    openai_compat_spec,
    providers,
)
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.run_scope import InnerSpawnContext, run_scope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence

    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores

# What the world answers, by version: an edit to any edge's answer bumps it, so a trace names its world.
WORLD = "2"
NOT_A_KEY = "offline-run-not-a-key"
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
LABELS = ("TRUE", "FALSE", "Uncertain")
# Past the origin's panel, inside the first round's walk.
INTERRUPT_AT_CELL = 25
MARKER = re.compile(r"Variant ([\w.]+):")


def draw(*parts: object) -> int:
    """The same integer for the same parts, in every process: the world's only randomness."""
    return int.from_bytes(hashlib.sha256("\x1f".join(map(str, parts)).encode()).digest()[:8], "big")


def shown_text(prompt: str, locus: str) -> str:
    """A field's first paragraph under L1's CURRENT PROMPTS, as a model copies an anchor; empty
    where the field renders none, which anchors the empty field whole."""
    head = f"\n[{locus}]\n"
    shown = prompt.find(head, max(prompt.find("CURRENT PROMPTS"), 0))
    if shown < 0:
        return ""
    return prompt[shown + len(head) :].split("\n\n", 1)[0]


# The world's own usage: priced as it leaves the wire (`_wire`), off its model and these tokens by the
# rate table the production meter reads, 0.0 where the table lists no (provider, model) pair.
USAGE: dict[str, Any] = {
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "total_tokens": 1200,
    # The parts a provider breaks out, reported as it reports them: a zero is a reported zero.
    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0},
}


def completion(
    content: str,
    *,
    model: str,
    id: str = "offline",
    usage: dict[str, Any] | None = USAGE,
    usd: float | None = None,
) -> dict[str, Any]:
    """A `chat.completion` as an OpenAI-compatible provider sends it; *usage* None reports none,
    *usd* is the cost the gateway bills it, the world's door for a price a fact states."""
    if usd is not None:
        usage = (usage or USAGE) | {"cost": usd}
    return {
        "id": id,
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "provider": "OfflineHost",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        **({"usage": usage} if usage is not None else {}),
    }


type Said = str | dict[str, Any] | httpx.Response
# The one provider wire the world answers: a text is sent back as a completion.
LLM_WIRE = "/chat/completions"


class Heal(enum.StrEnum):
    """An invalid answer, by the rung of the client's ladder it sends a structured call to: a
    schema repair, a clean re-ask, or a refusal whose generation the client salvages. A text call
    climbs none: noncompliant is out of its form."""

    NONCOMPLIANT = "noncompliant"
    EMPTY = "empty"
    REFUSED = "refused"


def invalid(heal: Heal, valid: str, *, model: str) -> dict[str, Any] | httpx.Response:
    """*valid* is the answer the call heals to; only a refusal carries it."""
    match heal:
        case Heal.NONCOMPLIANT:
            return completion(json.dumps({"offline": "an answer its schema rejects"}), model=model)
        case Heal.EMPTY:
            return completion("", model=model)
        case Heal.REFUSED:
            error = {
                "message": "offline: json_validate_failed",
                "code": "json_validate_failed",
                "failed_generation": valid,
            }
            return httpx.Response(400, json={"error": error})


class FakeLLM:
    """The LLM edge: every provider's `LLM_WIRE`, every body it is sent kept in `sent`.

    *says* is the policy: a text, a whole reply, or a response carrying a status — sync or async,
    and it may raise what a wire raises (an `httpx` transport error, an `OSError`). Anything else
    it raises is the harness's own bug, a `PolicyFault` no handler of a refused call takes."""

    def __init__(self, says: Callable[[dict[str, Any]], Said | Awaitable[Said]]) -> None:
        self.says = says
        self.sent: list[dict[str, Any]] = []

    def reply(
        self, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response | Awaitable[httpx.Response]:
        self.sent.append(body)
        with _policy_faults():
            said = self.says(body)
            if not inspect.isawaitable(said):
                return _wire(request, body, said)
        pending = said

        async def landed() -> httpx.Response:
            with _policy_faults():
                return _wire(request, body, await pending)

        return landed()


class PolicyFault(BaseException):
    """A fault in the world's policy: past every `except Exception`, out of the scenario."""


@contextlib.contextmanager
def _policy_faults() -> Iterator[None]:
    try:
        yield
    except (httpx.RequestError, OSError):
        raise
    except Exception as fault:
        raise PolicyFault(repr(fault)) from fault


def _wire(request: httpx.Request, body: dict[str, Any], said: Said) -> httpx.Response:
    if isinstance(said, httpx.Response):
        return said
    if isinstance(said, str):
        said = completion(said, model=body["model"])
    if said.get("usage") is USAGE:
        said = said | {"usage": USAGE | {"cost": _priced(request, said["model"])}}
    return _json(200, said, request)


def _priced(request: httpx.Request, model: str) -> float:
    """What production's rate table says `USAGE` costs on the provider whose wire *request* is."""
    host = urlsplit(str(request.url)).netloc
    provider = next(
        (
            p
            for p in providers()
            if (spec := openai_compat_spec(p)) is not None
            and urlsplit(spec.base_url).netloc == host
        ),
        None,
    )
    usd = compute_usd(model, USAGE["prompt_tokens"], USAGE["completion_tokens"], provider=provider)
    return 0.0 if usd is None else usd


def _json(status: int, payload: Any, request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=request,
    )


LLM_ONLY_NODE: dict[str, Any] = {
    "type": "llm",
    "runtime": "backend",
    "node_role": "ranker",
    "description": "Generic LLM call (offline TermNorm).",
    "prompt_info": {
        "family": "llm_only",
        "template_variables": [],
        "fields": list(PROMPT_FIELDS),
        "description": "offline",
    },
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
            *PROMPT_FIELDS,
        ],
        "param_allowed_values": {
            "provider": ["openai", "groq", "openrouter"],
            "reasoning_effort": ["none", "default", "low", "medium", "high"],
        },
        "observation_name": "llm_only",
        "observation_mappings": [
            {"pipeline_key": "final_ranking", "output_field": "final_ranking", "is_llm": True}
        ],
    },
    "spend_bound": {"kind": "llm", "attempts": 3, "input_bytes": 31000, "max_tokens": 8192},
}


def prompt_node(name: str, start: Mapping[str, str]) -> dict[str, Any]:
    """A node a test declares, served as `llm_only` is: its prompt fields, each starting as *start*."""
    fields = list(start)
    return LLM_ONLY_NODE | {
        "prompt_info": {
            **LLM_ONLY_NODE["prompt_info"],
            "family": name,
            "fields": fields,
            "rewrite_field": fields[0],
            "start": dict(start),
        },
        "optimizer": {
            **LLM_ONLY_NODE["optimizer"],
            "param_keys": [
                *(k for k in LLM_ONLY_NODE["optimizer"]["param_keys"] if k not in PROMPT_FIELDS),
                *fields,
            ],
            "observation_name": name,
        },
    }


# What the rebase leg's L2 steers L1 onto (`L2Memory.steered_onto`): no other leg's L2 steers.
L2_STEER = {"temperature": 0.5}
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
# A cited panel, by the heading it opens on: the framing where the prompt renders one, else the tally.
FRAMING = "TASK CONTEXT:\n"
TALLY = "ANSWER DISTRIBUTION ("


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


def inner_cell(stores: Stores, hop: CycleHop) -> str:
    """An L4 cell's inner campaign at *hop* by its seat's `run_seed` and the arm laid into its
    overlay, so two seats of one arm never share it, never by its id, which hashes the code."""
    config = cycle_config(stores, stores.campaigns.campaign_cycle(hop)).config
    overlay = {node: dict(o.config) for node, o in sorted(config.optimization.nodes.items())}
    held = json.dumps([config.run_seed, overlay], sort_keys=True, default=str)
    return hashlib.sha256(held.encode()).hexdigest()[:8]


def _asker() -> str:
    """Empty for the run's own call; an L4 cell's inner campaign by its `inner_cell`."""
    scope = run_scope()
    if scope.instrument is None:
        return ""
    spawn, stores = scope.spawn, scope.account
    assert isinstance(spawn, InnerSpawnContext) and stores is not None, "an inner run binds both"
    return inner_cell(stores, spawn.inside[-1])


class ByCallIdentity:
    """The offline run's `FakeLLM` policy: an answer follows the CALL's identity, never the
    prompt's wording nor the arrival order. Each request is kept in *capture* (none: kept
    nowhere). *rebasing*, every L2 fire steers L1 and proposes a fork. *crossing*, every variant
    rewrites a second field, so any two arms differ on two loci a crossover swaps."""

    def __init__(
        self,
        capture: Path | None,
        templates: Mapping[str, str],
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
        if self.capture is None:
            return said
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
        cites = FRAMING if FRAMING in prompt else TALLY
        assert cites in prompt, f"offline run: no {cites!r} panel to cite"
        start = prompt.index(cites) + len(cites) + draw("cite", generation, i) % 20
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


def rendered(selected: SelectedOptimizer) -> dict[str, str]:
    """Each of *selected*'s llm nodes by its rendered template: how `ByCallIdentity` names a text call."""
    return {
        node: running_prompt(node, selected.node_config(node), selected.manifest).render()
        for node in selected.llm_nodes
    }


def optimizer() -> ByCallIdentity:
    """The optimizer LLM of a world no journey scripted (a served app's, each run it launches):
    every installed optimizer's call answered by its identity, its request kept nowhere."""
    templates: dict[str, str] = {}
    for name in sorted(MANIFESTS.table):
        templates |= rendered(select_optimizer(bench_choice(name)))
    return ByCallIdentity(None, templates)


class FakeHarness:
    """The n8n eval-harness edge: one webhook at `URL`, each POST kept in `asked` as its headers
    and body. *says* is the policy: a reply body, or a response carrying a status."""

    AT = "127.0.0.1:5678"
    URL = f"http://{AT}/webhook/eval-harness"

    def __init__(self) -> None:
        self.says: Callable[[dict[str, Any]], dict[str, Any] | httpx.Response] = lambda body: {
            "case_id": body["case_id"],
            "answer": "true",
            "raw": {},
        }
        self.asked: list[tuple[httpx.Headers, dict[str, Any]]] = []

    def respond(self, request: httpx.Request, body: bytes) -> httpx.Response | None:
        if request.method != "POST" or str(request.url) != self.URL:
            return None
        sent = json.loads(body)
        self.asked.append((request.headers, sent))
        said = self.says(sent)
        return said if isinstance(said, httpx.Response) else _json(200, said, request)


class Hang:
    """A request the backend never answers: only the client's own clock ends it."""


type Fault = httpx.Response | httpx.RequestError | Hang


class FakeBackend:
    """The backend edge: TermNorm's wire at `AT`, the node graph `nodes` walked in order (`llm_only`
    unless a test declares one, `prompt_node`), every path asked kept in `asked`. *faults* answer
    a path's requests, in order, before the backend does; *hardness* moves each row's `k` by its
    own draw, so the δ ruler spans the rows and θ holds a level."""

    AT = "127.0.0.1:8000"
    URL = f"http://{AT}"
    # P(correct) = k/10 by marker. Potter's script: a round-1 win, then the stalls that fire L2.
    SKILL: ClassVar[dict[str | None, int]] = {
        None: 3,
        "g0v0": 1, "g0v1": 7, "g0v2": 2, "g1v0": 2, "g1v1": 4, "g1v2": 3,
        "g2v0": 3, "g2v1": 1, "g2v2": 5, "g3v0": 9, "g3v1": 2, "g3v2": 1,
    }  # fmt: skip

    def __init__(
        self,
        samples: list[Sample],
        *,
        interrupts: int = 0,
        faults: Mapping[str, Sequence[Fault]] | None = None,
        hardness: int = 0,
    ) -> None:
        self.truth = {s.query: s.ground_truth for s in samples}
        # The terms the run's session posted (`/sessions`): a row the world holds no truth for
        # (a served world's, whose rows are the operator's) takes its truth and answers from them.
        self.terms: list[str] = []
        self.cells = 0
        self.interrupts = interrupts
        self.hardness = hardness
        self.faults = {path: list(script) for path, script in (faults or {}).items()}
        self.asked: list[str] = []
        # Every cell asked, as (prompt, query, round asking, ``None`` outside a round's loop);
        # while `down`, each is refused as a 500.
        self.sent: list[tuple[str, str, int | None]] = []
        self.down = False
        # One in this many of the origin's rows answered as the pipeline's ERROR candidate, a
        # charged error, on every ask of that row (0: none).
        self.origin_errors = 0
        # Awaited before each cell is answered, inside the run's own task: an operator acting mid-walk.
        self.before_answer: Callable[[], Awaitable[None]] | None = None
        # A test's own answers: whether the cell of (prompt, query) is right, in place of the skill.
        self.answers: Callable[[str, str], bool] | None = None
        # The graph `/pipeline` serves, one pipeline walking every node in order.
        self.nodes: dict[str, dict[str, Any]] = {"llm_only": LLM_ONLY_NODE}

    def declared(self, *_: Any) -> dict[str, Any]:
        """The graph `/pipeline` serves: an in-process connector's `pipeline_declaration`."""
        return {
            "name": "TermNorm",
            "version": "offline",
            "nodes": self.nodes,
            "pipelines": {next(iter(self.nodes)): list(self.nodes)},
        }

    def respond(
        self, request: httpx.Request, body: bytes
    ) -> httpx.Response | Awaitable[httpx.Response] | None:
        path = request.url.path
        self.asked.append(path)
        if script := self.faults.get(path):
            fault = script.pop(0)
            if isinstance(fault, Hang):
                return _never()
            if isinstance(fault, httpx.RequestError):
                raise fault
            return fault
        match path:
            case "/pipeline":
                answer = {
                    "status": "success",
                    "message": "Pipeline configuration",
                    "data": self.declared(),
                }
            case "/status":
                answer = {"status": "ok"}
            case "/sessions":
                self.terms = [str(t) for t in json.loads(body).get("terms") or ()]
                answer = {"status": "success", "data": {"terms_count": len(self.terms)}}
            case "/matches":
                payload = json.loads(body)
                prompt = self.prompt(payload)
                self.sent.append((prompt, payload["query"], current_round()))
                if self.down:
                    return _json(500, {"detail": "offline: the backend is down"}, request)
                answer = self.matches(payload)
                hook = self.before_answer
                # A cell's own lag, in loop turns keyed on what it asks: calls out at once return
                # in another order than sent, the same order in every process.
                lag = draw("lag", prompt, payload["query"]) % 4
                if not lag and hook is None:
                    return _json(200, answer, request)

                async def answered() -> httpx.Response:
                    for _ in range(lag):
                        await asyncio.sleep(0)
                    if hook is not None:
                        _acted(hook)
                    return _json(200, answer, request)

                return answered()
            case _:
                return None
        return _json(200, answer, request)

    def prompt(self, payload: dict[str, Any]) -> str:
        """Every node's prompt the cell sent, in walk order."""
        return "\n".join(str(payload["node_config"][n]["prompt"]) for n in self.nodes)

    def matches(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.cells += 1
        if self.cells == INTERRUPT_AT_CELL:
            for _ in range(self.interrupts):
                signal.raise_signal(signal.SIGINT)
        query = payload["query"]
        terminal = list(self.nodes)[-1]
        node_cfg = payload["node_config"][terminal]
        # The prompt the skill is read off keys every draw: a wire key's spelling moves none.
        prompt = self.prompt(payload)
        marks = MARKER.findall(prompt)
        tag = marks[-1] if marks else None
        k = self.SKILL.get(tag, 1 + draw("skill", tag) % 9)
        k += self.hardness * (draw("hard", query) % 3 - 1)
        labels: tuple[str, ...] = LABELS
        if (gt := self.truth.get(query)) is None:
            assert self.terms, f"offline: no truth and no session's terms for {query[:60]!r}"
            labels = tuple(self.terms)
            gt = labels[draw("truth", query) % len(labels)]
        label: str | None
        if self.answers is not None:
            right = self.answers(prompt, query)
            label = gt if right else next(lab for lab in labels if lab != gt)
        elif tag is None and self.origin_errors and draw("errs", query) % self.origin_errors == 0:
            # The pipeline's own failure on the row, which the walk charges as a miss.
            label = "ERROR"
        elif draw("cell", prompt, query) % 10 < k:
            label = gt
        else:
            wrong = [lab for lab in labels if lab != gt] or [gt]
            label = wrong[draw("wrong", prompt, query) % len(wrong)]
        return {
            "status": "success",
            "message": "ok",
            "data": {
                "final_ranking": [{"candidate": label, "relevance_score": 1.0}],
                "reasoning_trace": f"offline trace for k={k}",
                "diagnostics": {"step_statuses": {terminal: "success"}, "warnings": []},
                "step_timings": {terminal: 1.0},
                "step_tokens": {
                    terminal: {
                        "input": 400 + len(prompt) // 4,
                        "output": 200 + draw("out", prompt) % 400,
                        "cost_usd": 0.0,
                        "model": str(node_cfg["model"]),
                        "served_by": "OfflineHost",
                        "finish_reason": "stop",
                        "reasoning": 0,
                        "cache_write": 0,
                    }
                },
                "total_time": 1.0,
                "terminal_node": terminal,
            },
        }


def _acted(act: Callable[[], Awaitable[None]]) -> None:
    """*act* run whole while the run's loop waits on it: a command's thread hops last as long as
    the machine makes them, so on the loop no cell lands and no timer wakes a phase while it acts."""

    async def whole() -> None:
        await act()

    with ThreadPoolExecutor(1) as one:
        one.submit(contextvars.copy_context().run, asyncio.run, whole()).result()


async def _never() -> httpx.Response:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


def openrouter_endpoints(model: str) -> dict[str, Any]:
    """The hosts OpenRouter lists *model* on: the world's own, and every host production pins it to."""
    pinned = default_routes().get("openrouter", {}).get(normalize_model_id(model), [])
    return {
        "data": {
            "endpoints": [
                {
                    "tag": host,
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000004", "request": "0"},
                    "max_completion_tokens": 32000,
                    "context_length": 131072,
                }
                for host in ("offlinehost", *pinned)
            ]
        }
    }


def _harbor_registry() -> str:
    from harbor.constants import DEFAULT_REGISTRY_URL

    url: str = DEFAULT_REGISTRY_URL
    return url


def _key_checks() -> set[str]:
    return {spec.base_url + spec.key_check for spec, _ in keyed_specs().values()}


@functools.cache
def _bundled_listing() -> str:
    """The shipped floor in upstream's vocabulary, its `lister` upstream's `litellm_provider`:
    validated once a process, as a world is laid per fact."""
    floor = RatesFile.model_validate_json(BUNDLED_PATH.read_bytes()).models
    return json.dumps(
        {
            name: body.model_dump(exclude={"lister"}, exclude_none=True)
            | ({"litellm_provider": body.lister} if body.lister else {})
            for name, body in floor.items()
        }
    )


class Router:
    """The world: the backend and the n8n harness at their addresses, an LLM on any of its wires
    and its key check, the model catalogue (empty unless a journey lists one), Harbor's registry once one is published, the
    upstream price list (the bundled floor unless a journey says what it lists or kills it), a sign-in provider's token endpoint
    once a journey says what it answers; anything else is named in `unrouted` and refused."""

    def __init__(self, llm: FakeLLM, backend: FakeBackend) -> None:
        self.llm = llm
        self.backend = backend
        self.harness = FakeHarness()
        self.registry: list[dict[str, Any]] | None = None
        # The model catalogue: empty unless a journey lists one, so a run reads the measured profiles.
        self.catalogue: list[dict[str, Any]] = []
        # Upstream's rows, `provider/model` -> its per-token costs, as `refresh_rates` fetches them:
        # the bundled floor, unless a journey says what upstream lists.
        self.rates: dict[str, dict[str, Any]] = json.loads(_bundled_listing())
        # Upstream unreachable, as a dead host fails `urlopen`; every try at it counted alike.
        self.upstream_dead = False
        self.rate_fetches = 0
        # `host/path` of a token endpoint -> the (status, body) it answers every code with.
        self.token_endpoints: dict[str, tuple[int, dict[str, Any]]] = {}
        # An OpenRouter model -> the hosts its gateway lists; one unnamed lists `openrouter_endpoints`.
        self.hosts: dict[str, dict[str, Any]] = {}
        # OpenRouter unreachable, as a dead host fails `urlopen` on its endpoints list.
        self.openrouter_dead = False
        self.unrouted: set[str] = set()

    @classmethod
    def idle(cls) -> Router:
        """The world before anyone says what it answers: every LLM call `ok`, a backend of no rows."""
        return cls(FakeLLM(lambda _body: "ok"), FakeBackend([]))

    def route(
        self, request: httpx.Request, body: bytes
    ) -> httpx.Response | Awaitable[httpx.Response]:
        u = urlsplit(str(request.url))
        answered = None
        if u.netloc == FakeBackend.AT:
            answered = self.backend.respond(request, body)
        elif u.netloc == FakeHarness.AT:
            answered = self.harness.respond(request, body)
        elif u.path.endswith(LLM_WIRE):
            answered = self.llm.reply(request, json.loads(body))
        elif f"{u.netloc}{u.path}" == "openrouter.ai/api/v1/models":
            answered = _json(200, {"data": self.catalogue}, request)
        elif request.method == "GET" and f"{u.scheme}://{u.netloc}{u.path}" in _key_checks():
            # Every key the world holds is one its provider accepts.
            answered = _json(200, {"data": []}, request)
        elif self.registry is not None and f"{u.scheme}://{u.netloc}{u.path}" == _harbor_registry():
            answered = _json(200, self.registry, request)
        elif (token := self.token_endpoints.get(f"{u.netloc}{u.path}")) is not None:
            answered = _json(*token, request)
        if answered is None:
            self.unrouted.add(f"{request.method} {u.scheme}://{u.netloc}{u.path}")
            return _json(404, {"error": "offline run: no route"}, request)
        return answered


class _Body(io.BytesIO):
    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


UNROUTED = "unrouted: this fact asks for no network world (the `offline` fixture answers one)"


def _refuse_resolve(*_: object, **__: object) -> Any:
    raise OSError("offline run: no name resolution, so no request leaves this process")


def _swap_sends(
    handle_async_request: Callable[[Any, httpx.Request], Awaitable[httpx.Response]],
    handle_request: Callable[[Any, httpx.Request], httpx.Response],
    urlopen: Callable[..., Any],
    getaddrinfo: Callable[..., Any],
) -> Callable[[], None]:
    """Puts the doors a send leaves this process by (each `httpx` transport, `urlopen`, name
    resolution) behind the handlers given; the callable returned restores them."""
    real = (
        httpx.AsyncHTTPTransport.handle_async_request,
        httpx.HTTPTransport.handle_request,
        urllib.request.urlopen,
        socket.getaddrinfo,
    )
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign,assignment]
    httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign,assignment]
    urllib.request.urlopen = urlopen
    socket.getaddrinfo = getaddrinfo

    def restore() -> None:
        (
            httpx.AsyncHTTPTransport.handle_async_request,  # type: ignore[method-assign]
            httpx.HTTPTransport.handle_request,  # type: ignore[method-assign]
            urllib.request.urlopen,
            socket.getaddrinfo,
        ) = real

    return restore


def refuse_network(refused: set[str]) -> Callable[[], None]:
    """Refuses every send in this process, naming each in *refused*: the world of a fact that asks
    for none. The callable returned restores the doors."""

    def named(target: str) -> str:
        refused.add(target)
        return f"{UNROUTED}: {target}"

    async def handle_async_request(_: Any, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(named(f"{request.method} {request.url}"), request=request)

    def handle_request(_: Any, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(named(f"{request.method} {request.url}"), request=request)

    def urlopen(url: Any, *_: Any, **__: Any) -> Any:
        target = url if isinstance(url, str) else url.full_url
        raise urllib.error.URLError(named(f"urlopen {target}"))

    def getaddrinfo(host: Any, *_: Any, **__: Any) -> Any:
        raise OSError(named(f"resolve {host}"))

    return _swap_sends(handle_async_request, handle_request, urlopen, getaddrinfo)


def install_network(router: Router) -> Callable[[], None]:
    """Answers every client in this process from *router*, and sleeps no backend resend's wait;
    the callable returned restores them."""

    def sent(request: httpx.Request, body: bytes) -> httpx.Response | Awaitable[httpx.Response]:
        try:
            return router.route(request, body)
        except httpx.RequestError as dropped:
            dropped.request = request
            raise

    async def handle_async_request(_: Any, request: httpx.Request) -> httpx.Response:
        answered = sent(request, await request.aread())
        if not inspect.isawaitable(answered):
            return answered
        # The client's read timeout, as a socket meets it: an answer that never comes fails the
        # send, never leaves the loop waiting on no timer.
        timer = asyncio.timeout(request.extensions.get("timeout", {}).get("read"))
        try:
            async with timer:
                return await answered
        except TimeoutError:
            if not timer.expired():
                raise
            raise httpx.ReadTimeout(
                "offline: no answer within the read timeout", request=request
            ) from None
        except httpx.RequestError as dropped:
            dropped.request = request
            raise

    def handle_request(_: Any, request: httpx.Request) -> httpx.Response:
        answered = sent(request, request.read())
        assert isinstance(answered, httpx.Response), "a sync client met an async answer"
        return answered

    def urlopen(url: Any, *_: Any, **__: Any) -> Any:
        target = url if isinstance(url, str) else url.full_url
        if "openrouter.ai/api/v1/models/" in target and target.endswith("/endpoints"):
            if router.openrouter_dead:
                raise urllib.error.URLError("offline: OpenRouter's host list is unreachable")
            model = target.partition("/models/")[2].removesuffix("/endpoints")
            return _Body(
                json.dumps(router.hosts.get(model) or openrouter_endpoints(model)).encode()
            )
        if target == UPSTREAM_URL:
            router.rate_fetches += 1
            if router.upstream_dead:
                raise urllib.error.URLError("offline: upstream's price list is unreachable")
            return _Body(json.dumps(router.rates).encode())
        router.unrouted.add(f"urlopen {target}")
        raise urllib.error.URLError("offline run")

    async def unslept(total_sec: float, label: str) -> None:  # noqa: ARG001 -- held_wait's signature
        await asyncio.sleep(0)

    # `send_pacing.py::held_wait`, the one binding every sender reads, the backend's and an LLM's.
    wire = vars(send_pacing)
    real = (wire["held_wait"], termnorm.env_block.ENDPOINTS)
    # The operator's TermNorm is this world's backend, its one endpoint.
    termnorm.env_block.ENDPOINTS = {"offline": FakeBackend.URL}
    # Every client above is answered before it resolves a name; one that is not fails there.
    doors = _swap_sends(handle_async_request, handle_request, urlopen, _refuse_resolve)
    # A refusal answered in-process costs no wall clock: the resend's wait is decided, not slept.
    wire["held_wait"] = unslept

    def restore() -> None:
        doors()
        wire["held_wait"], termnorm.env_block.ENDPOINTS = real

    return restore


def child(code: str, *argv: str) -> list[str]:
    """The command line a fact spawns a child process by: *code* runs as `python -c` runs it, *argv*
    its arguments, inside this world (`run_child`)."""
    return [sys.executable, "-X", "utf8", __file__, code, *argv]


# A run's own process, spawned from inside a world: the module `spawning.command` names, run as
# `python -m` runs it, on the served world's optimizer.
_LAUNCHED = """
import runpy, sys
from offline_world import optimizer
world.llm.says = optimizer()
runpy.run_module(sys.argv[1], run_name="__main__", alter_sys=True)
"""


def held(world: Router) -> None:
    """This process inside *world*, for good, as the `offline` fixture lays the suite's: every key
    one its provider accepts, no rate capped, every send answered by *world*, and every run it
    spawns (`jobs/spawning.py::command`) a child inside a world of its own."""
    for key in PROVIDER_KEYS:
        setattr(settings, key, SecretStr(NOT_A_KEY))
    settings.RATE_LIMITS = {}
    install_network(world)

    spawning.command = lambda module: child(_LAUNCHED, module)


def served() -> Router:
    """The world a served app runs in (`webapp/e2e/serve_app.py`): its optimizer answers every
    installed optimizer's call, its backend the terms each run's session posts."""
    world = Router.idle()
    world.llm.says = optimizer()
    held(world)
    return world


def run_child(code: str, *argv: str) -> None:
    """A spawned child's world (`held`), which *code* reads as `world`. A send no route answered
    fails the child at its exit, whatever *code* did."""
    world = Router.idle()
    held(world)
    sys.argv = [sys.argv[0], *argv]
    try:
        exec(compile(code, "<offline child>", "exec"), {"__name__": "__main__", "world": world})
    finally:
        if world.unrouted:
            raise SystemExit(f"offline child: unrouted sends {sorted(world.unrouted)}")


if __name__ == "__main__":
    # The world its importers know, never a second copy of this module named `__main__`.
    import offline_world

    offline_world.run_child(*sys.argv[1:])
