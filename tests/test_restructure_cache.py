"""Tests for alias-aware restructure caching in context.py."""

import pytest

from api.services.search.context import (
    load_cached_restructure,
    restructure_context_cached,
    save_restructure_cache,
)


SAMPLE_FIELDS = {
    "persona": "You are a candidate evaluation expert.",
    "task_intent": "Evaluate and rank candidates.",
    "problem_description": "Given an entity profile...",
    "instruction": "1. Summarize. 2. Rank.",
    "thinking_style": "Think step by step",
    "answer_format": "Return JSON.",
}


# -- low-level helpers --------------------------------------------------------


def test_save_and_load_round_trip(tmp_path):
    save_restructure_cache(
        tmp_path, "b1", "hash_abc", "areas", SAMPLE_FIELDS,
    )
    result = load_cached_restructure(
        tmp_path, "b1", {"hash_abc"}, "areas",
    )
    assert result == SAMPLE_FIELDS


def test_load_miss_empty_cache(tmp_path):
    result = load_cached_restructure(tmp_path, "b1", {"hash_abc"}, "")
    assert result is None


def test_load_miss_wrong_improvement_areas(tmp_path):
    save_restructure_cache(tmp_path, "b1", "hash_abc", "areas_v1", SAMPLE_FIELDS)
    result = load_cached_restructure(
        tmp_path, "b1", {"hash_abc"}, "areas_v2",
    )
    assert result is None


def test_load_hit_via_alias(tmp_path):
    """Cache keyed by hash_abc is found via alias set containing hash_abc."""
    save_restructure_cache(tmp_path, "b1", "hash_abc", "", SAMPLE_FIELDS)
    result = load_cached_restructure(
        tmp_path, "b1", {"hash_xyz", "hash_abc"}, "",
    )
    assert result == SAMPLE_FIELDS


# -- restructure_context_cached -----------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_calls_llm(tmp_path, monkeypatch):
    call_count = 0

    async def _mock_restructure(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        return dict(SAMPLE_FIELDS)

    monkeypatch.setattr(
        "api.services.search.context.restructure_context", _mock_restructure,
    )

    result, was_cached = await restructure_context_cached(
        "test instruction", None,
        store_base_dir=tmp_path, backend_id="b1",
        alias_hashes={"no_match"},
    )
    assert was_cached is False
    assert call_count == 1
    assert result["persona"] == SAMPLE_FIELDS["persona"]


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(tmp_path, monkeypatch):
    # Pre-populate cache
    save_restructure_cache(tmp_path, "b1", "hash_abc", "", SAMPLE_FIELDS)

    call_count = 0

    async def _mock_restructure(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        return {"persona": "DIFFERENT"}

    monkeypatch.setattr(
        "api.services.search.context.restructure_context", _mock_restructure,
    )

    result, was_cached = await restructure_context_cached(
        "test instruction", None,
        store_base_dir=tmp_path, backend_id="b1",
        alias_hashes={"hash_abc"},
    )
    assert was_cached is True
    assert call_count == 0
    assert result["persona"] == SAMPLE_FIELDS["persona"]


@pytest.mark.asyncio
async def test_force_bypasses_cache(tmp_path, monkeypatch):
    save_restructure_cache(tmp_path, "b1", "hash_abc", "", SAMPLE_FIELDS)

    fresh = {**SAMPLE_FIELDS, "persona": "FRESH"}

    async def _mock_restructure(*_a, **_kw):
        return dict(fresh)

    monkeypatch.setattr(
        "api.services.search.context.restructure_context", _mock_restructure,
    )

    result, was_cached = await restructure_context_cached(
        "test instruction", None,
        store_base_dir=tmp_path, backend_id="b1",
        alias_hashes={"hash_abc"},
        force=True,
    )
    assert was_cached is False
    assert result["persona"] == "FRESH"


@pytest.mark.asyncio
async def test_rp_hash_used_as_save_key(tmp_path, monkeypatch):
    """When rp_hash is provided, cache saves under that key (not context_input hash)."""
    async def _mock_restructure(*_a, **_kw):
        return dict(SAMPLE_FIELDS)

    monkeypatch.setattr(
        "api.services.search.context.restructure_context", _mock_restructure,
    )

    # First call: miss, saves under "render_hash"
    await restructure_context_cached(
        "raw instruction text (different from render)", None,
        store_base_dir=tmp_path, backend_id="b1",
        alias_hashes=set(),
        rp_hash="render_hash",
    )

    # Lookup by the render hash should hit
    cached = load_cached_restructure(tmp_path, "b1", {"render_hash"}, "")
    assert cached == SAMPLE_FIELDS


@pytest.mark.asyncio
async def test_no_store_fallthrough(monkeypatch):
    """When store_base_dir is None, caching is skipped entirely."""
    async def _mock_restructure(*_a, **_kw):
        return dict(SAMPLE_FIELDS)

    monkeypatch.setattr(
        "api.services.search.context.restructure_context", _mock_restructure,
    )

    result, was_cached = await restructure_context_cached(
        "test instruction", None,
    )
    assert was_cached is False
    assert result == SAMPLE_FIELDS
