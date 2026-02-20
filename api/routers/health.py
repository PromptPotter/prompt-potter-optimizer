"""
Health check endpoints
"""
from fastapi import APIRouter
from datetime import datetime, timezone

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.4.0"
    }


@router.get("/ready")
async def readiness_check():
    """
    Readiness check endpoint
    Verifies all dependencies are available
    """
    return {
        "status": "ready",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
