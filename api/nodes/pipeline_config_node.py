"""
Pipeline configuration node.

Assembles pipeline configuration parameters (e.g., model overrides,
candidate limits) for forwarding to a backend.
PromptPotter configures *what* the backend does, not executes it directly.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .base import NodeBase


class PipelineConfigInput(BaseModel):
    """Input model for pipeline config node."""

    query: str = Field(..., description="Query to configure the pipeline for")
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional pipeline parameters to forward to the backend",
    )


class PipelineConfigOutput(BaseModel):
    """Output model for pipeline config node."""

    query: str = Field(..., description="Original query (passed through)")
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
        pipeline_params: Default pipeline parameters merged with input overrides
    """

    @classmethod
    def get_input_model(cls) -> type[PipelineConfigInput]:
        return PipelineConfigInput

    @classmethod
    def get_output_model(cls) -> type[PipelineConfigOutput]:
        return PipelineConfigOutput

    async def _execute(
        self, input_data: PipelineConfigInput
    ) -> PipelineConfigOutput:
        # Merge: node config defaults <- input overrides
        merged_params = {**self.config.get("pipeline_params", {})}
        merged_params.update(input_data.pipeline_params)

        return PipelineConfigOutput(
            query=input_data.query,
            pipeline_params=merged_params,
        )
