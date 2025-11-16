"""
Prompt optimization endpoints
"""
from fastapi import APIRouter, HTTPException
from api.models.request import OptimizationRequest
from api.models.response import OptimizationResponse, ErrorResponse
from api.core.optimizer import PromptOptimizer
from api.config.settings import settings
import time

router = APIRouter()


@router.post("/optimize", response_model=OptimizationResponse)
async def optimize_prompt(request: OptimizationRequest):
    """
    Optimize a prompt based on provided dataset and target metric

    This endpoint takes an initial prompt and a dataset, then iteratively
    improves the prompt to maximize performance on the specified metric.

    Args:
        request: OptimizationRequest with initial prompt, dataset, and parameters

    Returns:
        OptimizationResponse with optimized prompt and performance metrics
    """
    try:
        # Validate dataset size
        if len(request.dataset) > settings.MAX_DATASET_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Dataset too large. Maximum size: {settings.MAX_DATASET_SIZE}"
            )

        # Initialize optimizer
        optimizer = PromptOptimizer(
            model=request.model or settings.DEFAULT_MODEL,
            max_iterations=request.max_iterations or settings.MAX_ITERATIONS
        )

        # Run optimization
        start_time = time.time()
        result = await optimizer.optimize(
            initial_prompt=request.initial_prompt,
            dataset=request.dataset,
            target_metric=request.target_metric,
            custom_instructions=request.custom_instructions
        )
        optimization_time = time.time() - start_time

        # Build response
        return OptimizationResponse(
            success=True,
            optimized_prompt=result["optimized_prompt"],
            initial_score=result["initial_score"],
            final_score=result["final_score"],
            improvement=result["improvement"],
            iterations=result["iterations"],
            total_iterations=len(result["iterations"]),
            optimization_time=optimization_time,
            model_used=request.model or settings.DEFAULT_MODEL
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")
