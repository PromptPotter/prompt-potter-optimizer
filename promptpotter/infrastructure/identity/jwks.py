"""JWKS cache keyed by URL, refreshed on an unknown ``kid`` — which covers Google rotating keys with no fixed schedule."""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPublicKey,
    RSAPublicNumbers,
)

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL_S = 60 * 60  # 1 h


def _b64url_decode_int(value: str) -> int:
    padding_chars = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding_chars)
    return int.from_bytes(raw, "big")


def _jwk_to_rsa_public_key(jwk: dict[str, Any]) -> RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise ValueError(f"unsupported JWK kty: {jwk.get('kty')!r} (only RSA supported)")
    n = _b64url_decode_int(str(jwk["n"]))
    e = _b64url_decode_int(str(jwk["e"]))
    return RSAPublicNumbers(e=e, n=n).public_key()


@dataclass
class _JWKSEntry:
    keys: dict[str, RSAPublicKey]
    fetched_at: float


class JWKSCache:
    """Process-wide JWKS cache. Thread-safe; refresh on unknown kid + TTL expiry."""

    def __init__(self, ttl_s: float = _DEFAULT_CACHE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._entries: dict[str, _JWKSEntry] = {}
        self._lock = threading.Lock()

    async def get_key(self, jwks_uri: str, kid: str) -> RSAPublicKey:
        cached = self._lookup(jwks_uri, kid)
        if cached is not None:
            return cached
        await self._refresh(jwks_uri)
        cached = self._lookup(jwks_uri, kid)
        if cached is None:
            raise KeyError(f"kid {kid!r} not found in JWKS at {jwks_uri}")
        return cached

    def _lookup(self, jwks_uri: str, kid: str) -> RSAPublicKey | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(jwks_uri)
            if entry is None or (now - entry.fetched_at) > self._ttl_s:
                return None
            return entry.keys.get(kid)

    async def _refresh(self, jwks_uri: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(jwks_uri)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise ValueError(f"JWKS at {jwks_uri} is not a JSON object")
        raw_keys = body.get("keys")
        if not isinstance(raw_keys, list):
            raise ValueError(f"JWKS at {jwks_uri} missing 'keys' array")
        parsed: dict[str, RSAPublicKey] = {}
        for jwk in raw_keys:
            if not isinstance(jwk, dict) or "kid" not in jwk:
                continue
            try:
                parsed[str(jwk["kid"])] = _jwk_to_rsa_public_key(jwk)
            except (ValueError, KeyError) as exc:
                logger.warning("Skipping malformed JWK at %s: %s", jwks_uri, exc)
        with self._lock:
            self._entries[jwks_uri] = _JWKSEntry(keys=parsed, fetched_at=time.monotonic())


__all__ = ["JWKSCache"]
