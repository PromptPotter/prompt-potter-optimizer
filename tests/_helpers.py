"""Shared test helpers — mock functions and classes used across test files.

Fixtures live in ``conftest.py``; this module holds plain functions and classes
that test modules can import directly.
"""

from api.services.llm_client import MockLLMClient


# ---------------------------------------------------------------------------
# Feedback-cycle mock helpers
# ---------------------------------------------------------------------------


def apply_init_mock(monkeypatch):
    """Mock restructure_context for InitNode."""
    async def mock_restructure(context_input, llm_client, **kwargs):
        return {
            "persona": "Expert",
            "instruction": "Rank by relevance",
            "thinking_style": "Step by step",
        }

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )


def apply_llm_mock(monkeypatch):
    """Mock get_llm_client to return a MockLLMClient."""
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


def apply_grow_mock(monkeypatch):
    """Mock generate_candidates to return deterministic variants."""
    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"variant_{i}_acc{accuracy:.0%}",
                changes_description=f"gen_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )


def apply_eval_mock(monkeypatch, round_hits=None):
    """Mock evaluate_prompt_cached with configurable per-round hit counts.

    Returns a ``call_count`` list ([int]) so callers can track invocations.
    """
    if round_hits is None:
        round_hits = [1, 2, 3]
    call_count = [0]

    async def mock_eval(ps, data, backend_client, **kwargs):
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
            scores = {"hits": target_hits, "total": len(data),
                      "accuracy": target_hits / len(data), "errors": 0}
            call_count[0] += 1
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0}
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
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.generations: list[dict] = []
        self.trace_updates: list[dict] = []
        self.end_trace_calls: list[str] = []
        self.flush_count = 0
        self._counter = 0
        self._rate_limit_until = 0.0

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
                    *, parent_observation_id=None, as_type="span"):
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output, "metadata": metadata,
            "as_type": as_type, "parent_observation_id": parent_observation_id,
        })
        return f"span_{name}"

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
