# Identity Foundation — OIDC wire + RLS data, sized 1 → 1B users

> **Status:** Forward direction — load-bearing foundation. No code shipped under this spec; the existing `TenantContext` (`application/bootstrap/session.py:33`) is the Stage-0 reification.
> **Load-bearing scope:** every multi-tenant code path in the cluster ([`spend-and-tenancy.md`](spend-and-tenancy.md), [`m12-control-plane.md`](m12-control-plane.md), [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md), [`state-sync-cleanup.md`](state-sync-cleanup.md)) consumes the contracts pinned here. **Cluster front-door.**

The premise: the codebase commits today to two wire-and-data contracts that scale from one operator on a laptop to Facebook/Netflix-shape without rewriting prior code. Every other multi-tenant spec is a **consumer**, not a peer.

## Why a foundation doc

"Thread `TenantContext` everywhere, we'll figure out auth later" is the path that produced every rewrite-the-identity-layer story in industry. The cheap insurance: pick the two contracts the giants already standardized on, shape our seams to them now, and let the *provider* of identity become a runtime config choice.

- **OIDC** is the wire contract every IdP speaks (Google, Microsoft Entra, Apple, Okta, Auth0, Keycloak, Ory, Zitadel, Authentik). [RFC 6749 / OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc6749), [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html).
- **PostgreSQL row-level security (RLS)** is the data-isolation contract Shopify, Discord, GitLab, and Supabase build multi-tenant on top of. [PostgreSQL RLS docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [Supabase RLS guide](https://supabase.com/docs/guides/database/postgres/row-level-security).

Shape the seams once. Swap the implementation per stage.

## Three-stage staging

| Stage | Who we serve | Identity source | Data layer | Code delta from prior stage |
|---|---|---|---|---|
| **Stage 0 — today** | one operator, one machine | `IdentityContext(user_id="default", tenant_id="default")` from bootstrap; auth-off | file-based, tenant-prefixed (`projects/{tenant_id}/`) | nothing — `spend-and-tenancy.md` lands the `IdentityContext` reification |
| **Stage 1 — small SaaS / casual multi-user** | dozens to thousands of end-users | **OIDC client** to Google / Apple / GitHub / Microsoft. We never store passwords, never run a passkey ceremony — we federate to providers whose passkey UX already works. `(provider, subject) → user_id` mapping is the only thing we persist. | same file-based layout, tenant-prefixed for real | one OIDC client module (~200 LoC) + one dep (`cryptography` for JWT signature verify) |
| **Stage 2 — Facebook / Netflix-shape** | millions+; B2B SSO; enterprise compliance | **Become OIDC provider** — front Ory Hydra / Zitadel / Authentik / Keycloak (open-source IdPs publicly run by enterprises) as a sibling process; we own the issuer URL. | PostgreSQL + RLS; the file-based stage migrates behind a clean store adapter | swap the issuer; data layer migrates via the adapter — no application-code rewrite |

Each stage's code is forward-compatible with the next. Reaching Stage 2 from Stage 0 is two additive jumps, not one rewrite.

## The two contracts

### Contract A — OIDC (wire / identity ingress)

Every request that crosses a trust boundary into the application carries an **OIDC-shape identity claim**. Internally, the seam below the boundary takes an `IdentityContext`, not a token.

- **ID Token shape** — `iss` (issuer URL), `sub` (subject — stable per-user id within the issuer), `aud` (audience — our application's client id), `exp` / `iat`, plus a custom `tenant_id` claim. Verify signature against the issuer's JWKS (`/.well-known/jwks.json`) — keys rotate, the discovery endpoint is the source of truth.
- **Discovery** — `{issuer}/.well-known/openid-configuration` provides authorization / token / JWKS / userinfo endpoints; the client treats this URL as the only piece of provider config it hardcodes.
- **Tokens never enter app code.** They're verified at the boundary (`presentation/api/middleware/`); past the boundary, the codebase sees `IdentityContext` only. PR rule: a JWT type appearing outside `presentation/api/middleware/` and `infrastructure/identity/` is a block.
- **Session pattern** — first-party browser sessions are **server-side sessions keyed by HttpOnly cookie**, per [The Copenhagen Book](https://thecopenhagenbook.com/sessions) (Lucia Auth's author deprecated his library in favor of these patterns; framework-agnostic; OWASP-aligned). **ID Tokens are only for cross-trust-boundary** (callback from the provider, B2B SSO, service-to-service). The "stuff a JWT in a cookie" anti-pattern is an explicit gate violation.
- **Code anchor (Stage 1+):** `promptpotter/infrastructure/identity/` (new) — `OIDCClient`, `IdTokenVerifier`, `JWKSCache`. ~200 LoC plus `cryptography` for signature verification.
- **Code anchor (Stage 0):** `IdentityContext` constructed by `presentation/api/deps.py::identity_context` returning the auth-off default; CLI seam returns the same default from `presentation/cli/commands/_shared.py::init_services_cli`.

### Contract B — PostgreSQL RLS (data / storage isolation)

The tenant boundary is enforced **at the data engine**, not by every `WHERE tenant_id = ?` a developer remembers to write. RLS makes cross-tenant reads structurally impossible, not just conventionally avoided.

- **Today's file-based layout is the degenerate case.** `projects/{tenant_id}/` is a one-tenant-per-directory isolation primitive enforced by the OS. The contract shape — "every storage operation is scoped to `IdentityContext.tenant_id`, no exceptions" — is the same.
- **Stage-2 form** — every tenant-scoped table carries a `tenant_id` column; an RLS policy of the form `USING (tenant_id = current_setting('app.tenant_id')::uuid)` is attached; the application sets `SET LOCAL app.tenant_id = …` at the start of each request inside a transaction. The store adapter (below) is the single place this happens.
- **Store adapter** — `infrastructure/store/stores.py::build_stores(identity: IdentityContext, …)` is the single construction route. Stage 0 returns file-backed stores rooted at `projects/{tenant_id}/`. Stage 2 returns Postgres-backed stores that set the RLS session variable. **No application caller knows which.** The RLS adapter is a swap, not a rewrite.
- **Cross-tenant primitives.** `archive/measurements/` is dataset-scoped + cross-campaign by design (per `docs/architecture.md` §0). It stays cross-tenant within a single install; cross-*install* sharing is an explicit non-goal (per `m13-chat-first-user-web.md`). The RLS policy on archive tables omits the tenant filter; install-level isolation comes from running separate databases per install.

## Data model — SCIM 2.0 Core + EnterpriseUser

The internal `User` / `Group` shape is **SCIM 2.0 Core + EnterpriseUser** ([RFC 7643](https://datatracker.ietf.org/doc/html/rfc7643)). Every workforce IdP — Microsoft Entra, Okta, Google Workspace, Auth0, OneLogin, Ping — already produces and consumes SCIM resources; the schema is JSON Schema, vendor-portable, and a decade mature ([WorkOS 2026 SCIM provider survey](https://workos.com/blog/scim-providers)). Adopting it now means our user records speak the same vocabulary every enterprise integration will ask for, with zero translation layer.

### SCIM ↔ OIDC field mapping

Populate SCIM fields from OIDC standard claims ([OIDC Core §5.1](https://openid.net/specs/openid-connect-core-1_0.html#StandardClaims)) at login. This table is the seam between the wire contract (OIDC) and the data model (SCIM).

| SCIM field | OIDC claim source | Notes |
|---|---|---|
| `id` | (local UUID) | Locally minted, stable per (issuer, subject) — never the raw `sub`. |
| `externalId` | `sub` | Verbatim subject from the issuer. |
| `userName` | `preferred_username` ‖ `email` | Unique within the install. |
| `name.givenName` | `given_name` | |
| `name.familyName` | `family_name` | |
| `name.formatted` | `name` | |
| `displayName` / `nickName` | `nickname` | |
| `emails[{value, type, primary}]` | `email` + `email_verified` | `primary=true`, `type="work"` default. |
| `phoneNumbers[]` | `phone_number` | |
| `photos[]` | `picture` | |
| `preferredLanguage` / `locale` | `locale` | |
| `timezone` | `zoneinfo` | |
| `active` | (derived) | `true` while the OIDC session is valid + the user is not soft-deleted. |
| `groups[]` | (provider-specific claim, e.g. `groups`) | Group memberships at the IdP. |
| `roles[]` | (provider-specific claim, e.g. `roles`) | Application-level role assignments — see [Authorization swap-target table](#authorization-swap-target-table). |
| `entitlements[]` | (provider-specific claim) | Capability grants. |
| `meta.{created, lastModified, version}` | (local) | Standard SCIM resource metadata. |

The **EnterpriseUser extension** (`urn:ietf:params:scim:schemas:extension:enterprise:2.0:User`) adds `employeeNumber`, `costCenter`, `organization`, `division`, `department`, `manager`. Free once Core is adopted; populated from provider-specific claims when present (Microsoft Entra emits most of these natively).

### Tenant claim normalization — `org_id`

`org_id` is the canonical internal field name for the tenant claim. No RFC blesses one name; `org_id` is winning the B2B SaaS literature battle (Auth0 Organizations emits it natively; WorkOS, Frontegg, PropelAuth, Stytch follow). Normalize at the edge:

- Auth0 Organizations → native `org_id` claim → passthrough.
- Microsoft Entra → `tid` claim → rename to `org_id` in the verifier.
- Custom IdP / Stage-2 own-issuer → emit `org_id` directly.

`IdentityContext.tenant_id` is populated from `org_id`; any serialized form of the context (logs, ledger events, audit trail) uses the field name `org_id`. **One internal name, one external claim name, one mapping rule per IdP.**

### Schema.org `Person` JSON-LD — output projection only

[Schema.org `Person`](https://schema.org/Person) ([`schemaorg/schemaorg`](https://github.com/schemaorg/schemaorg)) is emitted as JSON-LD on marketing pages and public profile surfaces for SEO. **It is an output projection, not the internal model.** Its 70+ Person properties include noise the application has no use for (`spouse`, `netWorth`, `height`, `colleague`). The application stores SCIM; the public-facing renderer projects a small subset of SCIM fields into Schema.org JSON-LD at response time.

## `IdentityContext` — the new layer above `TenantContext`

`TenantContext` (`application/bootstrap/session.py:33`) describes *which slice of storage* a call uses. `IdentityContext` describes *who is making the call*. They're distinct concerns; `IdentityContext` carries (and supersedes) the `TenantContext` data.

```python
@dataclass(frozen=True)
class IdentityContext:
    user_id: UserId                  # NewType[str]; stable per (issuer, subject)
    tenant_id: TenantId              # NewType[str]; storage scope
    issuer: Issuer | None            # NewType[str]; None in Stage-0 auth-off
    claims: Mapping[str, object]     # raw verified ID-Token claims; empty in Stage-0
    capabilities: frozenset[str]     # flat capability set; RBAC is post-M13
```

- **Stage 0** — `IdentityContext(user_id=UserId("default"), tenant_id=TenantId("default"), issuer=None, claims={}, capabilities=frozenset())`. Constructed once at bootstrap. The single-operator path is the auth-off branch — one branch, not a feature flag.
- **Stage 1** — constructed by the OIDC middleware from a verified ID Token. `user_id = f"{issuer}:{sub}"`, `tenant_id` from the custom `tenant_id` claim (provider-set for B2B, install-scoped fallback for casual users), `issuer` from `iss`.
- **`TenantContext` collapses into `IdentityContext`.** `Session.tenant: TenantContext | None` (`application/bootstrap/session.py:74`) becomes `Session.identity: IdentityContext`. The spend-and-tenancy seam — and every consumer — takes `IdentityContext`, never bare `tenant_id` or bare `TenantContext`. **Behavior change, no shim.**

## No-drift gates

Enforceable rules. A PR violating any of these is a block; gates marked **(test)** land as `tests/test_invariants.py` checks.

1. **Every API request resolves an `IdentityContext`.** No router accepts an unauthenticated request outside the auth-off boundary. **(test)** — middleware coverage check.
2. **No JWT in first-party cookies.** Session cookie is an opaque server-side session id, period. ID Tokens cross trust boundaries only. **(test)** — grep for JWT types in `presentation/` outside the middleware path.
3. **No `tenant_id: str` parameter past the seam.** Every store / query call takes `IdentityContext`; `tenant_id` is a `TenantId` newtype derived from it. **(test)** — `build_stores` signature + caller scan.
4. **`IdentityContext.tenant_id` is the only source of tenant scope.** No `request.headers["X-Tenant"]`, no `args.tenant` past the resolver. The seam derives, downstream consumes. **(test)** — invariant on the resolver call sites.
5. **Adding a new identity-bearing ingress amends §0 first.** Per CLAUDE.md §6 Q4 sub-rule. Stage 1 (OIDC ingress on the API) and Stage 2 (B2B SSO ingress, eventual SAML/SCIM) each require an §0 note.
6. **Internal `User` / `Group` shape uses SCIM 2.0 field names verbatim.** No custom field invention that diverges from SCIM Core or EnterpriseUser. New fields land in the SCIM extension namespace (`urn:ietf:params:scim:schemas:extension:<name>:2.0:User`). **(test)** — invariant on the User dataclass field set.

## Minimal-deps invariant

- **Stage 0 — zero new deps.** Stdlib only. The existing `TenantContext` is reified into `IdentityContext`; no library required.
- **Stage 1 — one new Python dep: `cryptography`** for JWT/JWS signature verification against JWKS. Everything else (HTTP discovery fetch, session cookies, opaque token generation, PKCE) is stdlib. The OIDC client is ~200 LoC we write ourselves.
- **Stage 2 — zero new Python deps.** Open-source IdPs (Ory / Zitadel / Keycloak / Authentik) are **sibling processes** — we call them over HTTP/OIDC, we do not import a Python auth library. The PostgreSQL adapter rides our existing storage abstractions plus the `psycopg` binding we'd already need for any DB store.
- **Never** add a Python auth library (no `python-jose`, no `authlib`, no `python-social-auth`, no `flask-login`-shape framework). Either we implement OIDC client ourselves (Stage 1) or we call out to a sibling IdP (Stage 2).
- Reference for zero-dep patterns: [The Copenhagen Book](https://thecopenhagenbook.com/). Framework-agnostic, OWASP-aligned, by Lucia Auth's author after he deprecated his library in favor of the patterns themselves.
- **Schema vendoring (not a Python dep).** Vendor [`bjmc/scim-jsonschema`](https://github.com/bjmc/scim-jsonschema) as a git submodule at `vendor/schemas/scim/`, tag-pinned. JSON Schema files for `User`, `Group`, `EnterpriseUser` plus RFC 7643 normative text live in our tree, version-pinned. **No `pip install`, no runtime cost** — schema vendor, not a library import. Schema.org JSON-LD context copies from [`schemaorg/schemaorg`](https://github.com/schemaorg/schemaorg) on demand for the public-facing projection only (submodule optional; copy-and-pin fine).

## Future-swap-target table (Stage 2 IdPs)

Pre-vetted swap targets when we want to delegate the **provider** role (i.e. become an OIDC provider ourselves by fronting one). The OIDC wire makes the swap trivial — middleware re-targets, application unchanged. **Not Day-1 dependencies.** Adopt one at Stage 2 if/when needed.

| IdP | Lang / shape | Publicly cited users | Notes |
|---|---|---|---|
| **[Ory Hydra + Kratos](https://www.ory.sh/)** | Go binaries; HTTP API | T-Mobile, OVHcloud, Padelmania ([case studies](https://www.ory.sh/case-studies)) | Apache 2.0. Hydra = OAuth2/OIDC server; Kratos = identity & user management. Composable. |
| **[Zitadel](https://zitadel.com/)** | Go, single-binary | [Public customer list](https://zitadel.com/customers) | Apache 2.0. Multi-tenant from day one — the others tack it on. |
| **[Keycloak](https://www.keycloak.org/)** | Java / Quarkus | Red Hat ecosystem; CNCF graduated; widely deployed in enterprise (Hitachi, Cisco internal references) | Apache 2.0. Heaviest, most feature-complete. SAML + OIDC + LDAP federation. |
| **[Authentik](https://goauthentik.io/)** | Python, single-binary | Self-hosters; growing enterprise adoption | MIT. Matches our stack but we still call it over HTTP — no Python import. |

Selection criterion when the time comes: multi-tenancy story + operational fit. **Zitadel** is the front-runner under today's information.

## Authorization swap-target table

Stage 0/1 authorization is **SCIM-named RBAC columns in Postgres + RLS** — `memberships(user_id, tenant_id, role)` with role values like `owner`, `editor`, `viewer`. The SCIM `User.roles` / `User.entitlements` / `Group.members` fields are the lowest-common-denominator IdPs speak (RFC 7643 defines them but punts semantics — we pin the semantics). RLS already enforces the tenant edge; role checks are a `WHERE role IN (…)` away.

**Stage-2 trigger** — graduate to Google Zanzibar-shape relational ReBAC when one of these is required:

- cross-tenant sharing (a resource owned by tenant A is visible to specific users in tenant B);
- group-of-groups / hierarchical organizational units (engineering ⊃ platform ⊃ identity);
- "who has access to X" reverse-lookup queries (compliance audits, leak investigations).

Pre-vetted swap targets, same shape as the IdP table. **Not Day-1 dependencies.** Adopt one at Stage 2 if/when the trigger fires.

| Authz engine | Lang / shape | Publicly cited users | Notes |
|---|---|---|---|
| **[OpenFGA](https://openfga.dev/)** | Go, HTTP/gRPC | Docker, Grafana, Okta, Auth0, Canonical, Sourcegraph | CNCF sandbox, Apache 2.0. Zanzibar-shape relationship tuples. Safest pick — broad adoption, vendor-neutral. |
| **[SpiceDB](https://authzed.com/spicedb)** | Go, gRPC | Netflix, Reddit, Turo, Headspace | Apache 2.0. Cleaner `.zed` schema language; closer to the Zanzibar paper. |
| **[Permify](https://permify.co/)** | Go | Newer entrant | Apache 2.0. Smaller community than OpenFGA / SpiceDB. |
| **[Cerbos](https://cerbos.dev/)** | Go, YAML policies | Various | Policy-engine, NOT Zanzibar-graph — pick only if relational ReBAC isn't needed and rule-based ABAC suffices. |

Skip-list (do not adopt):

- **XACML** — legacy enterprise XML-policy standard; supplanted by OPA in modern stacks.
- **Casbin** — library-not-spec; embedded per language; weak SaaS-vendor adoption vs. the four above.
- **AWS IAM JSON policies** — vendor-locked to AWS; unsuitable as application-internal authz.
- **OAuth scopes** — API-gating coarse layer, not app-internal authz; orthogonal concern.

## Code anchors

| Concern | Stage | File |
|---|---|---|
| `IdentityContext` (new) | 0+ | `promptpotter/application/bootstrap/session.py` (replaces `TenantContext`) |
| `UserId` / `Issuer` newtypes (new) | 0+ | `promptpotter/domain/identity.py` (new; sibling to `domain/tenant.py` from `spend-and-tenancy.md`) |
| `TenantId` newtype | 0+ | `promptpotter/domain/tenant.py` (per `spend-and-tenancy.md`) |
| Auth-off resolver | 0 | `promptpotter/presentation/api/deps.py::identity_context` (returns default) |
| CLI seam | 0+ | `promptpotter/presentation/cli/commands/_shared.py::init_services_cli` |
| Store construction | 0+ | `promptpotter/infrastructure/store/stores.py::build_stores(identity, …)` |
| OIDC client (Stage 1) | 1 | `promptpotter/infrastructure/identity/` (new) — `client.py`, `verifier.py`, `jwks.py`, `session.py` |
| OIDC middleware (Stage 1) | 1 | `promptpotter/presentation/api/middleware/oidc.py` (new) |
| RLS adapter (Stage 2) | 2 | `promptpotter/infrastructure/store/postgres/` (new) — drop-in for the file-based `stores.py` |
| §0 I/O kind | 1 | `docs/architecture.md` §0 — `Identity` ingress (see "§0 amendment" below) |

## §0 amendment

**Yes.** Identity-foundation introduces a new I/O ingress — the OIDC verification step at the API boundary — that doesn't fit any existing kind. Today's §0 names Persistence / Display / Control-local, with M12 adding Control-remote. **`Identity` is a fourth distinct kind** because:

- It mutates no campaign state (rules out Persistence).
- It's not a ledger subscriber (rules out Display).
- It doesn't signal the loop (rules out Control-local / -remote).
- It's the *gate* that establishes who the subsequent Control-remote call is *from*.

The amendment lands as a docs-only PR before any Stage-1 code. CLAUDE.md §6 Q4 sub-rule applies — the gate blocks code introducing a new I/O kind without §0 backing.

Stage-0 work (the `IdentityContext` reification under `spend-and-tenancy.md`) does **not** require the amendment — it's a refinement of the existing `TenantContext` seam, not a new ingress. The amendment lands with Stage 1.

## What's out of scope (explicit)

- **Feature surface** — login screens, settings UI, profile pages, account-merge flows. Those land in `m13-chat-first-user-web.md` and live below this foundation.
- **Provider choice for Stage 2** — Ory vs Zitadel vs Keycloak vs Authentik. The table above is pre-vetting; the decision is made when Stage 2 is imminent.
- **Implementation timing.** This spec doesn't schedule Stage 1 or Stage 2. The cluster's milestone specs (M12 / M13) own scheduling.
- **OAuth 2.1 / third-party access tokens for our API.** Stage 2+. First-party use case (a user signs into our webapp) doesn't need it.
- **SCIM provisioning.** Enterprise-tier — Stage 2+ at earliest, defer until requested.
- **SAML.** Enterprise B2B SSO — Stage 2+. Delegated to whichever IdP we front (Keycloak / Ory / Zitadel speak it; we don't implement SAML ourselves).
- **WebAuthn / passkey *provider* mode.** Stage 2 or later. Stage 1 leverages Google / Apple / Microsoft's passkey implementations via OIDC federation — we get passkey UX for free without owning the ceremony.
- **RBAC beyond a flat `frozenset[str]`** — `IdentityContext.capabilities` exists but stays empty until a real authorization model is needed.
- **Billing / quotas / per-tenant rate limiting.** Post-M13.

## Cross-refs (consumer specs)

- [`spend-and-tenancy.md`](spend-and-tenancy.md) — **first consumer.** Lands the `IdentityContext` reification at Stage 0; spend tracking is the payload demonstrating the seam works end-to-end.
- [`m12-control-plane.md`](m12-control-plane.md) — **second consumer.** Stage 1 OIDC client lands here; `JobRegistry` scopes on `IdentityContext`; auth-off mode is the Stage-0 fallback.
- [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) — **third consumer.** Install / User / Project nouns map onto OIDC claims (`Install = iss`, `User = sub`, project scoping rides `tenant_id` claim). Stage 2 considered when self-hosters demand native identity.
- [`state-sync-cleanup.md`](state-sync-cleanup.md) — convergence: identity-collapse touches the same store-seam files; sequence Phase 1 before the Stage-0 `IdentityContext` reification to avoid touching `index.json` writers twice.
- [`docs/architecture.md` §0](../architecture.md) — Identity I/O kind amendment lands here at Stage 1.
