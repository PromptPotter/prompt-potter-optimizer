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
    """Exists only while a provider holds a sender's sends, so its presence is the whole signal."""

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


class ServedBackpressure(BackpressureReading):
    """A provider hold as served: the banked reading with both clocks read at the response."""

    held_for_s: float | None = Field(description="Seconds the provider has been throttling.")
    resumes_in_s: float | None = Field(
        description="Seconds until the cooldown ends; 0 once it has, where one send probes."
    )

    @classmethod
    def at(cls, held: BackpressureReading, now: float) -> "ServedBackpressure":
        return cls(
            **{name: getattr(held, name) for name in BackpressureReading.model_fields},
            held_for_s=None if held.since is None else max(0.0, now - held.since),
            resumes_in_s=None if held.resumes_at is None else max(0.0, held.resumes_at - now),
        )


__all__ = ["BackendConnection", "BackpressureReading", "ServedBackpressure"]
