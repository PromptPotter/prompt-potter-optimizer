"""Google OIDC provider client — Authorization Code flow + ID Token verify."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from promptpotter.infrastructure.identity.jwks import JWKSCache
from promptpotter.infrastructure.identity.provider_config import OIDCProviderConfig
from promptpotter.infrastructure.identity.verifier import (
    IDTokenInvalidError,
    VerifiedIDToken,
    verify_id_token,
)

logger = logging.getLogger(__name__)

GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


class GoogleTokenExchangeError(RuntimeError):
    """Raised when the token endpoint refuses the auth code."""


@dataclass(frozen=True)
class ProviderIdentity:
    """Verified identity from a provider — common shape across Google + GitHub."""

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    provider: str


class GoogleProviderClient:
    """Google OIDC client — Authorization Code → ID Token verification."""

    def __init__(self, config: OIDCProviderConfig, jwks_cache: JWKSCache) -> None:
        self._config = config
        self._jwks = jwks_cache

    def authorize_url(self, *, state: str, nonce: str) -> str:
        """Build the user-facing redirect URL to Google's consent screen."""
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, expected_nonce: str) -> ProviderIdentity:
        """Exchange the auth code for an ID token, verify, return identity."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self._config.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise GoogleTokenExchangeError(
                f"Google token endpoint returned {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        id_token_raw = body.get("id_token")
        if not isinstance(id_token_raw, str):
            raise GoogleTokenExchangeError("Google token response missing id_token")

        verified: VerifiedIDToken = await verify_id_token(
            id_token_raw,
            expected_issuer=GOOGLE_ISSUER,
            expected_audience=self._config.client_id,
            jwks_uri=GOOGLE_JWKS_URL,
            jwks_cache=self._jwks,
        )

        nonce_claim = verified.claims.get("nonce")
        if nonce_claim != expected_nonce:
            raise IDTokenInvalidError(
                f"nonce mismatch: got {nonce_claim!r}, expected {expected_nonce!r}"
            )

        return ProviderIdentity(
            issuer=verified.issuer,
            subject=verified.subject,
            email=verified.email,
            email_verified=verified.email_verified,
            provider="google",
        )


__all__ = ["GOOGLE_ISSUER", "GoogleProviderClient", "GoogleTokenExchangeError", "ProviderIdentity"]
