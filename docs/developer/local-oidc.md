# Local OIDC harness

The reference recipe for running PromptPotter with `PROMPTPOTTER_AUTH=on`
on a laptop — no Google credentials, no tunnel deploy.

The harness lives at [`dev/oidc-local/`](../../dev/oidc-local/) and uses
Dex as a local OIDC provider impersonating the Google slot.

## Quick start

```bash
cd dev/oidc-local
docker compose up -d
mkdir -p ../../.promptpotter/identity
cp oidc.json allowlist.json ../../.promptpotter/identity/
cd ../..
PROMPTPOTTER_AUTH=on python -m uvicorn promptpotter.main:app --port 8001
```

Visit `http://localhost:8001/login/`, click Google, log in as
`dev@promptpotter.local` / `password`.

Full walkthrough + troubleshooting + alternate IdP recipes:
[`dev/oidc-local/README.md`](../../dev/oidc-local/README.md).

## When to reach for this

Any bug that only reproduces under an authenticated session. Examples:

- A React component that crashes only post-login (the post-login render
  loop that motivated the harness).
- A renderer that mishandles a missing `email` claim.
- A multi-tenant store path that scopes incorrectly when
  `IdentityContext.tenant_id` is real.
- A session-cookie edge case (expiry, SameSite, secure-flag mismatch).
- An OIDC claim shape variation (Dex emits a slightly different
  `email_verified` arm than Google in some configs).

If the bug only shows on `app.promptpotter.com`, mirror it here first —
that's where the debugging is fast.

## Why this exists

Pre-flight gate Q6 (root `CLAUDE.md`) extended: debug-state belongs on
disk in human-readable form, the same way runtime state does. An
auth-on environment that only exists on the maintainer's tunnel deploy
violates Q6 for any collaborator who doesn't have remote-desktop access
to that box. This harness ships the environment as code.

## How the discovery override works

`infrastructure/identity/provider_config.py::OIDCProviderConfig`
accepts four optional fields (`issuer` / `authorize_url` / `token_url`
/ `jwks_url`) on the Google slot. Unset → production Google URLs.
Set → any OIDC-conformant IdP (Dex here; Auth0, Keycloak, Okta in
production-style adopter deployments).

`infrastructure/identity/google.py::GoogleProviderClient` reads the
override-or-default at construction time. The verifier
(`identity/verifier.py`) is already discovery-agnostic — it always took
`expected_issuer` + `jwks_uri` as parameters, never module constants.

The discovery override is OIDC-only; GitHub is OAuth-2.0 and ignores
the four fields if set.

## Related docs

- [`dev/oidc-local/README.md`](../../dev/oidc-local/README.md) — harness recipe + IdP swap table.
- [`docs/adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — permanent identity contract; the harness ships against Stage 1.
- [`deploy-linux/README.md`](../../deploy-linux/README.md) — the production Cloudflare Tunnel + systemd deploy that this harness reproduces locally.
