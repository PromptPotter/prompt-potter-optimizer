# dev/oidc-local — local OIDC harness via Dex

Spin up a real OIDC provider on `http://localhost:5556` so PromptPotter
can run with `PROMPTPOTTER_AUTH=on` locally — no Google credentials, no
tunnel deploy. Reproduces every post-auth bug class on a laptop.

## Three-step recipe

```bash
# 1. start Dex
cd dev/oidc-local
docker compose up -d

# 2. install the harness OIDC + allowlist configs into the project
mkdir -p ../../.promptpotter/identity
cp oidc.json allowlist.json ../../.promptpotter/identity/

# 3. start PromptPotter with auth on
cd ../..
PROMPTPOTTER_AUTH=on python -m uvicorn promptpotter.main:app --port 8001
```

Open `http://localhost:8001/login/`, click the Google button (Dex is
configured as the Google slot — see § "How it works" below), enter:

- **email:** `dev@promptpotter.local`
- **password:** `password`

You land on `/` with a real authenticated session cookie. Subsequent
page loads, API calls, and any post-auth bug repros run exactly as they
would on `app.promptpotter.dev`.

## How it works

PromptPotter's `oidc.json` accepts four optional discovery overrides on
the Google slot (`issuer` / `authorize_url` / `token_url` / `jwks_url`).
Production leaves them unset → Google's production URLs apply. This
harness sets all four → the Google client rides Dex instead. The
verifier (`infrastructure/identity/verifier.py`) is already
discovery-agnostic; only `google.py` carried the hard-coded URLs, now
config-driven.

Dex is a CNCF-graduated OIDC provider. The `dex-config.yaml` ships:

- One static client (`promptpotter-dev` / `dev-secret`) with the
  callback URL pre-registered.
- One static password user (`dev@promptpotter.local`), bcrypt-hashed.
- An in-memory store — no persistence across `docker compose down`.

The allowlist file restricts logins to `dev@promptpotter.local`, so an
accidental copy of `oidc.json` to a public deployment without
overwriting `allowlist.json` still rejects every real user.

## When to use it

- **Any post-auth bug** — React #185 was the trigger; future
  authenticated render paths, multi-tenant boundary tests, OIDC claim
  edge cases, session-cookie behaviour all qualify.
- **Whitelabel adopter onboarding** — copy this directory as the
  reference "auth-on local dev" setup; swap the static password for the
  adopter's IdP later.
- **CI** — Dex is small enough to run as a service container in a
  GitHub Actions job; the same recipe applies (`PROMPTPOTTER_AUTH=on`
  + `oidc.json` from this dir).

## Switching to a different IdP

Same shape — point the four discovery URLs at any OIDC-conformant
provider:

| Provider | Discovery URL |
|---|---|
| Auth0 | `https://<tenant>.auth0.com/.well-known/openid-configuration` |
| Keycloak | `https://<host>/realms/<realm>/.well-known/openid-configuration` |
| Okta | `https://<tenant>.okta.com/.well-known/openid-configuration` |
| Dex (this harness) | `http://localhost:5556/dex/.well-known/openid-configuration` |

Read the four endpoints out of the provider's `well-known` document and
drop them into `oidc.json::google.{issuer,authorize_url,token_url,jwks_url}`.

## Teardown

```bash
cd dev/oidc-local
docker compose down
# optionally: remove the harness identity configs
rm ../../.promptpotter/identity/oidc.json ../../.promptpotter/identity/allowlist.json
```
