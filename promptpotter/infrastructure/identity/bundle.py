"""`IdentityBundle` — singleton assembled at FastAPI startup.

Holds provider config, session store, JWKS cache, the per-provider
clients, and the short-lived OAuth-state map (state token → pending
auth) used to bind the authorize→callback round-trip.

Stashed on `app.state.identity_bundle`; the middleware + auth router
both read from there.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from promptpotter.infrastructure.identity.github import GitHubProviderClient
from promptpotter.infrastructure.identity.google import GoogleProviderClient
from promptpotter.infrastructure.identity.jwks import JWKSCache
from promptpotter.infrastructure.identity.paths import IdentityPaths
from promptpotter.infrastructure.identity.provider_config import (
    ProviderConfigBundle,
    load_provider_config,
)
from promptpotter.infrastructure.identity.session import OIDCSessionStore

logger = logging.getLogger(__name__)

OAUTH_STATE_TTL_S = 600  # 10 min — covers slow consent screens but bounds replay
_MAX_PENDING_STATES = 1024


@dataclass(frozen=True)
class PendingAuth:
    """Short-lived state binding the authorize → callback round-trip."""

    provider: str
    nonce: str
    created_at: float


@dataclass
class IdentityBundle:
    paths: IdentityPaths
    config: ProviderConfigBundle
    session_store: OIDCSessionStore
    jwks_cache: JWKSCache
    google: GoogleProviderClient | None
    github: GitHubProviderClient | None
    pending_states: dict[str, PendingAuth] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def register_state(self, state: str, provider: str, nonce: str) -> None:
        now = time.monotonic()
        with self.lock:
            self._sweep_expired(now)
            if len(self.pending_states) >= _MAX_PENDING_STATES:
                self._evict_oldest()
            self.pending_states[state] = PendingAuth(provider=provider, nonce=nonce, created_at=now)

    def consume_state(self, state: str) -> PendingAuth | None:
        now = time.monotonic()
        with self.lock:
            self._sweep_expired(now)
            return self.pending_states.pop(state, None)

    def _sweep_expired(self, now: float) -> None:
        expired = [
            s for s, p in self.pending_states.items() if (now - p.created_at) > OAUTH_STATE_TTL_S
        ]
        for s in expired:
            self.pending_states.pop(s, None)

    def _evict_oldest(self) -> None:
        if not self.pending_states:
            return
        oldest = min(self.pending_states.items(), key=lambda kv: kv[1].created_at)[0]
        self.pending_states.pop(oldest, None)


def build_identity_bundle(paths: IdentityPaths) -> IdentityBundle:
    """Assemble the bundle from on-disk config; missing providers stay None."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.sessions_dir.mkdir(parents=True, exist_ok=True)
    config = load_provider_config(paths.provider_config)
    jwks_cache = JWKSCache()
    google = GoogleProviderClient(config.google, jwks_cache) if config.google is not None else None
    github = GitHubProviderClient(config.github) if config.github is not None else None
    logger.info("identity bundle ready; providers configured: %s", config.configured or "(none)")
    return IdentityBundle(
        paths=paths,
        config=config,
        session_store=OIDCSessionStore(paths.sessions_dir),
        jwks_cache=jwks_cache,
        google=google,
        github=github,
    )


__all__ = ["OAUTH_STATE_TTL_S", "IdentityBundle", "PendingAuth", "build_identity_bundle"]
