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
    pass


@dataclass(frozen=True)
class ProviderIdentity:
    """Verified identity from a provider — common shape across Google + GitHub."""

    issuer: str
    subject: str
    email: str | None
    provider: str


class GoogleProviderClient:
    """OIDC client — Authorization Code to ID Token verification. Every URL may be overridden in ``oidc.json``, so any
    OIDC-conformant IdP (Dex, Keycloak, Auth0, Okta) rides the same client."""

    def __init__(self, config: OIDCProviderConfig, jwks_cache: JWKSCache) -> None:
        self._config = config
        self._jwks = jwks_cache
        self._issuer = config.issuer or GOOGLE_ISSUER
        self._authorize_url = config.authorize_url or GOOGLE_AUTH_URL
        self._token_url = config.token_url or GOOGLE_TOKEN_URL
        self._jwks_url = config.jwks_url or GOOGLE_JWKS_URL

    def authorize_url(self, *, state: str, nonce: str) -> str:
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
        return f"{self._authorize_url}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, expected_nonce: str) -> ProviderIdentity:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self._token_url,
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
                f"token endpoint returned {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        id_token_raw = body.get("id_token")
        if not isinstance(id_token_raw, str):
            raise GoogleTokenExchangeError("token response missing id_token")

        verified: VerifiedIDToken = await verify_id_token(
            id_token_raw,
            expected_issuer=self._issuer,
            expected_audience=self._config.client_id,
            jwks_uri=self._jwks_url,
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
            provider="google",
        )


__all__ = ["GOOGLE_ISSUER", "GoogleProviderClient", "GoogleTokenExchangeError", "ProviderIdentity"]
