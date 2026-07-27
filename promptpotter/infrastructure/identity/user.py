"""`UserId` derivation from OIDC issuer + subject.

Stable across sign-ins of the same provider account; collision-resistant
across providers (different `iss`). 16 hex chars from SHA-256 — fits
`safe_name` (length ≤64, charset `[a-zA-Z0-9_-]+`).
"""

from __future__ import annotations

import hashlib

from promptpotter.domain.identity import UserId, safe_name


def derive_user_id(issuer: str, subject: str) -> UserId:
    digest = hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:16]
    return UserId(safe_name(digest))


__all__ = ["derive_user_id"]
