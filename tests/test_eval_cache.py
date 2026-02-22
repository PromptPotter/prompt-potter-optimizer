"""Tests for eval_cache_key determinism and sensitivity."""
from api.services.prompt_eval import eval_cache_key


def test_deterministic():
    """Same inputs → same hash."""
    data = [{"query": "q1", "ground_truth": "gt1"}]
    h1 = eval_cache_key("prompt", data, "model-a", 0.0)
    h2 = eval_cache_key("prompt", data, "model-a", 0.0)
    assert h1 == h2
    assert len(h1) == 16


def test_varies_on_prompt():
    data = [{"query": "q1", "ground_truth": "gt1"}]
    h1 = eval_cache_key("prompt A", data, "model", 0.0)
    h2 = eval_cache_key("prompt B", data, "model", 0.0)
    assert h1 != h2


def test_varies_on_model():
    data = [{"query": "q1", "ground_truth": "gt1"}]
    h1 = eval_cache_key("prompt", data, "model-a", 0.0)
    h2 = eval_cache_key("prompt", data, "model-b", 0.0)
    assert h1 != h2


def test_varies_on_temperature():
    data = [{"query": "q1", "ground_truth": "gt1"}]
    h1 = eval_cache_key("prompt", data, "model", 0.0)
    h2 = eval_cache_key("prompt", data, "model", 0.5)
    assert h1 != h2


def test_varies_on_data():
    data_a = [{"query": "q1", "ground_truth": "gt1"}]
    data_b = [{"query": "q2", "ground_truth": "gt2"}]
    h1 = eval_cache_key("prompt", data_a, "model", 0.0)
    h2 = eval_cache_key("prompt", data_b, "model", 0.0)
    assert h1 != h2


def test_order_independent():
    """Different query order → same hash (sorted internally)."""
    data_a = [
        {"query": "q1", "ground_truth": "gt1"},
        {"query": "q2", "ground_truth": "gt2"},
    ]
    data_b = [
        {"query": "q2", "ground_truth": "gt2"},
        {"query": "q1", "ground_truth": "gt1"},
    ]
    h1 = eval_cache_key("prompt", data_a, "model", 0.0)
    h2 = eval_cache_key("prompt", data_b, "model", 0.0)
    assert h1 == h2
