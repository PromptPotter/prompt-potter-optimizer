"""The connector contract's pure half, read without loading the registry. ``PipelineSchema`` owns
pipeline SHAPE and a connector only TRANSMITS it, so no pipeline fact lives here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

from promptpotter.shared.errors import PotterError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    import httpx

__all__ = [
    "BackendUnreachableError",
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
    """The configured backend isn't responding (503). Carries backend type + URL on ``details`` so the ``PotterError`` seam
    composes the envelope without re-parsing the message."""

    http_status = 503
    code = "backend_unreachable"

    def __init__(self, backend_type: str, backend_url: str, detail: str = "") -> None:
        self.backend_type = backend_type
        self.backend_url = backend_url
        self.detail = detail
        super().__init__(
            f"Backend '{backend_type}' at {backend_url} is not reachable. "
            f"Start the backend and try again." + (f" ({detail})" if detail else ""),
            details={"backend_type": backend_type, "backend_url": backend_url},
        )


class WireAdapter(Protocol):
    """Pure ``(query, pipeline_params) → request_body`` for ``BackendClient.run_query``."""

    def __call__(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class SessionProtocol(Protocol):
    """Session lifecycle for stateful backends, keeping ``BackendClient`` session-agnostic. Implementations own idempotency
    and recovery; a backend without sessions passes a no-op."""

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
    ) -> dict[str, Any]: ...

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool: ...
