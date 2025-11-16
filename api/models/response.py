"""
Response models for API endpoints
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


class IterationResult(BaseModel):
    """Result of a single optimization iteration"""

    iteration: int
    prompt: str
    score: float
    metrics: Dict[str, float]
    timestamp: str


class OptimizationResponse(BaseModel):
    """Response model for prompt optimization"""

    success: bool = Field(..., description="Whether optimization completed successfully")

    optimized_prompt: str = Field(..., description="The final optimized prompt")

    initial_score: float = Field(..., description="Score of initial prompt")

    final_score: float = Field(..., description="Score of optimized prompt")

    improvement: float = Field(..., description="Percentage improvement")

    iterations: List[IterationResult] = Field(
        ...,
        description="Results from each optimization iteration"
    )

    total_iterations: int = Field(..., description="Number of iterations performed")

    optimization_time: float = Field(..., description="Total optimization time in seconds")

    model_used: str = Field(..., description="LLM model used for optimization")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "optimized_prompt": "Analyze the sentiment of this text and respond with only 'positive' or 'negative':",
                "initial_score": 0.65,
                "final_score": 0.92,
                "improvement": 41.54,
                "iterations": [
                    {
                        "iteration": 1,
                        "prompt": "Classify sentiment:",
                        "score": 0.65,
                        "metrics": {"accuracy": 0.65},
                        "timestamp": "2025-11-16T14:00:00"
                    }
                ],
                "total_iterations": 3,
                "optimization_time": 45.2,
                "model_used": "gpt-4"
            }
        }


class ErrorResponse(BaseModel):
    """Error response model"""

    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
