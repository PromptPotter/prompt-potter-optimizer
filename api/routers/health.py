"""
Health check endpoints
"""
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Health check endpoint
    Returns service status and timestamp
    """
    return {
        "status": "healthy",
        "service": "PromptPotter Optimizer",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.1.0"
    }


@router.get("/ready")
async def readiness_check():
    """
    Readiness check endpoint
    Verifies all dependencies are available
    """
    # TODO: Add checks for LLM provider connectivity
    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat()
    }
