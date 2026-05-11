"""
PromptPotter Optimizer API — main FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference
from starlette.middleware.base import BaseHTTPMiddleware

from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import APP_VERSION, settings
from promptpotter.presentation import api

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("Starting %s v%s", app.title, app.version)
    logger.info("Environment: %s", settings.ENVIRONMENT)
    logger.info("Webapp available at: /ui/  (root / redirects there)")
    logger.info("API docs available at: /docs")
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


class NoStoreOnApiMiddleware(BaseHTTPMiddleware):
    """Set ``Cache-Control: no-store`` on every ``/api/v1/*`` response.

    The API is the webapp's live polling surface — ``dashboard.json``,
    per-cycle round files, file listings, active-session pointer. Any
    browser/intermediate caching is a freshness bug, not an optimization,
    because we already serve from in-memory state and the on-disk files
    are tiny. Static webapp assets under ``/ui/`` are unaffected (served
    by StaticFiles with its own caching defaults).
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(NoStoreOnApiMiddleware)

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
app.include_router(api.backends_router, prefix="/api/v1", tags=["Backends"])
app.include_router(api.campaigns_router, prefix="/api/v1", tags=["Campaigns"])
app.include_router(api._active_router, prefix="/api/v1")
app.include_router(api._datasets_router, prefix="/api/v1", tags=["Datasets"])

# Static webapp mount — read-only operator dashboard at /ui (Next.js export
# from webapp/, built via `npm run build` in that directory).
WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp" / "out"
if WEBAPP_DIR.exists():
    app.mount("/ui", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")

    # Root redirect — Uvicorn's startup banner prints the bare host:port, so
    # land the operator on the webapp instead of a 404.
    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/ui/", status_code=307)
