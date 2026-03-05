"""Tests for pipeline node extraction.

Verifies full pipeline extraction, missing step handling, node I/O content,
per-step pipeline_params, and _profile_summary helper.
"""

import pytest

from api.services.obs.pipeline_nodes import (
    extract_pipeline_nodes,
    _profile_summary,
)
from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _full_pipeline():
    """Pipeline data with all 4 steps present."""
    return {
        "web_search_status": "success",
        "web_search_error": None,
        "web_sources": [
            {"title": "Aspirin - Wikipedia", "url": "https://en.wikipedia.org/wiki/Aspirin"},
        ],
        "entity_profile": {
            "core_concept": "pain reliever",
            "entity_name": "Aspirin",
        },
        "token_matched_candidates": [
            ("Aspirin", 5), ("Aspirin Tablet", 3),
        ],
        "ranked_candidates": [
            {"candidate": "Aspirin", "relevance_score": 0.95},
            {"candidate": "Aspirin Tablet", "relevance_score": 0.70},
        ],
        "step_timings": {
            "web_search": 0.3,
            "entity_profiling": 1.2,
            "token_matching": 0.05,
            "llm_ranking": 0.8,
        },
        "llm_provider": "groq/llama-4",
        "total_time": 2.1,
        "pipeline_params": {
            "max_sites": 3,
            "num_results": 5,
            "content_char_limit": 5000,
            "raw_content_limit": 3000,
            "profiling_temperature": 0.1,
            "profiling_max_tokens": 512,
            "max_token_candidates": 20,
            "relevance_weight_core": 0.7,
            "ranking_temperature": 0.0,
            "ranking_max_tokens": 1024,
            "ranking_sample_size": 10,
        },
    }


# ---------------------------------------------------------------------------
# Full pipeline — comprehensive happy-path test
# ---------------------------------------------------------------------------


def test_full_pipeline_extraction():
    """One comprehensive test: order, types, model, timings, params."""
    nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)

    # 4 nodes in correct order
    assert [n.name for n in nodes] == [
        "web_search", "entity_profiling", "token_matching", "llm_ranking",
    ]

    # as_type mapping
    assert {n.name: n.as_type for n in nodes} == {
        "web_search": "tool",
        "entity_profiling": "generation",
        "token_matching": "retriever",
        "llm_ranking": "generation",
    }

    # model only on generation nodes
    for node in nodes:
        if node.as_type == "generation":
            assert node.model == "groq/llama-4"
        else:
            assert node.model is None

    # timing metadata
    assert {n.name: n.metadata["duration_s"] for n in nodes} == {
        "web_search": 0.3,
        "entity_profiling": 1.2,
        "token_matching": 0.05,
        "llm_ranking": 0.8,
    }

    # per-step pipeline_params
    assert nodes[0].metadata["pipeline_params"] == {
        "max_sites": 3, "num_results": 5, "content_char_limit": 5000,
    }
    assert nodes[1].metadata["pipeline_params"] == {
        "raw_content_limit": 3000,
        "profiling_temperature": 0.1,
        "profiling_max_tokens": 512,
    }
    assert nodes[2].metadata["pipeline_params"] == {
        "max_token_candidates": 20, "relevance_weight_core": 0.7,
    }
    assert nodes[3].metadata["pipeline_params"] == {
        "ranking_temperature": 0.0,
        "ranking_max_tokens": 1024,
        "ranking_sample_size": 10,
    }


# ---------------------------------------------------------------------------
# Missing steps — parametrized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("del_key,missing_step,expected_count", [
    ("web_search_status", "web_search", 3),
    ("entity_profile", "entity_profiling", 3),
    ("token_matched_candidates", "token_matching", 3),
    ("ranked_candidates", "llm_ranking", 3),
])
def test_missing_step(del_key, missing_step, expected_count):
    pipeline = _full_pipeline()
    del pipeline[del_key]
    nodes = extract_pipeline_nodes(pipeline, "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    names = [n.name for n in nodes]
    assert missing_step not in names
    assert len(nodes) == expected_count


# ---------------------------------------------------------------------------
# Node I/O content
# ---------------------------------------------------------------------------


def test_web_search_io():
    nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    ws = nodes[0]
    assert ws.input == {"query": "aspirin"}
    assert ws.output["status"] == "success"
    assert ws.output["error"] is None
    assert ws.output["n_sources"] == 1
    assert ws.output["sources"][0]["title"] == "Aspirin - Wikipedia"


def test_web_search_failed():
    pipeline = _full_pipeline()
    pipeline["web_search_status"] = "failed"
    pipeline["web_search_error"] = "timeout"
    pipeline["web_sources"] = []
    nodes = extract_pipeline_nodes(pipeline, "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    ws = nodes[0]
    assert ws.output["status"] == "failed"
    assert ws.output["error"] == "timeout"
    assert ws.output["n_sources"] == 0


def test_entity_profiling_io():
    pipeline = _full_pipeline()
    nodes = extract_pipeline_nodes(pipeline, "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    ep = nodes[1]
    assert ep.input == {"query": "aspirin"}
    assert ep.output is pipeline["entity_profile"]


def test_token_matching_io():
    nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    tm = nodes[2]
    assert tm.input["query"] == "aspirin"
    assert tm.input["profile"] == "Aspirin (pain reliever)"
    assert tm.output["n_candidates"] == 2
    assert tm.output["candidates"] == [("Aspirin", 5), ("Aspirin Tablet", 3)]


def test_llm_ranking_io():
    nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    lr = nodes[3]
    assert lr.input == {"n_candidates": 2}
    assert lr.output["candidates"][0]["candidate"] == "Aspirin"
    assert lr.output["candidates"][0]["relevance_score"] == 0.95


# ---------------------------------------------------------------------------
# _profile_summary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile,expected", [
    ({"entity_name": "Asp", "core_concept": "pain"}, "Asp (pain)"),
    ({"entity_name": "Asp"}, "Asp"),
    ({"core_concept": "pain"}, "pain"),
])
def test_profile_summary(profile, expected):
    assert _profile_summary(profile) == expected


# ---------------------------------------------------------------------------
# Schema-based extraction — same output as hardcoded path
# ---------------------------------------------------------------------------


def test_schema_extraction_deterministic():
    """extract_pipeline_nodes with TERMNORM_DEFAULT_SCHEMA is deterministic across calls."""
    pipeline = _full_pipeline()
    nodes_a = extract_pipeline_nodes(pipeline, "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)
    nodes_b = extract_pipeline_nodes(pipeline, "aspirin", schema=TERMNORM_DEFAULT_SCHEMA)

    assert len(nodes_a) == len(nodes_b)
    for a, b in zip(nodes_a, nodes_b):
        assert a.name == b.name
        assert a.as_type == b.as_type
        assert a.metadata == b.metadata
        assert a.model == b.model
