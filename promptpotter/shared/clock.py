"""The one wall-clock helper. Every UTC timestamp string in the codebase
mints here so the format never drifts.

``Z`` suffix (RFC 3339 canonical UTC), not ``+00:00`` — unambiguous and what
the JSON wire + observability surfaces expect. Importable from any layer:
``shared/`` is leaf-level, so domain models may use it without reaching into
infrastructure.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["iso_z", "utcnow_iso"]


def iso_z(dt: datetime) -> str:
    """*dt* as an RFC 3339 UTC string. The ONE place the ``Z`` spelling is applied.

    Not a second helper — the same rule, for an instant the caller already has (a file
    mtime, a parsed record). Split out because that case cannot go through
    :func:`utcnow_iso` and so was hand-formatting ``+00:00`` onto the wire instead.
    """
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utcnow_iso() -> str:
    """Current UTC instant as an RFC 3339 string, e.g. ``2026-06-04T12:00:00Z``."""
    return iso_z(datetime.now(UTC))
