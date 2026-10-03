from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso


class BackendConnection(StrictModel):
    id: str = Field(..., description="Unique backend ID, e.g. 'local'")
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'default'")
    base_url: str = Field(..., description="Backend API base URL")
    created_at: str = Field(default_factory=utcnow_iso)


class BackpressureReading(StrictModel):
    """A provider holding a sender's sends (`infrastructure/llm/rate_limit.py::Backpressure`).
    Exists only while something is held, so its presence is the whole signal."""

    model_config = ConfigDict(frozen=True)

    sender: str
    at_once: int | None = Field(description="Sends that may be out at once; None: uncapped.")
    since: float | None = Field(
        description="Epoch seconds the provider began throttling; None once it answers again "
        "and only the cap, climbing back, still holds sends."
    )
    resumes_at: float | None = Field(
        description="Epoch seconds the episode's cooldown ends; past it, one send probes."
    )
    detail: str = Field(description="The provider's own words, from the throttle that opened it.")


__all__ = ["BackendConnection", "BackpressureReading"]
