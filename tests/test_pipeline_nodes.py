"""Tests for pipeline node extraction.

Verifies:
- Full pipeline → 4 nodes in correct order
- Missing steps → fewer nodes
- Correct as_type per node
- model set on generation nodes only
- Timing metadata populated from step_timings
- Per-step pipeline_params in metadata
- _profile_summary helper
"""

import pytest

from api.services.obs.pipeline_nodes import (
    extract_pipeline_nodes,
    _profile_summary,
)


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
# Tests — extract_pipeline_nodes
# ---------------------------------------------------------------------------


class TestExtractPipelineNodes:
    def test_full_pipeline_returns_4_nodes(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        assert len(nodes) == 4

    def test_node_order(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        names = [n.name for n in nodes]
        assert names == ["web_search", "entity_profiling", "token_matching", "llm_ranking"]

    def test_as_type_mapping(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        type_map = {n.name: n.as_type for n in nodes}
        assert type_map == {
            "web_search": "tool",
            "entity_profiling": "generation",
            "token_matching": "retriever",
            "llm_ranking": "generation",
        }

    def test_model_set_on_generation_nodes_only(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        for node in nodes:
            if node.as_type == "generation":
                assert node.model == "groq/llama-4"
            else:
                assert node.model is None

    def test_model_none_when_llm_provider_empty(self):
        pipeline = _full_pipeline()
        pipeline["llm_provider"] = ""
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        for node in nodes:
            assert node.model is None

    def test_timing_metadata(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        timing_map = {n.name: n.metadata.get("duration_s") for n in nodes}
        assert timing_map == {
            "web_search": 0.3,
            "entity_profiling": 1.2,
            "token_matching": 0.05,
            "llm_ranking": 0.8,
        }

    def test_no_timings_means_empty_metadata(self):
        pipeline = _full_pipeline()
        del pipeline["step_timings"]
        del pipeline["pipeline_params"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        for node in nodes:
            assert "duration_s" not in node.metadata

    def test_nodes_are_frozen(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        with pytest.raises(AttributeError):
            nodes[0].name = "modified"


class TestMissingSteps:
    def test_no_web_search(self):
        pipeline = _full_pipeline()
        del pipeline["web_search_status"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        names = [n.name for n in nodes]
        assert "web_search" not in names
        assert len(nodes) == 3

    def test_no_entity_profile(self):
        pipeline = _full_pipeline()
        del pipeline["entity_profile"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        names = [n.name for n in nodes]
        assert "entity_profiling" not in names
        # token_matching still has profile="" fallback
        assert "token_matching" in names

    def test_no_token_matching(self):
        pipeline = _full_pipeline()
        del pipeline["token_matched_candidates"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        names = [n.name for n in nodes]
        assert "token_matching" not in names
        assert len(nodes) == 3

    def test_no_llm_ranking(self):
        pipeline = _full_pipeline()
        del pipeline["ranked_candidates"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        names = [n.name for n in nodes]
        assert "llm_ranking" not in names
        assert len(nodes) == 3

    def test_empty_pipeline(self):
        nodes = extract_pipeline_nodes({}, "aspirin")
        assert nodes == []

    def test_only_web_search(self):
        pipeline = {"web_search_status": "success"}
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        assert len(nodes) == 1
        assert nodes[0].name == "web_search"
        assert nodes[0].as_type == "tool"


class TestNodeContent:
    def test_web_search_io(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        ws = nodes[0]
        assert ws.input == {"query": "aspirin"}
        assert ws.output["status"] == "success"
        assert ws.output["error"] is None
        assert ws.output["n_sources"] == 1
        assert len(ws.output["sources"]) == 1
        assert ws.output["sources"][0]["title"] == "Aspirin - Wikipedia"

    def test_web_search_failed(self):
        pipeline = _full_pipeline()
        pipeline["web_search_status"] = "failed"
        pipeline["web_search_error"] = "timeout"
        pipeline["web_sources"] = []
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        ws = nodes[0]
        assert ws.output["status"] == "failed"
        assert ws.output["error"] == "timeout"
        assert ws.output["sources"] == []
        assert ws.output["n_sources"] == 0

    def test_entity_profiling_io(self):
        pipeline = _full_pipeline()
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        ep = nodes[1]
        assert ep.input == {"query": "aspirin"}
        # Full profile dict as output (not just 2 keys)
        assert ep.output["core_concept"] == "pain reliever"
        assert ep.output["entity_name"] == "Aspirin"
        assert ep.output is pipeline["entity_profile"]

    def test_token_matching_io(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        tm = nodes[2]
        assert tm.input["query"] == "aspirin"
        assert tm.input["profile"] == "Aspirin (pain reliever)"
        assert tm.output["n_candidates"] == 2
        # Full candidate list (not truncated top_3)
        assert tm.output["candidates"] == [("Aspirin", 5), ("Aspirin Tablet", 3)]
        assert "top_3" not in tm.output

    def test_llm_ranking_io(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        lr = nodes[3]
        assert lr.input == {"n_candidates": 2}
        assert lr.output["n_candidates"] == 2
        # Full ranked candidates list (not just top_candidate/top_score)
        assert len(lr.output["candidates"]) == 2
        assert lr.output["candidates"][0]["candidate"] == "Aspirin"
        assert lr.output["candidates"][0]["relevance_score"] == 0.95
        assert "top_candidate" not in lr.output
        assert "top_score" not in lr.output


class TestPipelineParamsInMetadata:
    def test_web_search_params(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        ws = nodes[0]
        pp = ws.metadata["pipeline_params"]
        assert pp == {"max_sites": 3, "num_results": 5, "content_char_limit": 5000}

    def test_entity_profiling_params(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        ep = nodes[1]
        pp = ep.metadata["pipeline_params"]
        assert pp == {
            "raw_content_limit": 3000,
            "profiling_temperature": 0.1,
            "profiling_max_tokens": 512,
        }

    def test_token_matching_params(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        tm = nodes[2]
        pp = tm.metadata["pipeline_params"]
        assert pp == {"max_token_candidates": 20, "relevance_weight_core": 0.7}

    def test_llm_ranking_params(self):
        nodes = extract_pipeline_nodes(_full_pipeline(), "aspirin")
        lr = nodes[3]
        pp = lr.metadata["pipeline_params"]
        assert pp == {
            "ranking_temperature": 0.0,
            "ranking_max_tokens": 1024,
            "ranking_sample_size": 10,
        }

    def test_no_pipeline_params_means_no_key(self):
        pipeline = _full_pipeline()
        del pipeline["pipeline_params"]
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        for node in nodes:
            assert "pipeline_params" not in node.metadata

    def test_partial_pipeline_params(self):
        pipeline = _full_pipeline()
        pipeline["pipeline_params"] = {"max_sites": 3}  # only web_search param
        nodes = extract_pipeline_nodes(pipeline, "aspirin")
        # web_search has it
        assert nodes[0].metadata["pipeline_params"] == {"max_sites": 3}
        # Other nodes don't
        for node in nodes[1:]:
            assert "pipeline_params" not in node.metadata


# ---------------------------------------------------------------------------
# Tests — _profile_summary
# ---------------------------------------------------------------------------


class TestProfileSummary:
    def test_both_fields(self):
        assert _profile_summary({"entity_name": "Asp", "core_concept": "pain"}) == "Asp (pain)"

    def test_name_only(self):
        assert _profile_summary({"entity_name": "Asp"}) == "Asp"

    def test_concept_only(self):
        assert _profile_summary({"core_concept": "pain"}) == "pain"

    def test_empty(self):
        assert _profile_summary({}) == ""
