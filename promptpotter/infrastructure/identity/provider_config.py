"""The PROVIDER checks ``redirect_uri`` against its app page; we verify only the inbound ``state``."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OIDCProviderConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    issuer: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    jwks_url: str | None = None


@dataclass(frozen=True)
class ProviderConfigBundle:
    google: OIDCProviderConfig | None
    github: OIDCProviderConfig | None

    @property
    def configured(self) -> tuple[str, ...]:
        return tuple(name for name in SUPPORTED_PROVIDERS if getattr(self, name) is not None)


SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(f.name for f in fields(ProviderConfigBundle))


class OIDCConfigError(ValueError):
    pass


def load_provider_config(path: Path) -> ProviderConfigBundle:
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
    unknown = set(data.keys()) - set(SUPPORTED_PROVIDERS)
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
        client_id=str(raw["client_id"]),
        client_secret=str(raw["client_secret"]),
        redirect_uri=str(raw["redirect_uri"]),
        issuer=str(raw["issuer"]) if raw.get("issuer") else None,
        authorize_url=str(raw["authorize_url"]) if raw.get("authorize_url") else None,
        token_url=str(raw["token_url"]) if raw.get("token_url") else None,
        jwks_url=str(raw["jwks_url"]) if raw.get("jwks_url") else None,
    )


__all__ = [
    "SUPPORTED_PROVIDERS",
    "OIDCProviderConfig",
    "ProviderConfigBundle",
    "load_provider_config",
]
