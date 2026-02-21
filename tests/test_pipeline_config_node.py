"""Tests for PipelineConfigNode."""
import pytest
from api.nodes.pipeline_config_node import PipelineConfigNode


@pytest.mark.asyncio
async def test_pipeline_config_node():
    # Defaults
    node = PipelineConfigNode(node_id="cfg1")
    result = await node.process({"query": "copper sheet"})
    assert result.query == "copper sheet"
    assert result.skip_llm_ranking is True

    # Input overrides config
    node2 = PipelineConfigNode(
        node_id="cfg2",
        config={"skip_llm_ranking": True, "pipeline_params": {"max_candidates": 10}},
    )
    result2 = await node2.process({
        "query": "steel plate",
        "skip_llm_ranking": False,
        "pipeline_params": {"model": "gpt-4"},
    })
    assert result2.skip_llm_ranking is False
    assert result2.pipeline_params == {"max_candidates": 10, "model": "gpt-4"}
