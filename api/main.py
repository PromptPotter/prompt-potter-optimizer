"""
PromptPotter Optimizer API
Main FastAPI application entry point
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.config.settings import settings
from api.routers import backends, health, workflows


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    print(f"Starting {app.title} v{app.version}")
    print(f"Environment: {settings.ENVIRONMENT}")
    print("Docs available at: /docs")
    yield
    print("Shutting down PromptPotter Optimizer")


app = FastAPI(
    title="PromptPotter Optimizer",
    description="API-first prompt optimization service",
    version="0.5.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
app.include_router(backends.router, prefix="/api/v1", tags=["Backends"])
