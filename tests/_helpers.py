"""Shared test helpers — mock functions and classes used across test files.

Fixtures live in ``conftest.py``; this module holds plain functions and classes
that test modules can import directly.
"""

import hashlib

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.models.hashing import HASH_TRUNCATE


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
    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"variant_{i}_acc{accuracy:.0%}",
                changes_description=f"gen_{i}",
            ).model_dump()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.l1_generate",
        mock_generate,
    )


def apply_eval_mock(monkeypatch, round_hits=None):
    """Mock evaluate_prompt_cached with configurable per-round hit counts.

    The first positional arg is now a ``SearchPoint`` (not a raw PromptState).
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
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )
    return call_count


# ---------------------------------------------------------------------------
# Mock Langfuse logger (superset of integration + backfill needs)
# ---------------------------------------------------------------------------


class MockLangfuseLogger:
    """Records all Langfuse calls for test verification.

    Covers both feedback-cycle integration tests (traces, spans, scores,
    generations) and backfill tests (dataset API, enabled flag, rate limiting).
    """

    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.traces: list[dict] = []
        self.trace_ids: list[str] = []  # bare trace IDs (from create_trace_id)
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.generations: list[dict] = []
        self.trace_updates: list[dict] = []
        self.end_trace_calls: list[str] = []
        self.flush_count = 0
        self._counter = 0
        self._rate_limit_until = 0.0

        # Top-level observations (pipeline graph nodes)
        self.top_level_observations: list[dict] = []

        # Dataset API tracking
        self.datasets_created: list[dict] = []
        self.dataset_items_created: list[dict] = []
        self.dataset_items_updated: list[dict] = []
        self.dataset_gets: list[str] = []
        self.dataset_run_links: list[dict] = []
        self._item_counter = 0

    @property
    def rate_limited(self) -> bool:
        import time
        return time.time() < self._rate_limit_until

    def create_trace_id(self):
        if not self.enabled:
            return None
        self._counter += 1
        tid = f"mock_trace_{self._counter:03d}"
        self.trace_ids.append(tid)
        return tid

    def create_trace(self, name, input, metadata=None, user_id=None,
                     session_id=None, tags=None):
        if not self.enabled:
            return None
        self._counter += 1
        tid = f"mock_trace_{self._counter:03d}"
        self.traces.append({
            "id": tid, "name": name, "input": input, "metadata": metadata,
            "session_id": session_id, "tags": tags,
        })
        return tid

    def start_span(self, trace_id, name, input=None, metadata=None,
                   *, parent_observation_id=None, as_type="span"):
        self._counter += 1
        obs_id = f"open_obs_{self._counter:03d}"
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": None, "metadata": metadata,
            "obs_id": obs_id, "open": True,
            "as_type": as_type, "parent_observation_id": parent_observation_id,
        })
        return obs_id

    def end_observation(self, obs_id, output=None, metadata=None):
        for span in self.spans:
            if span.get("obs_id") == obs_id and span.get("open"):
                span["output"] = output
                if metadata:
                    span["metadata"] = metadata
                span["open"] = False
                break

    def create_span(self, trace_id, name, input, output, metadata=None,
                    *, parent_observation_id=None, as_type="span",
                    model=None, usage_details=None):
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output, "metadata": metadata,
            "as_type": as_type, "parent_observation_id": parent_observation_id,
            "model": model, "usage_details": usage_details,
        })
        return f"span_{name}"

    def create_top_level_observation(
        self, trace_id, name, as_type, input, output,
        metadata=None, model=None, usage_details=None,
        *, trace_params=None,
    ):
        record = {
            "trace_id": trace_id, "name": name, "as_type": as_type,
            "input": input, "output": output, "metadata": metadata,
            "model": model, "usage_details": usage_details,
            "top_level": True, "trace_params": trace_params,
        }
        self.top_level_observations.append(record)
        # Also record in spans for backward compat with existing count assertions
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output, "metadata": metadata,
            "as_type": as_type, "parent_observation_id": None,
            "top_level": True,
        })
        return f"tl_{name}"

    def create_generation(self, trace_id, name, model, input, output,
                          usage=None, metadata=None):
        self.generations.append({
            "trace_id": trace_id, "name": name, "model": model,
        })
        return f"gen_{name}"

    def create_score(self, trace_id, name, value, data_type="NUMERIC",
                     comment=None):
        self.scores.append({
            "trace_id": trace_id, "name": name,
            "value": value, "comment": comment,
        })
        return True

    def update_trace(self, trace_id, output=None, metadata=None):
        self.trace_updates.append({
            "trace_id": trace_id, "output": output, "metadata": metadata,
        })
        return True

    def end_trace(self, trace_id):
        self.end_trace_calls.append(trace_id)

    def flush(self):
        self.flush_count += 1

    # Dataset API

    def create_dataset(self, name, description=None, metadata=None):
        self.datasets_created.append({
            "name": name, "description": description, "metadata": metadata,
        })
        return True

    def create_dataset_item(self, dataset_name, input, expected_output=None,
                            metadata=None):
        self._item_counter += 1
        item_id = f"item_{self._item_counter:03d}"
        self.dataset_items_created.append({
            "id": item_id, "dataset_name": dataset_name,
            "input": input, "expected_output": expected_output,
        })
        return item_id

    def get_dataset(self, name):
        self.dataset_gets.append(name)
        return type("Dataset", (), {"name": name, "items": []})()

    def update_dataset_item(self, item_id, expected_output=None, metadata=None):
        self.dataset_items_updated.append({
            "item_id": item_id, "expected_output": expected_output,
        })
        return True

    def link_item_to_run(self, dataset_item_id, trace_id,
                         observation_id=None, run_name="", run_metadata=None):
        self.dataset_run_links.append({
            "dataset_item_id": dataset_item_id,
            "trace_id": trace_id,
            "observation_id": observation_id,
            "run_name": run_name,
            "run_metadata": run_metadata,
        })
        return True


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

    choices = [Choice()]

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


def make_baseline_ps(**overrides) -> PromptState:
    """Build a baseline PromptState with sensible defaults."""
    defaults = {
        "instruction": "Rank the candidates.",
        "persona": "",
        "task_intent": "",
        "thinking_style": "",
        "answer_format": "",
    }
    defaults.update(overrides)
    return PromptState(**defaults)


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
        "prompt_state_id": f"ps_{run_id}",
        "model": "test-model",
        "temperature": 0,
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


def make_http_error(status_code: int, message: str = "error"):
    """Create a mock HTTP error class with the given status_code."""
    return type(f"Mock{status_code}Error", (Exception,), {"status_code": status_code})(message)


async def run_simple_cycle(
    monkeypatch, eval_data, config, *, round_hits, **kwargs,
):
    """Apply standard mocks and run a feedback cycle. Returns CycleResult."""
    from api.services.campaign.feedback_cycle import run_feedback_cycle

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=round_hits)

    defaults = dict(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=PromptState(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )
    defaults.update(kwargs)
    return await run_feedback_cycle(**defaults)
