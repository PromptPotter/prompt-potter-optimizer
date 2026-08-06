"""The wire boundary: ``WireAdapter`` shapes the outbound payload, ``SessionProtocol`` the handshake. ``PipelineSchema`` owns pipeline
SHAPE and connectors only TRANSMIT it, so anything beyond wire+session belongs in the schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

__all__ = ["SessionProtocol", "WireAdapter"]


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
