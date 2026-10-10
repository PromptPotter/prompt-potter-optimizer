from __future__ import annotations

from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import UserId, safe_name


def derive_user_id(issuer: str, subject: str) -> UserId:
    return UserId(safe_name(stable_hash([issuer, subject])))


__all__ = ["derive_user_id"]
