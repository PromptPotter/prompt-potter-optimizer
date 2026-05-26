"""OIDC provider configuration — Google + GitHub.

Loaded from `.promptpotter/identity/oidc.json`. Schema:

```json
{
  "google": {
    "client_id": "...",
    "client_secret": "...",
    "redirect_uri": "https://your-tunnel.example.com/api/v1/auth/callback/google"
  },
  "github": {
    "client_id": "...",
    "client_secret": "...",
    "redirect_uri": "https://your-tunnel.example.com/api/v1/auth/callback/github"
  }
}
```

`redirect_uri` is provider-registered on the OAuth app page; the
middleware verifies the inbound `state` token but the provider verifies
the redirect URI matches what was registered. Either provider may be
omitted — the login page hides buttons for unconfigured providers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = frozenset({"google", "github"})


@dataclass(frozen=True)
class OIDCProviderConfig:
    """Per-provider OAuth/OIDC client config."""

    name: str
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class ProviderConfigBundle:
    """Loaded provider set — at most one of `google` / `github`."""

    google: OIDCProviderConfig | None
    github: OIDCProviderConfig | None

    @property
    def configured(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.google is not None:
            out.append("google")
        if self.github is not None:
            out.append("github")
        return tuple(out)

    def get(self, name: str) -> OIDCProviderConfig | None:
        if name == "google":
            return self.google
        if name == "github":
            return self.github
        return None


class OIDCConfigError(ValueError):
    """Raised when `oidc.json` is missing or malformed."""


def load_provider_config(path: Path) -> ProviderConfigBundle:
    """Parse `oidc.json`. Empty file or missing path → both providers None."""
    if not path.is_file():
        return ProviderConfigBundle(google=None, github=None)
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return ProviderConfigBundle(google=None, github=None)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OIDCConfigError(f"oidc.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OIDCConfigError("oidc.json top-level must be a JSON object.")
    google = _parse_provider("google", data.get("google"))
    github = _parse_provider("github", data.get("github"))
    unknown = set(data.keys()) - _SUPPORTED_PROVIDERS
    if unknown:
        logger.warning("oidc.json contains unsupported providers (ignored): %s", sorted(unknown))
    return ProviderConfigBundle(google=google, github=github)


def _parse_provider(name: str, raw: object) -> OIDCProviderConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise OIDCConfigError(f"oidc.json[{name!r}] must be an object or null.")
    required = ("client_id", "client_secret", "redirect_uri")
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise OIDCConfigError(f"oidc.json[{name!r}] missing required keys: {', '.join(missing)}")
    return OIDCProviderConfig(
        name=name,
        client_id=str(raw["client_id"]),
        client_secret=str(raw["client_secret"]),
        redirect_uri=str(raw["redirect_uri"]),
    )


__all__ = [
    "OIDCConfigError",
    "OIDCProviderConfig",
    "ProviderConfigBundle",
    "load_provider_config",
]
