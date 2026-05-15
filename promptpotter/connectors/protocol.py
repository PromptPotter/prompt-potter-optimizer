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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from promptpotter.domain.connector import SessionProtocol, WireAdapter


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

    extract_experiment: Callable[[dict], tuple[list[dict], list[str]]]
    """Backend experiment data → ``(queries, index_terms)``."""

    resolve_ground_truth: Callable[[dict, str], str | None]
    """Resolve ground truth for a single query string from experiment data."""

    def to_dict(self) -> dict[str, Any]:
        """Identity projection — ``name`` only; callables are not JSON-able."""
        return {"name": self.name}


__all__ = ["Connector", "SessionProtocol", "WireAdapter"]
