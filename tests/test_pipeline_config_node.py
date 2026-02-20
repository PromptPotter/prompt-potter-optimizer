"""Tests for PipelineConfigNode."""
import pytest
from api.nodes.pipeline_config_node import PipelineConfigNode


@pytest.mark.asyncio
async def test_default_config():
    node = PipelineConfigNode(node_id="cfg1")
    result = await node.process({"query": "copper sheet"})
    assert result.query == "copper sheet"
    assert result.skip_llm_ranking is True
    assert result.pipeline_params == {}


@pytest.mark.asyncio
async def test_input_overrides():
    node = PipelineConfigNode(
        node_id="cfg2",
        config={"skip_llm_ranking": True, "pipeline_params": {"max_candidates": 10}},
    )
    result = await node.process({
        "query": "steel plate",
        "skip_llm_ranking": False,
        "pipeline_params": {"model": "gpt-4"},
    })
    assert result.skip_llm_ranking is False
    assert result.pipeline_params == {"max_candidates": 10, "model": "gpt-4"}


@pytest.mark.asyncio
async def test_node_config_defaults():
    node = PipelineConfigNode(
        node_id="cfg3",
        config={"skip_llm_ranking": False, "pipeline_params": {"temperature": 0.0}},
    )
    result = await node.process({"query": "aluminum alloy"})
    assert result.skip_llm_ranking is False
    assert result.pipeline_params == {"temperature": 0.0}
