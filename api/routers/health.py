"""
Health check endpoints.
"""
from datetime import datetime, timezone

from fastapi import APIRouter

from api.config.settings import APP_VERSION

router = APIRouter()


@router.get("/health")
async def health_check():
    """Return service status, timestamp, and version."""
    return {
        "status": "healthy",
        "service": "PromptPotter Optimizer",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
    }
