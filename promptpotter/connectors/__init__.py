"""Connector registry — single lookup point for backend-specific hooks.

Connectors are declared as data in :data:`CONNECTORS`. To add one, write
``connectors/<name>.py`` exporting a ``Connector(...)`` binding, then add
an import + dict entry below. No import-side-effects.
"""

from __future__ import annotations

from promptpotter.connectors.promptpotter import CONNECTOR as _PROMPTPOTTER
from promptpotter.connectors.protocol import BackendUnreachableError, Connector
from promptpotter.connectors.termnorm import CONNECTOR as _TERMNORM

__all__ = ["CONNECTORS", "BackendUnreachableError", "Connector", "get"]

CONNECTORS: dict[str, Connector] = {
    "termnorm": _TERMNORM,
    "promptpotter": _PROMPTPOTTER,
}


def get(name: str) -> Connector:
    """Look up a connector by name. Raises ``KeyError`` when unknown."""
    if name not in CONNECTORS:
        known = ", ".join(sorted(CONNECTORS)) or "(none)"
        raise KeyError(f"connector {name!r} not registered. Known: {known}")
    return CONNECTORS[name]
