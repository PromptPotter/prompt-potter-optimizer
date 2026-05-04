"""Connector registry — single lookup point for backend-specific hooks.

Connectors self-register at import time. ``bootstrap`` imports each
connector module (``promptpotter.connectors.termnorm`` etc.) once on
boot; other modules read via :func:`get`.

Adding a connector for M12: write ``connectors/<name>.py``, define a
``Connector(...)``, call ``register(...)`` at module top, and add an
import in ``bootstrap`` so it loads on boot. No edits anywhere else.
"""

from __future__ import annotations

from promptpotter.connectors.protocol import Connector

__all__ = ["Connector", "get", "names", "register"]

_REGISTRY: dict[str, Connector] = {}


def register(connector: Connector) -> None:
    """Register a connector. Idempotent — same name overwrites silently."""
    _REGISTRY[connector.name] = connector


def get(name: str) -> Connector:
    """Look up a connector by name. Raises ``KeyError`` when unknown."""
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"connector {name!r} not registered. Known: {known}")
    return _REGISTRY[name]


def names() -> list[str]:
    """Sorted list of registered connector names — for diagnostics."""
    return sorted(_REGISTRY)
