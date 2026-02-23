"""
Pipeline configuration node.

Assembles pipeline configuration parameters (e.g., skip_llm_ranking,
model overrides, candidate limits) for forwarding to a backend.
PromptPotter configures *what* the backend does, not executes it directly.
"""
from typing import Any, Type

from pydantic import BaseModel, Field

from .base import NodeBase


class PipelineConfigInput(BaseModel):
    """Input model for pipeline config node."""

    query: str = Field(..., description="Query to configure the pipeline for")
    skip_llm_ranking: bool | None = Field(
        None, description="Override: skip LLM ranking stage"
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional pipeline parameters to forward to the backend",
    )


class PipelineConfigOutput(BaseModel):
    """Output model for pipeline config node."""

    query: str = Field(..., description="Original query (passed through)")
    skip_llm_ranking: bool = Field(
        default=True, description="Whether to skip LLM ranking"
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Merged pipeline parameters for the backend",
    )


class PipelineConfigNode(NodeBase[PipelineConfigInput, PipelineConfigOutput]):
    """
    Assemble pipeline configuration for a backend execution.

    Merges node-level config defaults with per-request overrides.
    The output is a structured configuration object that a backend
    connector (e.g., TermNorm) can consume directly.

    Config options:
        skip_llm_ranking: Default value for skip_llm_ranking (default: true)
        pipeline_params: Default pipeline parameters merged with input overrides
    """

    @classmethod
    def get_input_model(cls) -> Type[PipelineConfigInput]:
        return PipelineConfigInput

    @classmethod
    def get_output_model(cls) -> Type[PipelineConfigOutput]:
        return PipelineConfigOutput

    async def _execute(
        self, input_data: PipelineConfigInput
    ) -> PipelineConfigOutput:
        # Resolve skip_llm_ranking: input override > node config > default True
        skip = input_data.skip_llm_ranking
        if skip is None:
            skip = self.config.get("skip_llm_ranking", True)

        # Merge: node config defaults ← input overrides
        merged_params = {**self.config.get("pipeline_params", {})}
        merged_params.update(input_data.pipeline_params)

        return PipelineConfigOutput(
            query=input_data.query,
            skip_llm_ranking=skip,
            pipeline_params=merged_params,
        )
