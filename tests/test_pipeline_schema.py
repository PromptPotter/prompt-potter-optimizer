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
    def test_exclude_drops_named_nodes_and_returns_self_for_empty(self):
        schema = _three_node_schema()
        assert [n.name for n in schema.exclude({"b"}).nodes] == ["a", "c"]
        assert schema.exclude(None) is schema
        assert schema.exclude(set()) is schema

    def test_node_configs_preserves_order_and_fills_empty(self):
        schema = _three_node_schema()
        configs = schema.node_configs({"a": {"max_results": 5}, "b": {"temperature": 0.7}})
        assert [name for name, _ in configs] == ["a", "b", "c"]
        assert configs[0][1] == {"max_results": 5}
        assert configs[2][1] == {}  # missing node → empty dict

    def test_sp_hash_distinguishes_configs_and_handles_empty_schema(self):
        from promptpotter.domain.pipeline_schema import stable_hash

        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}}
        assert schema.sp_hash(pp) == stable_hash(schema.node_configs(pp))
        assert schema.sp_hash(pp) != schema.sp_hash({"a": {"max_results": 10}})
        assert PipelineSchema(nodes=[]).sp_hash({}) == ""
