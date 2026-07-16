"""OIDC identity foundation — Stage-1 wiring per `docs/adr/0002-identity-foundation.md`.

Public surface: provider config + allowlist + `OIDCSessionStore` (browser-login
cookies — NOT `infrastructure.store.SessionStore`, which persists a campaign run's
session artifacts) + OAuth flows + default-tenant claim migration. The middleware seam at
`promptpotter/presentation/api/middleware/oidc.py` is the sole consumer
upstream; nothing else imports from this package.

Operator config lives under the repo-local data dir
(`.promptpotter/identity/`): `oidc.json` (provider client_id/secret/
redirect), `allowlist.json` (permitted emails), `sessions/` (per-session
JSON files). All git-ignored by inclusion in `.promptpotter/`.
"""
