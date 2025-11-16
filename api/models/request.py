"""
Request models for API endpoints
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any


class OptimizationRequest(BaseModel):
    """Request model for prompt optimization"""

    initial_prompt: str = Field(
        ...,
        description="The initial prompt to optimize",
        min_length=1
    )

    dataset: List[Dict[str, Any]] = Field(
        ...,
        description="Dataset of examples for optimization",
        min_length=1
    )

    target_metric: str = Field(
        default="accuracy",
        description="Metric to optimize for (accuracy, f1, precision, recall, custom)"
    )

    model: Optional[str] = Field(
        default=None,
        description="LLM model to use for optimization"
    )

    max_iterations: Optional[int] = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of optimization iterations"
    )

    custom_instructions: Optional[str] = Field(
        default=None,
        description="Additional instructions for the optimization process"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "initial_prompt": "Classify the sentiment of the following text:",
                "dataset": [
                    {"text": "I love this product!", "expected": "positive"},
                    {"text": "Terrible experience", "expected": "negative"}
                ],
                "target_metric": "accuracy",
                "model": "gpt-4",
                "max_iterations": 5
            }
        }
