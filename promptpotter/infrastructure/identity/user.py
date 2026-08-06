"""``UserId`` from OIDC issuer + subject — stable across sign-ins of one provider account, collision-resistant across
providers. 16 hex of SHA-256, which fits ``safe_name``."""

from __future__ import annotations

import hashlib

from promptpotter.domain.identity import UserId, safe_name


def derive_user_id(issuer: str, subject: str) -> UserId:
    digest = hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:16]
    return UserId(safe_name(digest))


__all__ = ["derive_user_id"]
