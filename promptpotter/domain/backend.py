"""Pydantic model for the connected-backend record (one per registered backend)."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class BackendConnection(BaseModel):
    """A registered backend connection (e.g. a local backend)."""

    id: str = Field(..., description="Unique backend ID, e.g. 'local'")
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'default'")
    base_url: str = Field(..., description="Backend API base URL")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_synced_at: str | None = None
