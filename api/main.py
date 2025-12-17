"""
PromptPotter Optimizer API
Main FastAPI application entry point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.config.settings import settings
from api.routers import health, optimize, workflows

app = FastAPI(
    title="PromptPotter Optimizer",
    description="API-first prompt optimization service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
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
app.include_router(optimize.router, prefix="/api/v1", tags=["Optimization"])
app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    print(f"Starting {app.title} v{app.version}")
    print(f"Environment: {settings.ENVIRONMENT}")
    print(f"Docs available at: /docs")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("Shutting down PromptPotter Optimizer")
