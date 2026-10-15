from __future__ import annotations

from dataclasses import dataclass

from promptpotter.infrastructure.identity.github import GitHubProviderClient
from promptpotter.infrastructure.identity.google import GoogleProviderClient
from promptpotter.infrastructure.identity.jwks import JWKSCache
from promptpotter.infrastructure.identity.paths import IdentityPaths
from promptpotter.infrastructure.identity.provider_config import (
    ProviderConfigBundle,
    load_provider_config,
)
from promptpotter.infrastructure.identity.session import OIDCSessionStore


@dataclass
class IdentityBundle:
    paths: IdentityPaths
    config: ProviderConfigBundle
    session_store: OIDCSessionStore
    jwks_cache: JWKSCache
    google: GoogleProviderClient | None
    github: GitHubProviderClient | None


def build_identity_bundle(paths: IdentityPaths) -> IdentityBundle:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.sessions_dir.mkdir(parents=True, exist_ok=True)
    config = load_provider_config(paths.provider_config)
    jwks_cache = JWKSCache()
    google = GoogleProviderClient(config.google, jwks_cache) if config.google is not None else None
    github = GitHubProviderClient(config.github) if config.github is not None else None
    return IdentityBundle(
        paths=paths,
        config=config,
        session_store=OIDCSessionStore(paths.sessions_dir),
        jwks_cache=jwks_cache,
        google=google,
        github=github,
    )


__all__ = ["IdentityBundle", "build_identity_bundle"]
