"""ConnectorProtocol — wire payload + session lifecycle pluggability.

These tests pin the M12 connector boundary: ``BackendClient`` must
accept a custom ``WireAdapter`` (outbound payload shape) and a custom
``SessionProtocol`` (session lifecycle) so a second backend can land
without touching ``BackendClient`` or any of its callers. The default
behaviour preserves TermNorm shape — these tests prove the *seam*
exists, not that we ship a second connector today.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from promptpotter.infrastructure.backend import (
    BackendClient,
    TermNormSession,
    termnorm_wire_adapter,
)


def test_termnorm_wire_adapter_default_shape() -> None:
    """Default adapter projects pipeline_params to {query, steps, node_config}."""
    payload = termnorm_wire_adapter(
        "what is X?",
        {
            "steps": ["fuzzy_matching", "entity_profiling"],
            "entity_profiling": {"prompt": "rank by relevance"},
            "fuzzy_matching": {"max_results": 10},
        },
    )
    assert payload["query"] == "what is X?"
    assert payload["steps"] == ["fuzzy_matching", "entity_profiling"]
    assert payload["node_config"] == {
        "entity_profiling": {"prompt": "rank by relevance"},
        "fuzzy_matching": {"max_results": 10},
    }


def test_termnorm_wire_adapter_drops_non_dict_pipeline_params() -> None:
    """Non-dict per-node values are dropped — backend contract is per-node config dict."""
    payload = termnorm_wire_adapter(
        "q",
        {"steps": ["x"], "x": {"k": "v"}, "garbage": "string-not-dict"},
    )
    assert "node_config" in payload
    assert payload["node_config"] == {"x": {"k": "v"}}


def test_termnorm_wire_adapter_empty_params() -> None:
    """Missing pipeline_params yields a query-only payload."""
    payload = termnorm_wire_adapter("q", None)
    assert payload == {"query": "q"}


def test_backend_client_uses_custom_wire_adapter() -> None:
    """BackendClient accepts a custom WireAdapter and uses it on run_query.

    The seam M12 needs: a second connector swaps in its own payload
    shape without touching BackendClient or callers.
    """
    captured: dict[str, Any] = {}

    def alt_wire(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
        captured["query"] = query
        captured["pp"] = pipeline_params
        return {"prompt": query, "options": pipeline_params or {}}

    client = BackendClient("http://example.invalid", wire_adapter=alt_wire)
    # Call the adapter directly (no HTTP) — the contract under test is
    # which adapter the client holds, not the network round-trip.
    payload = client._wire_adapter("hello", {"k": "v"})
    assert payload == {"prompt": "hello", "options": {"k": "v"}}
    assert captured == {"query": "hello", "pp": {"k": "v"}}


def test_backend_client_default_session_is_termnorm() -> None:
    """Default session is TermNormSession (preserves current behaviour)."""
    client = BackendClient("http://example.invalid")
    assert isinstance(client._guard, TermNormSession)
    assert client._guard.has_terms is False


def test_backend_client_accepts_custom_session() -> None:
    """A custom SessionProtocol implementation drives ``init_session`` / recovery."""
    calls: list[tuple[str, Any]] = []

    class FakeSession:
        @property
        def has_terms(self) -> bool:
            return True

        async def set_terms(
            self,
            http: httpx.AsyncClient,
            base_url: str,
            terms: list[str],
        ) -> dict[str, Any]:
            calls.append(("set_terms", terms))
            return {"status": "ok"}

        async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
            calls.append(("recover", None))
            return True

    client = BackendClient("http://example.invalid", session=FakeSession())
    assert client._guard.has_terms is True
    # Sanity: the custom session is a SessionProtocol-shaped object the
    # client holds; full network coverage lives in the runtime smoke.
    assert hasattr(client._guard, "set_terms")
    assert hasattr(client._guard, "recover")


@pytest.mark.asyncio
async def test_termnorm_session_idempotent_set_terms() -> None:
    """Identical terms shouldn't re-POST /sessions — idempotency contract."""

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"status": "ok"}

    posted: list[Any] = []

    class _StubHttp:
        async def post(self, *args: Any, **kwargs: Any) -> _StubResponse:
            posted.append((args, kwargs))
            return _StubResponse()

    sess = TermNormSession()
    await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert len(posted) == 1
    # Second call with identical terms must short-circuit.
    result = await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert result["status"] == "already_initialized"
    # Nothing additional posted.
    assert len(posted) == 1
