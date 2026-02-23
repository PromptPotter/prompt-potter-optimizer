"""
Standard API error response model.
"""
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Consistent error body returned by all API endpoints."""

    detail: str = Field(..., description="Human-readable error message")
    code: str | None = Field(None, description="Machine-readable error code")
