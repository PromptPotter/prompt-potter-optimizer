"""Connector protocols — the "talk to a backend over the wire" boundary.

- **WireAdapter** — outbound payload shaping; ``(query, pipeline_params)`` → HTTP body.
  TermNorm default lives in ``promptpotter.connectors.termnorm``.
- **SessionProtocol** — session-lifecycle handshake for stateful backends; no-op
  for backends without a session concept.

``PipelineSchema`` owns "pipeline shape"; connectors only **transmit** it. Keep
these protocols small — anything beyond wire+session belongs in the schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

__all__ = ["SessionProtocol", "WireAdapter"]


class WireAdapter(Protocol):
    """Pure ``(query, pipeline_params) → request_body`` for ``BackendClient.run_query``.

    TermNorm default projects to ``{"query", "steps", "node_config"}``.
    """

    def __call__(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class SessionProtocol(Protocol):
    """Session lifecycle for stateful backends — keeps ``BackendClient`` session-agnostic.

    Implementations own idempotency + recovery. Backends without sessions pass
    a no-op instance.
    """

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
    ) -> dict[str, Any]: ...

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool: ...
