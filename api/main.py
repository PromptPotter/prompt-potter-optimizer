"""
PromptPotter Optimizer API — main FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference

from api.config.settings import APP_VERSION, settings
from api.routers import backends, campaigns

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("Starting %s v%s", app.title, app.version)
    logger.info("Environment: %s", settings.ENVIRONMENT)
    logger.info("Docs available at: /docs")
    yield
    logger.info("Shutting down PromptPotter Optimizer")


app = FastAPI(
    title="PromptPotter Optimizer",
    description="API-first prompt optimization service",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check
_health = APIRouter()

@_health.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "PromptPotter Optimizer",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
    }

# Include routers
app.include_router(_health, prefix="/api/v1", tags=["Health"])
app.include_router(backends.router, prefix="/api/v1", tags=["Backends"])
app.include_router(campaigns.router, prefix="/api/v1", tags=["Campaigns"])
