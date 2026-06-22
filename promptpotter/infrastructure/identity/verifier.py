"""ID Token (JWS) verifier — RS256 only, the dialect Google emits.

GitHub does not issue ID tokens (OAuth 2.0, not OIDC); GitHub identity
extraction lives in `github.py` and never reaches this module.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from promptpotter.infrastructure.identity.jwks import JWKSCache


class IDTokenInvalidError(ValueError):
    """Raised when ID token verification fails for any reason."""


@dataclass(frozen=True)
class VerifiedIDToken:
    """Verified claims — only flows downstream until the middleware exits."""

    issuer: str
    subject: str
    email: str | None
    claims: dict[str, Any]


def _b64url_decode(value: str) -> bytes:
    padding_chars = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding_chars)


async def verify_id_token(
    id_token: str,
    *,
    expected_issuer: str,
    expected_audience: str,
    jwks_uri: str,
    jwks_cache: JWKSCache,
    leeway_s: int = 30,
) -> VerifiedIDToken:
    """Verify *id_token* signature + claims; return the verified envelope.

    Raises :class:`IDTokenInvalidError` on any failure — the caller treats it
    as a hard 401 at the trust boundary.
    """
    try:
        header_b64, payload_b64, signature_b64 = id_token.split(".")
    except ValueError as exc:
        raise IDTokenInvalidError(f"malformed JWT: expected 3 segments ({exc})") from exc

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise IDTokenInvalidError(f"malformed JWT body: {exc}") from exc

    if header.get("alg") != "RS256":
        raise IDTokenInvalidError(f"unsupported alg: {header.get('alg')!r} (require RS256)")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise IDTokenInvalidError("JWT header missing 'kid'")

    try:
        key = await jwks_cache.get_key(jwks_uri, kid)
    except KeyError as exc:
        raise IDTokenInvalidError(f"kid not in JWKS: {exc}") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode()
    try:
        key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise IDTokenInvalidError("signature verification failed") from exc

    iss = payload.get("iss")
    if iss != expected_issuer:
        raise IDTokenInvalidError(f"iss mismatch: got {iss!r}, expected {expected_issuer!r}")

    aud = payload.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if expected_audience not in audiences:
        raise IDTokenInvalidError(f"aud mismatch: {aud!r} does not include {expected_audience!r}")

    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int | float) or now > exp + leeway_s:
        raise IDTokenInvalidError("token expired")
    iat = payload.get("iat")
    if isinstance(iat, int | float) and iat > now + leeway_s:
        raise IDTokenInvalidError("iat in the future")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise IDTokenInvalidError("sub claim missing or empty")

    email_raw = payload.get("email")
    email = str(email_raw) if isinstance(email_raw, str) else None

    return VerifiedIDToken(
        issuer=iss,
        subject=sub,
        email=email,
        claims=payload,
    )


__all__ = ["IDTokenInvalidError", "VerifiedIDToken", "verify_id_token"]
