"""Identity newtypes — Stage-0 reification of the identity-foundation seam.

Three :pep:`484` :class:`~typing.NewType`s the rest of the codebase uses in
preference to bare ``str``: :data:`TenantId`, :data:`UserId`, :data:`Issuer`.
Plus :func:`safe_name` for path-segment validation.

The composite :class:`promptpotter.shared.identity.IdentityContext` carries
these — no code outside the identity layer constructs a raw ``TenantId``
or ``UserId``.
"""

from __future__ import annotations

import re
from typing import NewType

TenantId = NewType("TenantId", str)
UserId = NewType("UserId", str)
Issuer = NewType("Issuer", str)
SafeName = NewType("SafeName", str)

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_MAX_SAFE_NAME_LEN = 64


def safe_name(raw: str) -> SafeName:
    """Validate *raw* as a slug-style path segment; return the :class:`SafeName`.

    Allowed: ``[a-zA-Z0-9_-]+``, length ``<= 64``. Stricter than the existing
    ``validate_path_component`` (no dots) — tenant slugs and user ids are
    URL-safe lowercase-or-mixed identifiers, not free-form path tokens.
    """
    if not raw or not _SAFE_NAME_RE.match(raw) or len(raw) > _MAX_SAFE_NAME_LEN:
        raise ValueError(
            f"Invalid identity slug: {raw!r}. "
            f"Must match [a-zA-Z0-9_-]+ with length <= {_MAX_SAFE_NAME_LEN}."
        )
    return SafeName(raw)


__all__ = ["Issuer", "SafeName", "TenantId", "UserId", "safe_name"]
