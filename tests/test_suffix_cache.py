"""Tests for SuffixCache — full hit, partial reuse, deterministic keys."""

from promptpotter.domain.pipeline_schema import stable_hash, suffix_key
from promptpotter.infrastructure.store.suffix_cache import SuffixCache


def _configs(a: int = 1, b: int = 2, c: int = 3) -> list[tuple[str, dict]]:
    return [("A", {"x": a}), ("B", {"x": b}), ("C", {"x": c})]


def _outputs() -> dict[str, dict]:
    return {"A": {"out": "a"}, "B": {"out": "b"}, "C": {"out": "c"}}


def test_suffix_key_deterministic_and_sensitive():
    k1 = suffix_key("qh", _configs())
    k2 = suffix_key("qh", _configs())
    k3 = suffix_key("qh", _configs(c=99))
    assert k1 == k2
    assert k1 != k3


def test_full_hit_after_populate(tmp_path):
    cache = SuffixCache(tmp_path)
    cache.populate_from_run("bid", "query-1", _configs(), _outputs())
    key = suffix_key(stable_hash("query-1"), _configs())
    hit = cache.lookup("bid", key)
    assert hit is not None
    assert set(hit["covers"]) == {"A", "B", "C"}
    assert hit["node_outputs"] == _outputs()


def test_downstream_change_reuses_prefix(tmp_path):
    """Changing node C's config must still hit the A+B prefix from the start."""
    cache = SuffixCache(tmp_path)
    cache.populate_from_run("bid", "query-1", _configs(), _outputs())

    q_hash = stable_hash("query-1")
    new_configs = _configs(c=99)

    # Full key misses
    assert cache.lookup("bid", suffix_key(q_hash, new_configs)) is None
    # A+B prefix hits
    prefix_hit = cache.lookup("bid", suffix_key(q_hash, new_configs[:2]))
    assert prefix_hit is not None
    assert prefix_hit["covers"] == ["A", "B"]


def test_upstream_change_misses_everything(tmp_path):
    """Changing node A's config must invalidate all prefixes from start."""
    cache = SuffixCache(tmp_path)
    cache.populate_from_run("bid", "query-1", _configs(), _outputs())

    q_hash = stable_hash("query-1")
    new_configs = _configs(a=99)
    for j in range(1, 4):
        assert cache.lookup("bid", suffix_key(q_hash, new_configs[:j])) is None


def test_short_circuit_only_emits_executed_prefix(tmp_path):
    """If the backend short-circuits, only executed nodes' cut-points are emitted."""
    cache = SuffixCache(tmp_path)
    partial_outputs = {"A": {"out": "a"}, "B": {"out": "b"}}  # C missing
    cache.populate_from_run("bid", "query-1", _configs(), partial_outputs)

    q_hash = stable_hash("query-1")
    assert cache.lookup("bid", suffix_key(q_hash, _configs()[:2])) is not None
    assert cache.lookup("bid", suffix_key(q_hash, _configs())) is None
