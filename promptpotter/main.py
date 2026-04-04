"""
PromptPotter Optimizer API — main FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference

from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import APP_VERSION, settings
from promptpotter.routers import backends, campaigns

setup_logging()
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

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


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
        "timestamp": datetime.now(UTC).isoformat(),
        "version": APP_VERSION,
    }

# Include routers
app.include_router(_health, prefix="/api/v1", tags=["Health"])
app.include_router(backends.router, prefix="/api/v1", tags=["Backends"])
app.include_router(campaigns.router, prefix="/api/v1", tags=["Campaigns"])
