---
status: accepted
date: 2026-05-26
deciders: [maintainer]
consulted: [spend-and-tenancy, m12-control-plane]
informed: []
relates:
  - docs/adr/0001-m12-control-plane.md
  - docs/adr/0003-spend-and-tenancy.md
  - docs/adr/0004-operator-admin-channels.md
  - docs/specs/roadmap.md
  - docs/specs/roadmap.md
supersedes: []
superseded-by: []
tags: [identity, oidc, rls, scim, multi-tenancy, foundation]
---

# Identity Foundation — OIDC wire + RLS data, sized 1 → 1B users

## Context and Problem Statement

The codebase ships today as a single-operator CLI plus a read-only webapp. Every downstream multi-tenant spec ([`0001-m12-control-plane.md`](0001-m12-control-plane.md), [`0003-spend-and-tenancy.md`](0003-spend-and-tenancy.md), [`../specs/roadmap.md`](../specs/roadmap.md), [`../specs/roadmap.md`](../specs/roadmap.md)) sits on top of *some* identity contract. Without a foundation pick, each consumer invents its own — "thread `TenantContext` everywhere, we'll figure out auth later" is the path that produces every rewrite-the-identity-layer story in the industry.

How do we shape the codebase's identity + data-isolation seams now so the same application code runs unchanged from one operator on a laptop (Stage 0) through small-SaaS multi-user (Stage 1) all the way to Facebook / Netflix-shape (Stage 2)?

## Decision Drivers

* **OIDC ubiquity.** Every IdP every customer will ever ask us to integrate with — Google, Microsoft Entra, Apple, Okta, Auth0, Keycloak, Ory, Zitadel, Authentik — already speaks [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html) over [OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc6749). Picking OIDC at the wire means the *provider* becomes a runtime config choice, not a code rewrite.
* **PostgreSQL RLS is battle-tested.** Shopify, Discord, GitLab, and Supabase enforce tenant isolation at the data engine with [row-level security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) ([Supabase RLS guide](https://supabase.com/docs/guides/database/postgres/row-level-security)). Cross-tenant reads become structurally impossible, not conventionally avoided.
* **SCIM 2.0 is the vendor-portable user model.** Every workforce IdP (Entra, Okta, Google Workspace, Auth0, OneLogin, Ping) produces and consumes [SCIM Core](https://datatracker.ietf.org/doc/html/rfc7643) resources. Adopting it now means our user records speak the same JSON-Schema vocabulary every enterprise integration will ask for, with zero translation layer ([WorkOS 2026 SCIM provider survey](https://workos.com/blog/scim-providers)).
* **Federation beats passwords.** We never store passwords, never run a passkey ceremony — we federate to providers whose passkey UX already works. `(provider, subject) → user_id` is the only thing we persist.
* **Swap-target liquidity.** Stage 2's IdP (Ory / Zitadel / Keycloak / Authentik) and authz engine (OpenFGA / SpiceDB / Permify / Cerbos) decisions stay deferrable as long as our seams speak OIDC; pre-vetting candidates today costs nothing.

## Considered Options

* **A: OIDC wire + RLS data + SCIM 2.0 internal model.** Federate identity to industry-standard IdPs; isolate tenants at the data engine; speak the same user vocabulary every workforce tool already speaks.
* **B: Roll-our-own auth library.** Build session management, password hashing, MFA, passkey ceremony, IdP federation, SCIM emission, RLS adapter from scratch.
* **C: Third-party Python auth library (`python-jose` / `authlib` / `python-social-auth` / `flask-login`-shape framework).** Add a dependency that handles the auth wire end-to-end.
* **D: JWT-in-cookie shortcut.** Skip server-side sessions; stuff a verified ID Token (or our own signed token) into a first-party cookie for browser session state.
* **E: Postgres-only, no IdP.** Hash passwords ourselves, manage MFA, ship `users` + `sessions` tables, accept the indefinite roadmap cost of being our own identity provider.

## Decision Outcome

Chosen option: **A — OIDC wire + RLS data + SCIM 2.0 internal model.**

The two contracts shape the seams once; the implementation behind each seam swaps per stage. Stage 0 ships a single-operator `IdentityContext(user_id="default", tenant_id="default")` at init with no auth and a file-based `projects/{tenant_id}/` data layout. Stage 1 swaps the resolver for an OIDC client (~200 LoC + `cryptography`) federating to Google / Apple / Microsoft / GitHub. Stage 2 swaps the file-based stores for a PostgreSQL adapter that sets `SET LOCAL app.tenant_id` per request inside RLS-protected transactions, optionally fronting Ory / Zitadel / Keycloak / Authentik as the issuer. **Reaching Stage 2 from Stage 0 is two additive jumps, not one rewrite.**

Internal `User` / `Group` records use SCIM 2.0 Core + EnterpriseUser field names verbatim. The tenant claim normalizes to `org_id` at the verifier edge. Schema.org `Person` JSON-LD is an output projection on public surfaces — not the internal model. `IdentityContext` (5-field frozen dataclass at `promptpotter/shared/identity.py`) is the sole carrier past the seam; the deleted `TenantContext` collapses into it.

**Administering the gate** is a facet of this kind, not a new one. Editing the sign-in blocklist or provider config is an identity-config *write* — delivered through an in-zone **operator-admin channel** (outbound conduit, no inbound public route) and audited in the identity zone, never the campaign Control-remote highway and never a tenant ledger. The channel pattern + Purdue/zero-trust threat model live in [`0004-operator-admin-channels.md`](0004-operator-admin-channels.md).

### Consequences

* **Good** — provider swap is one resolver function. Stage 2 IdP swap is a docker-compose entry; application unchanged.
* **Good** — zero auth deps in Stage 0; one tightly-scoped dep (`cryptography`) in Stage 1; zero in Stage 2 (sibling-process IdPs, not Python imports).
* **Good** — SCIM-named records integrate with every workforce IdP without a translation layer.
* **Good** — RLS makes cross-tenant data leaks structurally impossible at Stage 2; the Stage-0 file layout is the degenerate one-tenant-per-OS-directory case of the same contract.
* **Good** — pre-vetted Stage-2 swap targets (IdPs + authz engines) decouple our roadmap from any single vendor's survival.
* **Neutral** — Stage 1 adds the `cryptography` Python dep for JWT/JWS signature verification against JWKS.
* **Bad** — we own ~200 LoC of OIDC client code (`infrastructure/identity/`). Tradeoff vs. pulling a Python auth library is intentional ([The Copenhagen Book](https://thecopenhagenbook.com/), Lucia Auth deprecation rationale).

### Confirmation

The no-drift gates that protect the seam from regression — gates #3 (`build_stores` signature), #4 (`Stores.identity` sole tenant source), #6 (SCIM-named field set) — are enforced by the typed seam itself (a wrong `build_stores` signature fails to typecheck) plus review; there is no standing `test_invariants.py` (the structural/contract suite was cut to the silent-harm core, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)). The §0-first rule (CLAUDE.md Pre-flight gate Q4) blocks any new identity-bearing ingress from landing without amending `docs/architecture.md` §0; Stage 1 (OIDC ingress) and Stage 2 (B2B SSO) each require an §0 note.

## Pros and Cons of the Options

### A — OIDC wire + RLS data + SCIM 2.0 internal model

* **Good** — every customer's chosen IdP is a runtime config change.
* **Good** — data-engine enforcement of tenant isolation; cross-tenant reads structurally impossible.
* **Good** — SCIM records speak every workforce IdP's vocabulary verbatim.
* **Good** — staging is additive (Stage 0 → 1 → 2 each adds one swap, no rewrite).
* **Neutral** — three standards to shape against (OIDC + RLS + SCIM) instead of one.
* **Bad** — Stage 1 requires us to own ~200 LoC of OIDC client.

### B — Roll-our-own auth library

* **Good** — total control.
* **Bad** — every IdP integration becomes our work; every CVE in the field is our research; every compliance question is our certification.
* **Bad** — passkey ceremony, MFA, federation, SCIM emission all become net-new code we maintain forever.

### C — Third-party Python auth library

* **Good** — fastest Stage-1 ship.
* **Bad** — Python auth libraries are graveyards (Lucia Auth deprecated by its own author in favor of framework-agnostic patterns; `python-social-auth` largely unmaintained; `authlib` opinionated; `flask-login` framework-tied).
* **Bad** — the OIDC client is ~200 LoC; the library adds a dependency surface 100× that for the wrapping.

### D — JWT-in-cookie shortcut

* **Good** — no server-side session store.
* **Bad** — explicit OWASP anti-pattern; logout requires token revocation lists or short TTLs (re-invents server-side sessions).
* **Bad** — claims become stale; cookie size grows with every claim addition.

### E — Postgres-only, no IdP

* **Good** — zero external dependencies.
* **Bad** — we become the identity provider permanently; every customer's "log in with Google" ask becomes new code.
* **Bad** — passkey UX, MFA, breach monitoring, compliance attestation all become our roadmap items.

## More Information

### Three-stage staging

| Stage | Who we serve | Identity source | Data layer | Code delta from prior stage |
|---|---|---|---|---|
| **Stage 0 — today** | one operator, one machine | `IdentityContext(user_id="default", tenant_id="default")` from init; auth-off | file-based, tenant-prefixed (`projects/{tenant_id}/`) | nothing — [`0003-spend-and-tenancy.md`](0003-spend-and-tenancy.md) lands the `IdentityContext` reification |
| **Stage 1 — small SaaS / casual multi-user** | dozens to thousands of end-users | **OIDC client** to Google / Apple / GitHub / Microsoft. We never store passwords, never run a passkey ceremony — we federate to providers whose passkey UX already works. `(provider, subject) → user_id` mapping is the only thing we persist. | same file-based layout, tenant-prefixed for real | one OIDC client module (~200 LoC) + one dep (`cryptography` for JWT signature verify) |
| **Stage 2 — Facebook / Netflix-shape** | millions+; B2B SSO; enterprise compliance | **Become OIDC provider** — front Ory Hydra / Zitadel / Authentik / Keycloak (open-source IdPs publicly run by enterprises) as a sibling process; we own the issuer URL. | PostgreSQL + RLS; the file-based stage migrates behind a clean store adapter | swap the issuer; data layer migrates via the adapter — no application-code rewrite |

### Contract A — OIDC (wire / identity ingress)

Every request that crosses a trust boundary into the application carries an **OIDC-shape identity claim**. Internally, the seam below the boundary takes an `IdentityContext`, not a token.

- **ID Token shape** — `iss` (issuer URL), `sub` (subject — stable per-user id within the issuer), `aud` (audience — our application's client id), `exp` / `iat`, plus a custom `tenant_id` claim. Verify signature against the issuer's JWKS (`/.well-known/jwks.json`) — keys rotate, the discovery endpoint is the source of truth.
- **Discovery** — `{issuer}/.well-known/openid-configuration` provides authorization / token / JWKS / userinfo endpoints; the client treats this URL as the only piece of provider config it hardcodes.
- **Tokens never enter app code.** They're verified at the boundary (`presentation/api/middleware/`); past the boundary, the codebase sees `IdentityContext` only. PR rule: a JWT type appearing outside `presentation/api/middleware/` and `infrastructure/identity/` is a block.
- **Session pattern** — first-party browser sessions are **server-side sessions keyed by HttpOnly cookie**, per [The Copenhagen Book](https://thecopenhagenbook.com/sessions) (Lucia Auth's author deprecated his library in favor of these patterns; framework-agnostic; OWASP-aligned). **ID Tokens are only for cross-trust-boundary** (callback from the provider, B2B SSO, service-to-service). The "stuff a JWT in a cookie" anti-pattern is an explicit gate violation.
- **Code anchor (Stage 1+):** `promptpotter/infrastructure/identity/` (new) — `OIDCClient`, `IdTokenVerifier`, `JWKSCache`. ~200 LoC plus `cryptography` for signature verification.
- **Code anchor (Stage 0, shipped):** `IdentityContext` constructed by `presentation/api/deps.py::resolve_identity` returning the auth-off default; CLI seam constructs it from `args.tenant` via `presentation/cli/commands/_shared.py::identity_from_args` and threads it through `init_services_cli(identity=…)`.

### Contract B — PostgreSQL RLS (data / storage isolation)

The tenant boundary is enforced **at the data engine**, not by every `WHERE tenant_id = ?` a developer remembers to write. RLS makes cross-tenant reads structurally impossible, not just conventionally avoided.

- **Today's file-based layout is the degenerate case.** `projects/{tenant_id}/` is a one-tenant-per-directory isolation primitive enforced by the OS. The contract shape — "every storage operation is scoped to `IdentityContext.tenant_id`, no exceptions" — is the same.
- **Stage-2 form** — every tenant-scoped table carries a `tenant_id` column; an RLS policy of the form `USING (tenant_id = current_setting('app.tenant_id')::uuid)` is attached; the application sets `SET LOCAL app.tenant_id = …` at the start of each request inside a transaction. The store adapter (below) is the single place this happens.
- **Store adapter** — `infrastructure/store/stores.py::build_stores(identity: IdentityContext, …)` is the single construction route. Stage 0 returns file-backed stores rooted at `projects/{tenant_id}/`. Stage 2 returns Postgres-backed stores that set the RLS session variable. **No application caller knows which.** The RLS adapter is a swap, not a rewrite.
- **Cross-tenant primitives.** `measurements/` is dataset-scoped + cross-campaign by design (per `docs/architecture.md` §0), as is its peer `optimizer_reuse/` — both are keyed by content hash, which is what makes them shareable at all. They stay cross-tenant within a single install; cross-*install* sharing is an explicit non-goal (per `../specs/roadmap.md`). The RLS policy on the measurement tables omits the tenant filter; install-level isolation comes from running separate databases per install.

### Data model — SCIM 2.0 Core + EnterpriseUser

The internal `User` / `Group` shape is **SCIM 2.0 Core + EnterpriseUser** ([RFC 7643](https://datatracker.ietf.org/doc/html/rfc7643)). Every workforce IdP — Microsoft Entra, Okta, Google Workspace, Auth0, OneLogin, Ping — already produces and consumes SCIM resources; the schema is JSON Schema, vendor-portable, and a decade mature ([WorkOS 2026 SCIM provider survey](https://workos.com/blog/scim-providers)). Adopting it now means our user records speak the same vocabulary every enterprise integration will ask for, with zero translation layer.

#### SCIM ↔ OIDC field mapping

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

#### Tenant claim normalization — `org_id`

`org_id` is the canonical internal field name for the tenant claim. No RFC blesses one name; `org_id` is winning the B2B SaaS literature battle (Auth0 Organizations emits it natively; WorkOS, Frontegg, PropelAuth, Stytch follow). Normalize at the edge:

- Auth0 Organizations → native `org_id` claim → passthrough.
- Microsoft Entra → `tid` claim → rename to `org_id` in the verifier.
- Custom IdP / Stage-2 own-issuer → emit `org_id` directly.

`IdentityContext.tenant_id` is populated from `org_id`; any serialized form of the context (logs, ledger events, audit trail) uses the field name `org_id`. **One internal name, one external claim name, one mapping rule per IdP.**

#### Schema.org `Person` JSON-LD — output projection only

[Schema.org `Person`](https://schema.org/Person) ([`schemaorg/schemaorg`](https://github.com/schemaorg/schemaorg)) is emitted as JSON-LD on marketing pages and public profile surfaces for SEO. **It is an output projection, not the internal model.** Its 70+ Person properties include noise the application has no use for (`spouse`, `netWorth`, `height`, `colleague`). The application stores SCIM; the public-facing renderer projects a small subset of SCIM fields into Schema.org JSON-LD at response time.

### `IdentityContext` — the identity carrier

`IdentityContext` (`promptpotter/shared/identity.py`) describes *who is making the call*; its `tenant_id` field describes *which slice of storage* a call uses. The two prior concerns (the deleted `TenantContext` carried only the slice) collapse into one frozen dataclass.

```python
@dataclass(frozen=True)
class IdentityContext:
    user_id: UserId                  # NewType[str]; stable per (issuer, subject)
    tenant_id: TenantId              # NewType[str]; storage scope
    issuer: Issuer | None            # NewType[str]; None in Stage-0 auth-off
    claims: Mapping[str, object]     # raw verified ID-Token claims; empty in Stage-0
    capabilities: frozenset[str]     # flat capability set; RBAC is post-M13
```

- **Stage 0** — `IdentityContext(user_id=UserId("default"), tenant_id=TenantId("default"), issuer=None, claims={}, capabilities=frozenset())`. Constructed once at init. The single-operator path is the auth-off branch — one branch, not a feature flag.
- **Stage 1** — constructed by the OIDC middleware from a verified ID Token. `user_id = f"{issuer}:{sub}"`, `tenant_id` from the custom `tenant_id` claim (provider-set for B2B, install-scoped fallback for casual users), `issuer` from `iss`.
- **`TenantContext` collapsed into `IdentityContext`** (shipped). `Session.identity: IdentityContext` (`application/initialization/session.py`) replaces the deleted `Session.tenant`. The spend seam — and every consumer — takes `IdentityContext`, never bare `tenant_id` or bare `TenantContext`. Behavior change, no shim.

### No-drift gates

Enforceable rules. A PR violating any of these is a block. The **(test)** marker names the *kind* of check that would catch each gate — but none are standing tests today (the structural/contract suite was cut to the silent-harm core, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)); they are enforced by the typed seam + review, and most fail loud.

1. **Every API request resolves an `IdentityContext`.** No router accepts an unauthenticated request outside the auth-off boundary. **(test)** — middleware coverage check.
2. **No JWT in first-party cookies.** Session cookie is an opaque server-side session id, period. ID Tokens cross trust boundaries only. **(test)** — grep for JWT types in `presentation/` outside the middleware path.
3. **No `tenant_id: str` parameter past the seam.** Every store / query call takes `IdentityContext`; `tenant_id` is a `TenantId` newtype derived from it. **(test)** — `build_stores` signature + caller scan (typed seam + review; no standing test).
4. **`IdentityContext.tenant_id` is the only source of tenant scope.** No `request.headers["X-Tenant"]`, no `args.tenant` past the resolver. The seam derives, downstream consumes. **(test)** — invariant on the resolver call sites.
5. **Adding a new identity-bearing ingress amends §0 first.** Per CLAUDE.md Pre-flight gate Q4 sub-rule. Stage 1 (OIDC ingress on the API) and Stage 2 (B2B SSO ingress, eventual SAML/SCIM) each require an §0 note.
6. **Internal `User` / `Group` shape uses SCIM 2.0 field names verbatim.** No custom field invention that diverges from SCIM Core or EnterpriseUser. New fields land in the SCIM extension namespace (`urn:ietf:params:scim:schemas:extension:<name>:2.0:User`). **(test)** — invariant on the User dataclass field set.

### Minimal-deps invariant

- **Stage 0 — zero new deps.** Stdlib only. `IdentityContext` (`shared/identity.py`) + four newtypes (`domain/identity.py`) ship without any library.
- **Stage 1 — one new Python dep: `cryptography`** for JWT/JWS signature verification against JWKS. Everything else (HTTP discovery fetch, session cookies, opaque token generation, PKCE) is stdlib. The OIDC client is ~200 LoC we write ourselves.
- **Stage 2 — zero new Python deps.** Open-source IdPs (Ory / Zitadel / Keycloak / Authentik) are **sibling processes** — we call them over HTTP/OIDC, we do not import a Python auth library. The PostgreSQL adapter rides our existing storage abstractions plus the `psycopg` binding we'd already need for any DB store.
- **Never** add a Python auth library (no `python-jose`, no `authlib`, no `python-social-auth`, no `flask-login`-shape framework). Either we implement OIDC client ourselves (Stage 1) or we call out to a sibling IdP (Stage 2).
- Reference for zero-dep patterns: [The Copenhagen Book](https://thecopenhagenbook.com/). Framework-agnostic, OWASP-aligned, by Lucia Auth's author after he deprecated his library in favor of the patterns themselves.
- **Schema vendoring (not a Python dep).** Vendor [`bjmc/scim-jsonschema`](https://github.com/bjmc/scim-jsonschema) as a git submodule at `vendor/schemas/scim/`, tag-pinned. JSON Schema files for `User`, `Group`, `EnterpriseUser` plus RFC 7643 normative text live in our tree, version-pinned. **No `pip install`, no runtime cost** — schema vendor, not a library import. Schema.org JSON-LD context copies from [`schemaorg/schemaorg`](https://github.com/schemaorg/schemaorg) on demand for the public-facing projection only (submodule optional; copy-and-pin fine).

### Future-swap-target table (Stage 2 IdPs)

Pre-vetted swap targets when we want to delegate the **provider** role (i.e. become an OIDC provider ourselves by fronting one). The OIDC wire makes the swap trivial — middleware re-targets, application unchanged. **Not Day-1 dependencies.** Adopt one at Stage 2 if/when needed.

| IdP | Lang / shape | Publicly cited users | Notes |
|---|---|---|---|
| **[Ory Hydra + Kratos](https://www.ory.sh/)** | Go binaries; HTTP API | T-Mobile, OVHcloud, Padelmania ([case studies](https://www.ory.sh/case-studies)) | Apache 2.0. Hydra = OAuth2/OIDC server; Kratos = identity & user management. Composable. |
| **[Zitadel](https://zitadel.com/)** | Go, single-binary | [Public customer list](https://zitadel.com/customers) | Apache 2.0. Multi-tenant from day one — the others tack it on. |
| **[Keycloak](https://www.keycloak.org/)** | Java / Quarkus | Red Hat ecosystem; CNCF graduated; widely deployed in enterprise (Hitachi, Cisco internal references) | Apache 2.0. Heaviest, most feature-complete. SAML + OIDC + LDAP federation. |
| **[Authentik](https://goauthentik.io/)** | Python, single-binary | Self-hosters; growing enterprise adoption | MIT. Matches our stack but we still call it over HTTP — no Python import. |

Selection criterion when the time comes: multi-tenancy story + operational fit. **Zitadel** is the front-runner under today's information.

### Authorization swap-target table

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

### Stage 1 implementation (shipped)

Stage 1 OIDC sign-up landed at `promptpotter/infrastructure/identity/` and `promptpotter/presentation/api/middleware/oidc.py`. The package splits per provider (`google.py`, `github.py`) on top of shared infrastructure (`verifier.py`, `jwks.py`, `session.py`, `bundle.py`, `provider_config.py`, `allowlist.py`, `migration.py`, `paths.py`, `user.py`); `cryptography` is the only new Python dep per the minimal-deps invariant. The middleware at `presentation/api/middleware/oidc.py` verifies the inbound ID Token against the issuer's JWKS, populates `IdentityContext`, and ensures tokens never appear past the boundary (gate #2 — review-enforced; no standing test). `presentation/api/deps.py::resolve_identity` reads the verified context from the session-cookie store; Stage 0 (auth-off) substitutes `default_identity()`. Auto-mint at first sign-in is one-tenant-per-user (`tenant_id = UserId`), encoded by `infrastructure/identity/user.py::derive_user_id`. Sign-up surface lives at `webapp/app/login/page.tsx` over `/auth/login/{provider}` → `/auth/callback/{provider}`.

### Anchors

Every claim in this ADR names a file. Drift is caught by review against the typed seam (no standing test — the structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)); a stale path here fails loud as a broken link.

| Concern | File |
|---|---|
| `IdentityContext` (shipped, Stage 0+) | `promptpotter/shared/identity.py` |
| `TenantId` / `UserId` / `Issuer` / `SafeName` newtypes + `safe_name` validator (shipped, Stage 0+) | `promptpotter/domain/identity.py` |
| `Session.identity` field (shipped, Stage 0+ — replaces deleted `Session.tenant: TenantContext | None`) | `promptpotter/application/initialization/session.py` |
| Identity resolver (shipped, Stage 0 auth-off + Stage 1 OIDC) | `promptpotter/presentation/api/deps.py` |
| CLI seam (shipped, Stage 0+) | `promptpotter/presentation/cli/commands/_shared.py` |
| Store construction (shipped, Stage 0+) | `promptpotter/infrastructure/store/stores.py` |
| OIDC client package (shipped, Stage 1) | `promptpotter/infrastructure/identity/` |
| OIDC middleware (shipped, Stage 1) | `promptpotter/presentation/api/middleware/oidc.py` |
| Auth router — providers / login / callback / logout / me / quota / activity (shipped, Stage 1) | `promptpotter/presentation/api/routers/auth.py` |
| Login page (shipped, Stage 1) | `webapp/app/login/page.tsx` |
| RLS adapter (Stage 2 — not yet on disk) | `promptpotter/infrastructure/store/` |
| §0 I/O kind amendment (shipped, Stage 1) | `docs/architecture.md` |

### §0 amendment

**Yes.** Identity-foundation introduces a new I/O ingress — the OIDC verification step at the API boundary — that doesn't fit any existing kind. Today's §0 names Persistence / Display / Control-local / Control-remote (the fourth landed with [`0001-m12-control-plane.md`](0001-m12-control-plane.md)). **`Identity` is a fifth distinct kind** because:

- It mutates no campaign state (rules out Persistence).
- It's not a ledger subscriber (rules out Display).
- It doesn't signal the loop (rules out Control-local / -remote).
- It's the *gate* that establishes who the subsequent Control-remote call is *from*.

The amendment lands as a docs-only PR before any Stage-1 code. CLAUDE.md Pre-flight gate Q4 sub-rule applies — the gate blocks code introducing a new I/O kind without §0 backing.

Stage-0 work (the `IdentityContext` seam — shipped) does **not** require the amendment — it's a refinement of the prior tenant-context seam, not a new ingress. The amendment lands with Stage 1.

### Out of scope (forever, or for other documents)

- **Feature surface** — login screens, settings UI, profile pages, account-merge flows. Those land in [`../specs/roadmap.md`](../specs/roadmap.md) and live below this foundation.
- **Provider choice for Stage 2** — Ory vs Zitadel vs Keycloak vs Authentik. The table above is pre-vetting; the decision is made when Stage 2 is imminent.
- **Implementation timing.** This ADR doesn't schedule Stage 1 or Stage 2. The cluster's milestone specs (M12 / M13) own scheduling.
- **OAuth 2.1 / third-party access tokens for our API.** Stage 2+. First-party use case (a user signs into our webapp) doesn't need it.
- **SCIM provisioning.** Enterprise-tier — Stage 2+ at earliest, defer until requested.
- **SAML.** Enterprise B2B SSO — Stage 2+. Delegated to whichever IdP we front (Keycloak / Ory / Zitadel speak it; we don't implement SAML ourselves).
- **WebAuthn / passkey *provider* mode.** Stage 2 or later. Stage 1 leverages Google / Apple / Microsoft's passkey implementations via OIDC federation — we get passkey UX for free without owning the ceremony.
- **RBAC beyond a flat `frozenset[str]`** — `IdentityContext.capabilities` exists but stays empty until a real authorization model is needed.
- **Billing / quotas / per-tenant rate limiting.** Post-M13.

### Cross-refs

- [`0003-spend-and-tenancy.md`](0003-spend-and-tenancy.md) — **first consumer.** Lands the `IdentityContext` reification at Stage 0; spend tracking is the payload demonstrating the seam works end-to-end.
- [`0001-m12-control-plane.md`](0001-m12-control-plane.md) — **second consumer.** Stage 1 OIDC client lands here; `JobRegistry` scopes on `IdentityContext`; auth-off mode is the Stage-0 fallback.
- [`../specs/roadmap.md`](../specs/roadmap.md) — **third consumer.** Install / User / Project nouns map onto OIDC claims (`Install = iss`, `User = sub`, project scoping rides `tenant_id` claim). Stage 2 considered when self-hosters demand native identity.
- [`../specs/roadmap.md`](../specs/roadmap.md) — convergence: identity-collapse touches the same store-seam files; sequence Phase 1 before the Stage-0 `IdentityContext` reification to avoid touching `index.json` writers twice.
- [`0004-operator-admin-channels.md`](0004-operator-admin-channels.md) — **administrative-write facet.** How privileged identity/deployment mutations (blocklist edits) are delivered in-zone, outbound-only, without exposing an inbound route.
- [`../architecture.md`](../architecture.md) §0 — `Identity` I/O kind amendment lands here at Stage 1.
