"""The offline world: every network edge a run reaches, answered in-process — the optimizer
LLM's wire and the backend's. The offline run (``scripts/offline_run.py``) and the test journeys
run inside this one world; what it fakes and why: ``docs/developer/offline-run.md``."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import re
import signal
import socket
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from promptpotter.domain.sample import Sample

NOT_A_KEY = "offline-run-not-a-key"
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
LABELS = ("TRUE", "FALSE", "Uncertain")
# Past the origin's panel, inside the first round's walk.
INTERRUPT_AT_CELL = 25
MARKER = re.compile(r"Variant ([\w.]+):")


def draw(*parts: object) -> int:
    """The same integer for the same parts, in every process: the world's only randomness."""
    return int.from_bytes(hashlib.sha256("\x1f".join(map(str, parts)).encode()).digest()[:8], "big")


USAGE: dict[str, Any] = {
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "total_tokens": 1200,
    "cost": 0.0,
}


def completion(
    content: str, *, model: str, id: str = "offline", usage: dict[str, Any] | None = USAGE
) -> dict[str, Any]:
    """A `chat.completion` as an OpenAI-compatible provider sends it; *usage* None reports none."""
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


def message(content: str, *, model: str) -> dict[str, Any]:
    """A `message` as Anthropic's Messages API sends it."""
    return {
        "id": "offline",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": USAGE["prompt_tokens"],
            "output_tokens": USAGE["completion_tokens"],
        },
    }


type Said = str | dict[str, Any] | httpx.Response
# Each provider wire the world answers, by the path it posts to: a text is sent back in its shape.
LLM_WIRES: dict[str, Callable[..., dict[str, Any]]] = {
    "/chat/completions": completion,
    "/v1/messages": message,
}


class FakeLLM:
    """The LLM edge: every provider wire in `LLM_WIRES`, every body it is sent kept in `sent`.

    *says* is the policy: a text, a whole reply, or a response carrying a status — sync or async,
    and it may raise an `httpx` transport error as a dropped connection does."""

    def __init__(self, says: Callable[[dict[str, Any]], Said | Awaitable[Said]]) -> None:
        self.says = says
        self.sent: list[dict[str, Any]] = []

    def reply(
        self, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response | Awaitable[httpx.Response]:
        self.sent.append(body)
        said = self.says(body)
        if inspect.isawaitable(said):
            pending = said

            async def landed() -> httpx.Response:
                return _wire(request, body, await pending)

            return landed()
        return _wire(request, body, said)


def _wire(request: httpx.Request, body: dict[str, Any], said: Said) -> httpx.Response:
    if isinstance(said, httpx.Response):
        return said
    if isinstance(said, str):
        shaped = next(w for path, w in LLM_WIRES.items() if request.url.path.endswith(path))
        said = shaped(said, model=body["model"])
    return _json(200, said, request)


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
        "fields": [
            "persona",
            "task_intent",
            "problem_description",
            "instruction",
            "thinking_style",
            "answer_format",
        ],
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


class Hang:
    """A request the backend never answers: only the client's own clock ends it."""


type Fault = httpx.Response | httpx.RequestError | Hang


class FakeBackend:
    """The backend edge: TermNorm's wire at `AT`, one `llm_only` node, every path asked kept in
    `asked`. *faults* answer a path's requests, in order, before the backend does."""

    AT = "127.0.0.1:8000"
    # P(correct) = k/10 by marker. Potter's script: round-1 win, two stalls (L2, L3), round-4 win.
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
    ) -> None:
        self.truth = {s.query: s.ground_truth for s in samples}
        self.cells = 0
        self.interrupts = interrupts
        self.faults = {path: list(script) for path, script in (faults or {}).items()}
        self.asked: list[str] = []

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
                pipeline = {
                    "name": "TermNorm",
                    "version": "offline",
                    "nodes": {"llm_only": LLM_ONLY_NODE},
                    "pipelines": {"llm_only": ["llm_only"]},
                }
                answer = {
                    "status": "success",
                    "message": "Pipeline configuration",
                    "data": pipeline,
                }
            case "/status":
                answer = {"status": "ok"}
            case "/sessions":
                answer = {"status": "success", "data": {"terms_count": 0}}
            case "/matches":
                answer = self.matches(json.loads(body))
            case _:
                return None
        return _json(200, answer, request)

    def matches(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.cells += 1
        if self.cells == INTERRUPT_AT_CELL:
            for _ in range(self.interrupts):
                signal.raise_signal(signal.SIGINT)
        query = payload["query"]
        node_cfg = payload["node_config"]["llm_only"]
        cfg_key = json.dumps(node_cfg, sort_keys=True, ensure_ascii=False)
        prompt = str(node_cfg["prompt"])
        marks = MARKER.findall(prompt)
        tag = marks[-1] if marks else None
        k = self.SKILL.get(tag, 1 + draw("skill", tag) % 9)
        gt = self.truth[query]
        if draw("cell", cfg_key, query) % 10 < k:
            label = gt
        else:
            wrong = [lab for lab in LABELS if lab != gt]
            label = wrong[draw("wrong", cfg_key, query) % len(wrong)]
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
                        "output": 200 + draw("out", cfg_key) % 400,
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


async def _never() -> httpx.Response:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


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


def _harbor_registry() -> str:
    from harbor.constants import DEFAULT_REGISTRY_URL

    url: str = DEFAULT_REGISTRY_URL
    return url


class Router:
    """The world: the backend at its address, an LLM on any of its wires, an empty model
    catalogue, Harbor's registry once one is published; anything else is named in `unrouted`
    and refused."""

    def __init__(self, llm: FakeLLM, backend: FakeBackend) -> None:
        self.llm = llm
        self.backend = backend
        self.registry: list[dict[str, Any]] | None = None
        self.unrouted: set[str] = set()

    def route(
        self, request: httpx.Request, body: bytes
    ) -> httpx.Response | Awaitable[httpx.Response]:
        u = urlsplit(str(request.url))
        answered = None
        if u.netloc == FakeBackend.AT:
            answered = self.backend.respond(request, body)
        elif u.path.endswith(tuple(LLM_WIRES)):
            answered = self.llm.reply(request, json.loads(body))
        elif f"{u.netloc}{u.path}" == "openrouter.ai/api/v1/models":
            # No model claims a capability, so a run reads the measured profiles alone.
            answered = _json(200, {"data": []}, request)
        elif self.registry is not None and f"{u.scheme}://{u.netloc}{u.path}" == _harbor_registry():
            answered = _json(200, self.registry, request)
        if answered is None:
            self.unrouted.add(f"{request.method} {u.scheme}://{u.netloc}{u.path}")
            return _json(404, {"error": "offline run: no route"}, request)
        return answered


class _Body(io.BytesIO):
    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _refuse_resolve(*_: object, **__: object) -> Any:
    raise OSError("offline run: no name resolution, so no request leaves this process")


def install_network(router: Router) -> Callable[[], None]:
    """Answers every client in this process from *router*; the callable returned restores them."""

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
        try:
            return await answered
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
            return _Body(json.dumps(OPENROUTER_ENDPOINTS).encode())
        router.unrouted.add(f"urlopen {target}")
        raise urllib.error.URLError("offline run")

    real = (
        httpx.AsyncHTTPTransport.handle_async_request,
        httpx.HTTPTransport.handle_request,
        urllib.request.urlopen,
        socket.getaddrinfo,
    )
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign,assignment]
    httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign,assignment]
    urllib.request.urlopen = urlopen
    # Every client above is answered before it resolves a name; one that is not fails here.
    socket.getaddrinfo = _refuse_resolve

    def restore() -> None:
        (
            httpx.AsyncHTTPTransport.handle_async_request,  # type: ignore[method-assign]
            httpx.HTTPTransport.handle_request,  # type: ignore[method-assign]
            urllib.request.urlopen,
            socket.getaddrinfo,
        ) = real

    return restore
