"""Pydantic model for the connected-backend record (one per registered backend)."""

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso


class BackendConnection(StrictModel):
    """A registered backend connection (e.g. a local backend)."""

    id: str = Field(..., description="Unique backend ID, e.g. 'local'")
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'default'")
    base_url: str = Field(..., description="Backend API base URL")
    created_at: str = Field(default_factory=utcnow_iso)


__all__ = ["BackendConnection"]
