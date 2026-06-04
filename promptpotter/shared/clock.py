"""The one wall-clock helper. Every UTC timestamp string in the codebase
mints here so the format never drifts.

``Z`` suffix (RFC 3339 canonical UTC), not ``+00:00`` — unambiguous and what
the JSON wire + observability surfaces expect. Importable from any layer:
``shared/`` is leaf-level, so domain models may use it without reaching into
infrastructure.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["utcnow_iso"]


def utcnow_iso() -> str:
    """Current UTC instant as an RFC 3339 string, e.g. ``2026-06-04T12:00:00Z``."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
