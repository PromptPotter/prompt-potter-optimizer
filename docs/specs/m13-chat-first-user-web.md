# M13: Chat-First Multi-User Web

**Status:** spec only — no code. Replaces the abandoned `m12-multi-tenant-session-skeleton.md` (sidebar-tree shape mis-targeted product direction). Parent: [`m12-multi-connector.md`](m12-multi-connector.md) (Track 3 webapp Phase 2 unblocks this).

**Depends on:** [`identity-foundation.md`](identity-foundation.md) Stages 1 + 2 — Stage 1 (OIDC client federating to Google / Apple / GitHub) covers casual auth; Stage 2 (becoming an OIDC provider by fronting Zitadel / Ory / Authentik / Keycloak) is considered when self-hosters demand native identity that doesn't depend on third-party providers.

## What this covers

End-state product surface: claude.ai-shape. One admin self-hosts the install; end-users sign up casually over the web; they share one install's accumulated data. Today's file-tree sidebar is the developer's surface, not the product's.

## The four nouns ↔ OIDC claims

The nouns are product-language; the substrate is OIDC (per identity-foundation Contract A). Mapping the nouns onto OIDC claims now means the identity model survives Stage 1 (federated) → Stage 2 (we become the issuer) without renaming anything.

- **Install** — one administrator. Hosts PromptPotter. Brands it (whitelabel slot at `projects/{install_id}/tenant.json`). Onboards web users. **OIDC mapping:** `Install` = the `iss` (issuer) URL of our future Stage-2 provider; in Stage 1 every install federates to upstream IdPs and `install_id` is install-local (the operator's own choice at deploy time).
- **User** — signs up over the web, casual auth. 1–2 connectors typically. Owns N projects. **OIDC mapping:** `User` = `sub` (subject) within the install's issuer; `user_id = f"{issuer}:{sub}"` per identity-foundation's `IdentityContext`. Stage 1: `sub` comes from Google / Apple / GitHub. Stage 2: from our own issuer. Each `User` record is a SCIM 2.0 Core resource (see [`identity-foundation.md` § Data model](identity-foundation.md#data-model--scim-20-core--enterpriseuser)) carrying the `Install`-scoped `org_id`; user records on disk use SCIM field names verbatim — no custom-named columns.
- **Project** — the three-drop unit: dataset + `context.md` (task framing) + `pipeline.json`. Today's `datasets/{name}/` is exactly this — just not surfaced as a project. **OIDC mapping:** project scope rides the custom `tenant_id` claim — one user's projects share a `tenant_id`; install-level cross-user measurement sharing happens at the dataset-scoped `archive/`, not in claims.
- **Campaign** — one optimization run inside a project. Multiple per project; user can compare them. Maps 1:1 to today's cycle.

## The chat surface

Chat is the **constant control surface**, not an onboarding wizard. Through it the user drops a project, configures a campaign (chat negotiates `campaign.json`), interrupts mid-cycle, steers mid-cycle, queries results, asks the optimizer about its own state. The dashboard survives as the **live-view companion** to chat — chat is where you talk to it, dashboard is where you watch it work.

## Cross-user data leverage

Already works at the data layer: `archive/measurements/{content_hash}/` is content-addressed on `JobSearchPoint.content_hash(dataset)`. What's missing is **surface** — the chat / project view doesn't show "this query was measured 14× by other users on this install." One read panel; no new persistence.

## Non-goals

Cross-install sharing (install is a hard isolation boundary; the RLS adapter from identity-foundation Stage 2 enforces this at the DB level via separate databases per install) · billing/quotas · project sharing UI between users (measurements share; configs don't) · the chat LLM's persona / system prompt (its own design pass) · **owning the password / passkey ceremony at Stage 1** (we federate to providers whose UX already works — per identity-foundation, Stage 1 leverages Google/Apple passkeys via OIDC, Stage 2 considered for native).

## Sequencing (not scheduled)

1. **Identity-foundation Stage 1** lands first (OIDC client + middleware in M12). `Install` / `User` are real, federated to upstream IdPs. `IdentityContext` carries the verified claims. **§0 `Identity` I/O kind amendment lands in this step.**
2. Project as first-class noun on disk (`projects/{install}/users/{uid}/projects/{pid}/`; `datasets/{name}/` migrates here). Identity-scoped per `IdentityContext`.
3. Webapp project view (drop-three-things upload; campaign comparison rides existing per-cycle data).
4. Chat shell, read-only (query optimizer state).
5. Chat write-path (steer / interrupt / fork; reuses M12 Track 3 control-plane endpoints).
6. Cross-user measurement panel.
7. **Identity-foundation Stage 2** — considered when self-hosters demand native identity (no third-party dependency) and/or B2B SSO / SCIM. Front Zitadel / Ory / Keycloak / Authentik as a sibling process; the OIDC client we already wrote re-targets to our own issuer URL. No application-code rewrite.

Steps 1–3 = minimum to be a product. Steps 4–6 = the differentiator. Step 7 = optional, demand-driven.

## Pre-flight notes

`Identity` I/O kind (per identity-foundation §0 amendment, lands with Stage 1 in M12) · new on-disk concept (`users/` + `projects/`) · `Install` distinct from `Tenant` · `project_*` collides with operator memory tree — on-disk dir name candidate `bench/{pid}/` or `work/{pid}/`, decide before code · all identity uses `IdentityContext` (per identity-foundation no-drift gate #3 — no bare `tenant_id`).
