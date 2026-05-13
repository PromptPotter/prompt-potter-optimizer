"""Connector protocols — the M12 boundary for "talk to a backend over the wire".

Two seams that capture what's currently TermNorm-shaped inside
``infrastructure/backend.py``:

1. **WireAdapter** — the outbound payload-shaping callable. Translates
   ``(query, pipeline_params)`` into the HTTP request body the backend
   expects. The default TermNorm shape (``{"query", "steps", "node_config"}``)
   lives in ``infrastructure/backend.termnorm_wire_adapter``; M12 can swap
   it for a different connector without touching ``BackendClient`` or any
   of its callers.

2. **SessionProtocol** — the session-lifecycle contract for stateful
   backends that need a ``POST /sessions`` handshake before queries are
   accepted. The default ``TermNormSession`` lives in
   ``infrastructure/backend``; backends with no session concept can pass
   a no-op implementation.

``PipelineSchema`` already owns "pipeline shape" (which nodes are active,
their typed configs); the connector's role is only to **transmit** that
shape over a specific wire format. Keep these protocols small —
anything beyond wire+session belongs in the schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

__all__ = ["SessionProtocol", "WireAdapter"]


class WireAdapter(Protocol):
    """Outbound payload-shaping for ``BackendClient.run_query``.

    Given a query + pipeline_params (the per-node override map carried
    end-to-end by the optimizer), return the dict that becomes the HTTP
    request body. Pure function — no I/O, no logging, no side effects.
    The default TermNorm implementation projects to
    ``{"query", "steps", "node_config"}``.
    """

    def __call__(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class SessionProtocol(Protocol):
    """Session lifecycle for stateful backends.

    Backends that require a handshake (e.g. TermNorm's
    ``POST /sessions`` with a ``terms`` array) implement this so
    ``BackendClient`` can stay session-agnostic. Implementations own the
    idempotency check and the recovery handshake; ``BackendClient`` only
    asks them to initialize or recover.

    For backends with no session concept, pass an instance that
    no-ops on ``set_terms`` / ``recover``.
    """

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
    ) -> dict[str, Any]: ...

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool: ...
