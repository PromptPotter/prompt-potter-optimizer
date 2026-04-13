"""Tests for PipelineSchema model and pipeline discovery.

Verifies derivation methods, parse_pipeline_response factory,
and registry metadata flow.
"""

from promptpotter.application.pipeline_discovery import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)


def test_derivation_methods():
    schema = PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(
                name="search",
                param_keys={"max_results"},
                observation_name="search",
                observation_mappings=[
                    ObservationMapping(pipeline_key="results", output_field="items"),
                ],
                langfuse_type="tool",
            ),
            PipelineNode(
                name="rank",
                param_keys={"temperature"},
                observation_name="rank",
                observation_mappings=[
                    ObservationMapping(pipeline_key="ranked", is_llm=True),
                ],
                langfuse_type="generation",
            ),
            PipelineNode(
                name="cache",
                langfuse_type="span",
            ),
        ],
    )

    # node_param_keys
    assert schema.node_param_keys() == {
        "search": {"max_results"},
        "rank": {"temperature"},
    }
    assert "cache" not in schema.node_param_keys()

    # obs_extraction_map
    obs_map = schema.obs_extraction_map()
    assert set(obs_map.keys()) == {"search", "rank"}
    assert obs_map["search"][0].pipeline_key == "results"
    assert obs_map["rank"][0].is_llm is True


def test_parse_pipeline_response():
    """Self-describing TermNorm pipeline → PipelineSchema with ordered, typed nodes."""
    data = {
        "config": {
            "name": "TermNorm",
            "version": "2.0",
            "nodes": {
                "cache_lookup": {
                    "type": "cache",
                    "short_circuit": True,
                    "node_role": "cache",
                    "config": {},
                    "optimizer": {"langfuse_type": "span"},
                },
                "web_search": {
                    "type": "tool",
                    "node_role": "enricher",
                    "config": {"max_sites": 7},
                    "optimizer": {
                        "param_keys": ["max_sites"],
                        "observation_name": "web_search",
                        "observation_mappings": [{"pipeline_key": "web_sources"}],
                        "langfuse_type": "tool",
                    },
                },
            },
            "pipelines": {"default": ["cache_lookup", "web_search"]},
        },
    }
    schema = parse_pipeline_response(data)

    assert schema.name == "termnorm"
    assert schema.version == "2.0"
    assert [s.name for s in schema.nodes] == ["cache_lookup", "web_search"]
    assert schema.nodes[0].short_circuit is True
    assert schema.nodes[0].node_type == "cache"
    assert schema.nodes[1].param_keys == {"max_sites"}
    assert schema.nodes[1].observation_mappings[0].pipeline_key == "web_sources"


def _three_node_schema() -> PipelineSchema:
    """Helper: a → b → c pipeline with param_keys on a and b."""
    return PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(name="a", param_keys={"max_results"}),
            PipelineNode(
                name="b",
                param_keys={"temperature"},
                prompt_meta=NodePromptMeta(family="p"),
            ),
            PipelineNode(name="c"),
        ],
    )


class TestCoordinateLookups:
    def test_has_node(self):
        schema = _three_node_schema()
        assert schema.has_node("a")
        assert schema.has_node("b")
        assert not schema.has_node("z")

    def test_exclude(self):
        schema = _three_node_schema()
        reduced = schema.exclude({"b"})
        assert [n.name for n in reduced.nodes] == ["a", "c"]
        # None / empty → identity
        assert schema.exclude(None) is schema
        assert schema.exclude(set()) is schema

    def test_prefix_keys_stable_and_chained(self):
        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}, "b": {"temperature": 0.7}}
        keys = schema.prefix_keys(pp)
        assert len(keys) == 3
        assert all(len(k) == 16 for _, k in keys)
        names = [name for name, _ in keys]
        assert names == ["a", "b", "c"]
        # Changing upstream config changes downstream keys
        pp2 = {"a": {"max_results": 10}, "b": {"temperature": 0.7}}
        keys2 = schema.prefix_keys(pp2)
        assert keys2[0][1] != keys[0][1]  # a changed
        assert keys2[1][1] != keys[1][1]  # b cascaded
        assert keys2[2][1] != keys[2][1]  # c cascaded

    def test_prefix_keys_matches_node_cache_key(self):
        """prefix_keys produces the same hashes as node_cache_key."""
        from promptpotter.domain.pipeline_schema import node_cache_key

        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}}
        keys = schema.prefix_keys(pp)
        # First node: upstream=""
        assert keys[0][1] == node_cache_key("a", {"max_results": 5}, "")
        # Second node: upstream=first key
        assert keys[1][1] == node_cache_key("b", {}, keys[0][1])

    def test_sp_hash_is_terminal_of_prefix_keys(self):
        """sp_hash returns the terminal element of prefix_keys."""
        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}, "b": {"temperature": 0.7}}
        chain = schema.prefix_keys(pp)
        assert schema.sp_hash(pp) == chain[-1][1]

    def test_sp_hash_empty_schema(self):
        schema = PipelineSchema(nodes=[])
        assert schema.sp_hash({}) == ""

    def test_sp_hash_changes_with_config(self):
        schema = _three_node_schema()
        h1 = schema.sp_hash({"a": {"max_results": 5}})
        h2 = schema.sp_hash({"a": {"max_results": 10}})
        assert h1 != h2
