"""Connector protocol — bundles the four backend-specific hooks under one shape.

A connector packages everything PromptPotter needs to talk to a specific
backend kind. Additional connectors are added by writing one new file in
this package.

The four hooks:

- ``wire_adapter`` — outbound HTTP body shape for ``BackendClient.run_query``.
- ``session_factory`` — fresh ``SessionProtocol`` instance per ``BackendClient``.
- ``extract_experiment`` — backend experiment-data → ``(queries, index_terms)``.
- ``resolve_ground_truth`` — single-query ground-truth lookup over experiment
  data (currently unused by core paths but reserved for trace-ingestion flows).

Adding a connector is intentionally local to ``connectors/<name>.py`` — no
edits to ``application/config.py`` or ``infrastructure/backend.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.domain.connector import SessionProtocol, WireAdapter

if TYPE_CHECKING:
    import httpx

# Bootstrap calls a connector's version_check once at session init with the
# BackendClient's live httpx client + base_url; the return is the backend's
# self-reported revision string, or None when the backend is silent.
VersionCheck = Callable[["httpx.AsyncClient", str], Awaitable[str | None]]


@dataclass(frozen=True)
class Connector:
    """Frozen bundle of the four backend-specific hooks."""

    name: str
    """Lowercase id matching ``pipeline.json::backend_type`` and
    ``pipeline_schema.name.lower()``."""

    wire_adapter: WireAdapter
    """Outbound payload shaper for ``BackendClient.run_query``."""

    session_factory: Callable[[], SessionProtocol]
    """Fresh session instance per ``BackendClient`` — sessions hold per-client state."""

    extract_experiment: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    """Backend experiment data → ``(queries, index_terms)``."""

    resolve_ground_truth: Callable[[dict[str, Any], str], str | None]
    """Resolve ground truth for a single query string from experiment data."""

    expected_revision: str | None = None
    """Backend revision (git SHA, semver, …) this PromptPotter rev expects.
    Paired with ``version_check`` — bootstrap WARNs on drift. ``None`` opts out."""

    version_check: VersionCheck | None = None
    """Async ``(http, base_url) -> str | None`` — reads the backend's
    self-reported revision. Mirrors :class:`SessionProtocol` shape so connectors
    stay layer-clean (no ``BackendClient`` import). ``None`` opts out."""

    def to_dict(self) -> dict[str, Any]:
        """Identity projection — ``name`` only; callables are not JSON-able."""
        return {"name": self.name}


__all__ = ["Connector", "SessionProtocol", "VersionCheck", "WireAdapter"]
