"""OIDC identity foundation — Stage-1 Stage-1 wiring per `docs/adr/0002-identity-foundation.md`.

Public surface: provider config + allowlist + session store + OAuth flows
+ default-tenant claim migration. The middleware seam at
`promptpotter/presentation/api/middleware/oidc.py` is the sole consumer
upstream; nothing else imports from this package.

Operator config lives under the repo-local data dir
(`.promptpotter/identity/`): `oidc.json` (provider client_id/secret/
redirect), `allowlist.json` (permitted emails), `sessions/` (per-session
JSON files). All git-ignored by inclusion in `.promptpotter/`.
"""

from promptpotter.infrastructure.identity.allowlist import (
    AllowlistDecision,
    add_email,
    check_allowlist,
    list_emails,
    remove_email,
)
from promptpotter.infrastructure.identity.bundle import (
    IdentityBundle,
    build_identity_bundle,
)
from promptpotter.infrastructure.identity.github import GitHubProviderClient
from promptpotter.infrastructure.identity.google import GoogleProviderClient, ProviderIdentity
from promptpotter.infrastructure.identity.migration import (
    maybe_claim_default,
    registered_or_default_identity,
    registered_user_id,
)
from promptpotter.infrastructure.identity.paths import (
    IdentityPaths,
    default_identity_paths,
)
from promptpotter.infrastructure.identity.provider_config import (
    OIDCProviderConfig,
    ProviderConfigBundle,
    load_provider_config,
)
from promptpotter.infrastructure.identity.session import (
    OIDCSessionStore,
    SessionData,
)

__all__ = [
    "AllowlistDecision",
    "GitHubProviderClient",
    "GoogleProviderClient",
    "IdentityBundle",
    "IdentityPaths",
    "OIDCProviderConfig",
    "OIDCSessionStore",
    "ProviderConfigBundle",
    "ProviderIdentity",
    "SessionData",
    "add_email",
    "build_identity_bundle",
    "check_allowlist",
    "default_identity_paths",
    "list_emails",
    "load_provider_config",
    "maybe_claim_default",
    "registered_or_default_identity",
    "registered_user_id",
    "remove_email",
]
