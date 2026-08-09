"""GitHub is OAuth 2.0, NOT OIDC — no ID token, no JWS verification; identity comes from the profile API under TLS. The
synthetic issuer ``https://github.com`` keeps ``(iss, sub)`` hashing provider-distinct from Google."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx

from promptpotter.infrastructure.identity.google import ProviderIdentity
from promptpotter.infrastructure.identity.provider_config import OIDCProviderConfig

logger = logging.getLogger(__name__)

GITHUB_ISSUER = "https://github.com"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAIL_URL = "https://api.github.com/user/emails"


class GitHubTokenExchangeError(RuntimeError):
    pass


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
        async with httpx.AsyncClient(timeout=15.0) as client:
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
            if token_response.status_code != 200:
                raise GitHubTokenExchangeError(
                    f"GitHub token endpoint returned {token_response.status_code}: "
                    f"{token_response.text[:200]}"
                )
            token_body = token_response.json()
            access_token = token_body.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise GitHubTokenExchangeError(
                    f"GitHub token response missing access_token: {token_body!r}"
                )

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get(GITHUB_USER_URL, headers=auth_headers)
            if user_response.status_code != 200:
                raise GitHubTokenExchangeError(
                    f"GitHub user endpoint returned {user_response.status_code}"
                )
            user = user_response.json()
            user_id = user.get("id")
            if not isinstance(user_id, int):
                raise GitHubTokenExchangeError(
                    f"GitHub /user response missing numeric id: {user!r}"
                )

            email = user.get("email") if isinstance(user.get("email"), str) else None
            if email is None:
                email_response = await client.get(GITHUB_EMAIL_URL, headers=auth_headers)
                if email_response.status_code == 200:
                    primary = _select_primary_verified_email(email_response.json())
                    if primary is not None:
                        email = primary

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


__all__ = ["GitHubProviderClient", "GitHubTokenExchangeError"]
