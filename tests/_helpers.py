"""Shared test helpers — mock functions and classes used across test files.

Fixtures live in ``conftest.py``; this module holds plain functions and classes
that test modules can import directly.
"""

import hashlib
import typing

from api.models.opt_search_point import OptSearchPoint
from api.shared.hashing import HASH_TRUNCATE
from tests.mock_llm_client import MockLLMClient

# ---------------------------------------------------------------------------
# Feedback-cycle mock helpers
# ---------------------------------------------------------------------------


def apply_llm_mock(monkeypatch):
    """Mock get_llm_client to return a MockLLMClient."""
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


def apply_grow_mock(monkeypatch):
    """Mock l1_generate to return deterministic variants (as dicts)."""
    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            osp.derive_candidate(
                instruction=f"variant_{i}_acc{accuracy:.0%}",
                changes_description=f"gen_{i}",
            ).prompt_field_dict()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.l1_optimizer.l1_generate",
        mock_generate,
    )


def apply_eval_mock(monkeypatch, round_hits=None):
    """Mock eval_search_point with configurable per-round hit counts.

    The first positional arg is now a ``SearchPoint`` (not a raw OptSearchPoint).
    Returns a ``call_count`` list ([int]) so callers can track invocations.
    """
    if round_hits is None:
        round_hits = [1, 2, 3]
    call_count = [0]

    async def mock_eval(search_point, data, ctx=None, **kwargs):
        idx = min(call_count[0], len(round_hits) - 1)
        target_hits = round_hits[idx]
        label = kwargs.get("label", "")
        if label == "candidate_0":
            results = []
            for i, d in enumerate(data):
                hit = i < target_hits
                results.append({
                    "query": d["query"],
                    "predicted": d["ground_truth"] if hit else "WRONG",
                    "ground_truth": d["ground_truth"],
                    "hit": hit, "score": 1.0 if hit else 0.0, "error": None,
                })
            accuracy = target_hits / len(data)
            scores = {"hits": target_hits, "total": len(data),
                      "accuracy": accuracy, "errors": 0,
                      "token_recall": 0.0, "composite": accuracy}
            call_count[0] += 1
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0,
                      "token_recall": 0.0, "composite": 0.0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.eval_search_point",
        mock_eval,
    )
    return call_count


# ---------------------------------------------------------------------------
# Mock completion for LLM retry tests
# ---------------------------------------------------------------------------


class MockCompletion:
    """Fake OpenAI-compatible completion response for LLM tests."""

    class Choice:
        class Message:
            content = '{"result": "ok"}'
        message = Message()
        finish_reason = "stop"

    choices: typing.ClassVar = [Choice()]

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15
    usage = Usage()
    model = "test-model"


# ---------------------------------------------------------------------------
# Shared factories — replace duplicated helpers across test files
# ---------------------------------------------------------------------------


def rp_hash(text: str) -> str:
    """Compute rendered_prompt_hash the same way build_dataset_run_data does."""
    return hashlib.sha256(text.encode()).hexdigest()[:HASH_TRUNCATE]


def make_baseline_osp(**overrides) -> OptSearchPoint:
    """Build a baseline OptSearchPoint with sensible defaults."""
    defaults = {
        "instruction": "Rank the candidates.",
        "persona": "",
        "task_intent": "",
        "thinking_style": "",
        "answer_format": "",
    }
    defaults.update(overrides)
    return OptSearchPoint(**defaults)


def make_dataset_run(
    run_id: str = "baseline_aabbccdd",
    accuracy: float = 0.5,
    items: list[dict] | None = None,
    *,
    content_hash: str | None = None,
    name: str | None = None,
    rendered_prompt: str | None = None,
    n_queries: int = 2,
) -> dict:
    """Build a minimal dataset_run dict.

    - If *items* is None, generates *n_queries* synthetic items (all hits).
    - *rendered_prompt* sets rendered_prompt_hash if provided.
    """
    if items is None:
        items = [
            {"query": f"q{i}", "predicted": "p", "ground_truth": "p",
             "hit": True, "confidence": 0.9, "error": None}
            for i in range(n_queries)
        ]
    hits = sum(1 for it in items if it.get("hit"))
    total = len(items)
    run: dict = {
        "run_id": run_id,
        "name": name or run_id,
        "content_hash": content_hash or f"hash_{run_id}",
        "prompt_fields_id": f"ps_{run_id}",
        "item_count": total,
        "scores": {
            "hits": hits, "total": total,
            "accuracy": accuracy if accuracy is not None else (hits / total if total else 0.0),
            "errors": 0,
        },
        "created_at": "2026-02-22T14:00:00Z",
        "dataset_run_items": items,
    }
    if rendered_prompt is not None:
        run["rendered_prompt_hash"] = rp_hash(rendered_prompt)
    return run


def build_eval_results(data: list[dict], hits: int):
    """Build (results, scores) for eval mocking — *hits* of *data* items are correct."""
    results = []
    for i, d in enumerate(data):
        hit = i < hits
        results.append({
            "query": d["query"],
            "predicted": d["ground_truth"] if hit else "WRONG",
            "ground_truth": d["ground_truth"],
            "hit": hit, "score": 1.0 if hit else 0.0, "error": None,
        })
    accuracy = hits / len(data) if data else 0.0
    scores = {"hits": hits, "total": len(data),
              "accuracy": accuracy, "errors": 0,
              "token_recall": 0.0, "composite": accuracy}
    return results, scores


class MockHTTPError(Exception):
    """Base class for mock HTTP errors used in tests."""


def make_http_error(status_code: int, message: str = "error"):
    """Create a mock HTTP error class with the given status_code."""
    return type(f"Mock{status_code}Error", (MockHTTPError,), {"status_code": status_code})(message)


async def run_simple_cycle(
    monkeypatch, eval_data, config, *, round_hits, **kwargs,
):
    """Apply standard mocks and run a feedback cycle. Returns CycleResult."""
    from api.services.campaign.optimization_loop import run_optimization

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=round_hits)

    defaults = {
        "instruction": "Rank candidates.",
        "eval_data": eval_data,
        "config": config,
        "baseline_prompt_fields": OptSearchPoint(instruction="Rank candidates.").model_dump(),
        "baseline_accuracy": 0.0,
        "baseline_results": [],
    }
    defaults.update(kwargs)
    return await run_optimization(**defaults)
