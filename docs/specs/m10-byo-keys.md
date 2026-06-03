# M10: BYO Per-User API Keys

**Status:** SPEC ONLY — no code. Beta-blocker for the hosted multi-tenant deploy (`app.promptpotter.dev`). First consumer of the identity seam for *write* secrets, after ADR-0002 (identity) and ADR-0003 (spend on ledger).

## Problem

Every user spends the **operator's** quota. The LLM clients read their key from the process-global `settings`:

- `infrastructure/llm/registry.py:59` — `api_key=getattr(settings, spec.api_key_attr)` (Groq / OpenAI / OpenRouter).
- `infrastructure/llm/anthropic.py` — `AnthropicClient.__init__` reads `settings.ANTHROPIC_API_KEY`.

`get_llm_client(provider)` takes only a provider string — no identity reaches the construction site. On the live tunnel (OIDC + allowlist), every authenticated user's optimizer + backend LLM calls bill the single `.env` key. A user with a stated provider preference still spends the host's tokens. This is a present liability: uncapped spend on a shared credential, no per-user attribution at the *key* level (spend attribution on the ledger works; the *credential* does not).

## Principle

A per-user key rides the **identity seam** (ADR-0002): tenant-scoped, encrypted at rest, resolved at client-construction time from the `IdentityContext` already threaded through `build_stores`. Resolution order: **per-tenant key → `.env` global (auth-off / unset) → clear error**. No silent shared-key spend: an authenticated user with no key set and no global fallback gets a clean `no_api_key` error, never a quiet bill against the host.

## The single interception point

`get_llm_client(provider)` is the one choke point — every optimizer and backend LLM client is built through it (`registry.py`) or its Anthropic peer. Keep the registry **identity-free**; resolve the key at the construction call sites that already hold `Stores`, and thread it in explicitly:

```
get_llm_client(provider, *, api_key: str | None = None) -> LLMClientBase
```

- `api_key=None` (today's call shape, CLI / tests) → registry falls back to `getattr(settings, spec.api_key_attr)` exactly as now (auth-off parity).
- `api_key="…"` → the factory uses it instead of the settings attr.

The two construction sites that hold identity resolve the key first:

- `application/config.py::create_llm_client(campaign_config)` → gains `stores` access, calls `resolve_api_key(stores.identity, provider, stores)`.
- `application/datasets/origin_resolve.py::resolve_origin_turn` (the origin check-in already holds `stores`) → same resolver.

```
resolve_api_key(identity: IdentityContext, provider: str, stores: Stores) -> str | None
  1. key = stores.api_keys.get(provider)        # per-tenant, decrypted
  2. if key: return key
  3. return None                                # → registry uses .env global
```

The `no_api_key` gate lives in the registry: if `api_key is None` **and** the settings attr is empty **and** identity is not the auth-off default, raise `MissingApiKeyError(provider)` → 422 `no_api_key` at the API boundary (clear, actionable — "set a key for {provider}"). Auth-off (`default_identity()`) keeps the empty-key-is-fine behaviour for local single-operator runs.

## Per-tenant encrypted store

New `TenantApiKeyStore` (pattern: `UserStore` — one JSON file per tenant), at `projects/{tenant}/api_keys.json`:

```json
{
  "providers_set": ["groq", "anthropic"],            // plaintext index — AI/operator-readable (gate #6)
  "keys": { "groq": "<fernet-ciphertext>", "anthropic": "<fernet-ciphertext>" }
}
```

- **Encryption:** Fernet (`cryptography>=42`, already a dep — used for OIDC JWS today). Symmetric key from a new `settings.SECRETS_FERNET_KEY` (env, 32-byte urlsafe-base64); absent ⇒ store refuses writes with a clear startup error (no plaintext-at-rest fallback). Rotation is out of scope this slice (single active key).
- **`providers_set`** is plaintext so the operator (and the AI reading the file tree) sees *which* providers have a key without decrypting — the ciphertext never leaks, but "is a key set" is on disk (gate #6: material facts readable without running the CLI).
- Add `api_keys: TenantApiKeyStore` to the `Stores` composite (`store/stores.py`); build at `build_stores` rooted at the tenant dir — identity scope rides the existing prefix, never a per-record `tenant_id` (ADR-0002 gate #3).

## Set / clear verb (Control-remote, openapi-first)

Declare in `m12-api-openapi.yaml` **before** any handler (gate #4):

- `PUT /auth/api-keys/{provider}` — body `{api_key}`; writes the encrypted entry; `204`. Validates `provider ∈ {groq, openai, anthropic, openrouter}`. The key is **never** echoed back (request-only; the response carries `providers_set`, not the value).
- `DELETE /auth/api-keys/{provider}` — clears it; `204`.
- `GET /auth/api-keys` — returns `{providers_set: [...]}` only (which providers have a key, never the values). The existing `GET /llm-providers` gains a `key_source: "user" | "shared" | "none"` per provider so the IngestPane optimizer picker shows whether a campaign will spend the user's key or the host's.

These ride `/auth/*` (identity-router-owned, like `logout`), not the cycle command highway — they mutate tenant secret state, not a cycle ledger. Token boundary: the plaintext key appears only in the `PUT` request body and the encrypt call; it never lands on any ledger, log, trace, or response (ADR-0002 gate #2 — secrets never past the boundary).

## Spend stays on the ledger

Unchanged. `TokenUsageRecord` via `emit_token_usage` already attributes spend per tenant (ADR-0003); BYO keys change *whose credential pays*, not *how spend is recorded*. `/auth/quota-status` + `/auth/activity` keep working. A per-user key does **not** lift the per-user quota — quota is a host policy independent of credential ownership (a user on their own key may still be rate/spend-capped by the host; document this in the Account modal copy).

## Pre-flight gate

1. **§0 bucket** — Identity (per-tenant secret) + on-disk (encrypted store + plaintext `providers_set` index) + Control-remote (`PUT`/`DELETE /auth/api-keys`).
2. **Existing channel?** — key resolution rides the existing `IdentityContext` / `build_stores` seam + the single `get_llm_client` choke point; only the store + the resolver + the verb are new.
3. **Name distinct?** — `TenantApiKeyStore`, `resolve_api_key`, `MissingApiKeyError`, `SECRETS_FERNET_KEY` — grep-clean.
4. **Self-describing + new I/O kind?** — no new I/O kind (rides Identity / Persistence / Control-remote). New verbs declared in `m12-api-openapi.yaml` first.
5. **Rides existing infra?** — yes: `Stores` composite, identity seam, registry choke point. The only sidecar is the per-tenant `api_keys.json` (a peer of `user.json`, same store pattern).
6. **AI-readable on disk?** — yes: `providers_set` plaintext index says which providers have a key; the ciphertext stays opaque by design.
7. **§0 update?** — no; an application/infra-layer secret store + resolver, not a backbone change. ADR-0002's Identity I/O kind already covers "secrets ride the seam."
8. **Langfuse trace?** — no new LLM call site; the key only changes which credential the *existing* wrapped calls use.

## Non-goals

Key rotation / multiple active Fernet keys · per-campaign key override distinct from per-tenant · BYO for the backend pipeline node's own LLM (that rides `pipeline_overlay`; a separate slice) · a KMS / external secrets manager (file + Fernet is the slice; KMS is a deploy-hardening follow-up) · lifting per-user quotas for BYO users.
