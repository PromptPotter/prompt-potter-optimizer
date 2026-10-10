from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

from promptpotter.shared.errors import PotterError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    import httpx

    from promptpotter.domain.sample import Sample

__all__ = [
    "BackendUnreachableError",
    "CellEnvelopeSeconds",
    "ConnectorExecution",
    "MeasuredUnit",
    "SessionProtocol",
    "WireAdapter",
    "unit_count",
    "unit_plural",
]

ConnectorExecution = Literal["remote_http", "in_process"]

MeasuredUnit = Literal["sample", "cell"]


@shapes_optimizer_prompt
def unit_plural(unit: MeasuredUnit) -> str:
    return f"{unit}s"


@shapes_optimizer_prompt
def unit_count(n: int, unit: MeasuredUnit) -> str:
    return f"{n} {unit if n == 1 else unit_plural(unit)}"


class BackendUnreachableError(PotterError):
    http_status = 503
    code = "backend_unreachable"

    def __init__(self, backend_type: str, backend_url: str, detail: str = "") -> None:
        self.backend_type = backend_type
        self.backend_url = backend_url
        self.detail = detail
        # A connector that DIAGNOSED the fault leads with it; the URL names a port nothing serves for an in-process one.
        super().__init__(
            f"Backend '{backend_type}' is not ready: {detail}"
            if detail
            else f"Backend '{backend_type}' at {backend_url} is not reachable. "
            f"Start the backend and try again.",
            details={"backend_type": backend_type, "backend_url": backend_url},
        )


class WireAdapter(Protocol):
    def __call__(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class CellEnvelopeSeconds(Protocol):
    """The NUMBER only; the bound it puts in force is ``scoring/cell_envelope.py::CellEnvelope``."""

    def __call__(
        self,
        sample: Sample,
        pipeline_params: dict[str, Any] | None,
    ) -> float: ...


class SessionProtocol(Protocol):
    """Also how this backend's error replies READ, so ``BackendClient`` parses no backend's envelope."""

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
    ) -> dict[str, Any]: ...

    async def recover(
        self, http: httpx.AsyncClient, base_url: str, reply: httpx.Response
    ) -> bool: ...

    def resend_refused(self, reply: httpx.Response) -> str | None: ...
