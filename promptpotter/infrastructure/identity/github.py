"""GitHub is OAuth 2.0, not OIDC: no ID token to verify, and a synthetic issuer keeps `(iss, sub)` provider-distinct."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from promptpotter.infrastructure.identity.google import ProviderIdentity
from promptpotter.infrastructure.identity.provider_config import (
    CodeExchangeError,
    OIDCProviderConfig,
)
from promptpotter.shared.tls import tls_context

GITHUB_ISSUER = "https://github.com"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAIL_URL = "https://api.github.com/user/emails"


class GitHubProviderClient:
    def __init__(self, config: OIDCProviderConfig) -> None:
        self._config = config

    def authorize_url(self, *, state: str) -> str:
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": "read:user user:email",
            "state": state,
            "allow_signup": "true",
        }
        return f"{GITHUB_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, *, code: str) -> ProviderIdentity:
        async with httpx.AsyncClient(timeout=15.0, verify=tls_context()) as client:
            token_response = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self._config.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            # GitHub refuses with a 200 whose body names the `error`, not with a 4xx.
            access_token = (
                token_response.json().get("access_token")
                if token_response.status_code == 200
                else None
            )
            if not isinstance(access_token, str) or not access_token:
                raise CodeExchangeError.refused(
                    "github", token_response.status_code, token_response.content
                )

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get(GITHUB_USER_URL, headers=auth_headers)
            if user_response.status_code != 200:
                raise CodeExchangeError("github", f"user_http_{user_response.status_code}")
            user_id = user_response.json().get("id")
            if not isinstance(user_id, int):
                raise CodeExchangeError("github", "user_without_id")

            # Only the verified list: `/user`'s `email` is unchecked, and the blocklist and host-admin claim match on it.
            email = None
            email_response = await client.get(GITHUB_EMAIL_URL, headers=auth_headers)
            if email_response.status_code == 200:
                email = _select_primary_verified_email(email_response.json())

        return ProviderIdentity(
            issuer=GITHUB_ISSUER,
            subject=str(user_id),
            email=email,
            provider="github",
        )


def _select_primary_verified_email(payload: object) -> str | None:
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if (
            isinstance(entry, dict)
            and entry.get("primary") is True
            and entry.get("verified") is True
            and isinstance(entry.get("email"), str)
        ):
            return str(entry["email"])
    for entry in payload:
        if (
            isinstance(entry, dict)
            and entry.get("verified") is True
            and isinstance(entry.get("email"), str)
        ):
            return str(entry["email"])
    return None
