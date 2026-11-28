"""What a ledger heard of the provider and the backend: a retried fault, a 429 hold, the round's calls in flight."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso


class BackendWarningRecord(CycleRecord):
    """An attempt that failed and will be retried: a transport fault or a 5xx, never a 429."""

    record_type: Literal["backend_warning"] = "backend_warning"
    kind: str
    attempt: int
    max_attempts: int
    wait_s: float
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: Annotated[str, AfterValidator(lambda q: q[:80])] = ""
    detail: Annotated[str, AfterValidator(lambda d: d[:400])] = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderHoldRecord(CycleRecord):
    """A 429 episode's open or strike, heard by a ledger from its own sends."""

    record_type: Literal["provider_hold"] = "provider_hold"
    sender: str
    scope: str
    strikes: int
    # Epoch: a throttle past it stops the run instead of holding it.
    gives_up_at: float
    reading: BackpressureReading
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderEasedRecord(CycleRecord):
    """The provider answers the sender again: told at once to every ledger that heard the hold."""

    record_type: Literal["provider_eased"] = "provider_eased"
    sender: str
    held_s: float
    timestamp: str = Field(default_factory=utcnow_iso)


class FlightWaiting(FrozenModel):
    individual_id: str
    position: int
    since: float


class FlightRecord(CycleRecord):
    """The whole round's calls in flight, so it names no candidate."""

    record_type: Literal["flight"] = "flight"
    round: int
    out: int
    allowed: int
    most: int
    affordable: int | None = None
    cell_usd: float | None = None
    waiting: FlightWaiting | None = None
    backpressure: BackpressureReading | None = None
    timestamp: str = Field(default_factory=utcnow_iso)
