# M12: Control Plane — Multi-User SaaS Hardening

> **Status:** Forward direction — spec only, no shipped code. Depends on M10 operator control loop landing first.

Depends on [`m10-operator-control-loop.md`](m10-operator-control-loop.md) (single-operator write surface this milestone hardens into a SaaS).

## What this covers

M10's mini-milestone gives one operator on one machine a full write surface (launch / stop / resume / fork from webapp, SSE, `Control-remote` I/O kind, in-process `JobRegistry`). M12 turns that into a **hub**: one deployment serving N signed-in users with per-tenant isolation, login, and a whitelabel slot. Control machinery already exists — M12 adds the identity boundary, identity-scoped storage, and the multi-user UI.

Out of scope: distributed / out-of-process workers (post-M13); the multi-connector / competitor / L4 / fitness tracks ([`m12-multi-connector.md`](m12-multi-connector.md)); the single-operator write surface itself.

## Depends on

- [`identity-foundation.md`](identity-foundation.md) **Stages 0 + 1** — the two contracts (OIDC wire + RLS data), the `IdentityContext` shape, the no-drift gates. M12 is the milestone that lights up **Stage 1**: the OIDC client + middleware that flips `IdentityContext`'s source from the Stage-0 auth-off default to verified ID Token claims. **§0 amendment for the new `Identity` I/O kind lands first (docs-only PR), per identity-foundation Q4 sub-rule.**
- [`spend-and-tenancy.md`](spend-and-tenancy.md) — the Stage-0 reification (`IdentityContext` seam, `TenantId` / `UserId` newtypes, unforgeable `projects/{tenant_id}/` prefix). M12 consumes the seam; it does not re-define it.

## Open items

- **OIDC client (Stage 1 of identity-foundation).** Implements `promptpotter/infrastructure/identity/` — `OIDCClient`, `IdTokenVerifier`, `JWKSCache`, server-side session store. ~200 LoC of stdlib + one dep (`cryptography`) for signature verification. Discovery via `/.well-known/openid-configuration`, JWKS cache with rotation, PKCE for the auth code flow. Provider-agnostic by construction (per the OIDC contract); pick a Day-1 IdP (Google / GitHub / Microsoft) as a configuration choice, not a code choice.
- **OIDC middleware** populates the `IdentityContext` defined by `spend-and-tenancy.md`; control + tenant-scoped reads reject unauthenticated; static shell + login route open. Local "MS Word" mode runs auth-**off** (the `IdentityContext` Stage-0 default from identity-foundation). **Session cookies are opaque server-side session ids, not JWTs** (per identity-foundation no-drift gate #2). After verifying the OIDC ID Token, the middleware materializes an `IdentityContext` populated from the SCIM-OIDC field mapping defined in [`identity-foundation.md` § Data model](identity-foundation.md#data-model--scim-20-core--enterpriseuser). Authorization checks read SCIM-named `User.roles` / `User.entitlements` at Stage 1; graduate to OpenFGA tuples at Stage 2 per the same spec's [authorization swap-target table](identity-foundation.md#authorization-swap-target-table).
- **`JobRegistry` identity-scoped.** Jobs carry their `IdentityContext` (the way `spend-and-tenancy.md` threads it through `Stores`). Control routes reject cross-tenant `job_id`; SSE fans only the caller's tenant's jobs. **No bare `tenant_id` parameters** (per identity-foundation no-drift gate #3).
- **Hub mode + whitelabel.** `projects/{install_id}/tenant.json` carries brand (name, logo, palette) the webapp shell reads at load. Cross-user data leverage already works at the data layer (content-addressed `archive/measurements/`); M12 surfaces it.
- **Chat-panel launcher** alongside the configuration form. Form for power users + reproducibility; chat panel for low-friction onboarding (drop dataset → preview → toggle quiet evolution on). Wires to the `checkin` optimizer node.

## Code surface

| Area | Files |
|---|---|
| OIDC client (Stage 1) | `promptpotter/infrastructure/identity/` (new — per identity-foundation) |
| OIDC middleware | `presentation/api/middleware/oidc.py` (new) |
| Job registry | `application/jobs/` (new), consumes `IdentityContext` from the spend-and-tenancy seam |
| Webapp shell | `webapp/app/`, `webapp/lib/workspace.tsx` |
| Chat panel | `webapp/components/dashboard/ChatPane.tsx` |

(Identity seam + store rooting files live in [`identity-foundation.md`](identity-foundation.md) + [`spend-and-tenancy.md`](spend-and-tenancy.md).)

## Cross-refs

[`identity-foundation.md`](identity-foundation.md) (the foundation this milestone lights up Stage 1 of) · [`spend-and-tenancy.md`](spend-and-tenancy.md) (the Stage-0 reification this milestone consumes) · [`m12-multi-connector.md`](m12-multi-connector.md) (prompt-injection Phase 2 detail) · [`state-sync-cleanup.md`](state-sync-cleanup.md) Phase 1 prereq · [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) (end-state product surface; identity-foundation Stage 2 considered when self-hosters demand native identity).
