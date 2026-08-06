"""Identity newtypes, used in preference to bare ``str``. No code outside the identity layer constructs a raw ``TenantId`` or ``UserId``."""

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
    """Validate *raw* as a slug-style path segment. Stricter than ``validate_path_component`` (no dots): tenant slugs and
    user ids are URL-safe identifiers, not free-form path tokens."""
    if not raw or not _SAFE_NAME_RE.match(raw) or len(raw) > _MAX_SAFE_NAME_LEN:
        raise ValueError(
            f"Invalid identity slug: {raw!r}. "
            f"Must match [a-zA-Z0-9_-]+ with length <= {_MAX_SAFE_NAME_LEN}."
        )
    return SafeName(raw)


__all__ = ["Issuer", "SafeName", "TenantId", "UserId", "safe_name"]
