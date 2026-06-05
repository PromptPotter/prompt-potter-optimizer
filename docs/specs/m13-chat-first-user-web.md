# M13: Chat-First Multi-User Web

**Status:** partially shipped — ingest Slice 1 (CSV, `DraftCampaign`, `POST /datasets/ingest`, `mint-campaign-from-draft`) shipped under the M10 lane. **Casual sign-in (OIDC Stage 1 client) is shipped** — Google + GitHub code exchange, allowlist, server-side sessions, `/auth/*` (see [`ADR-0002`](../adr/0002-identity-foundation.md) and roadmap **Already shipped**). **The control-plane endpoints the chat write-path reuses are shipped** ([`ADR-0001`](../adr/0001-m12-control-plane.md)), so the chat surface (steps 4–6) is now **unblocked, not gated** — the `ChatPane` is still an inert placeholder awaiting wiring. Install/User/Project product nouns as webapp-first-class and the cross-user measurement panel remain spec only. Replaces the abandoned `m12-multi-tenant-session-skeleton.md` (sidebar-tree shape mis-targeted product direction). Parent: [`m12-multi-connector.md`](m12-multi-connector.md) (Track 3 webapp Phase 2 unblocks this).

**Depends on:** [`ADR-0002 identity-foundation`](../adr/0002-identity-foundation.md) Stages 1 + 2 — Stage 1 (OIDC client federating to Google / Apple / GitHub) covers casual auth; Stage 2 (becoming an OIDC provider by fronting Zitadel / Ory / Authentik / Keycloak) is considered when self-hosters demand native identity that doesn't depend on third-party providers.

## What this covers

End-state product surface: claude.ai-shape. One admin self-hosts the install; end-users sign up casually over the web; they share one install's accumulated data. Today's file-tree sidebar is the developer's surface, not the product's.

## The four nouns ↔ OIDC claims

The nouns are product-language; the substrate is OIDC (per identity-foundation Contract A). Mapping the nouns onto OIDC claims now means the identity model survives Stage 1 (federated) → Stage 2 (we become the issuer) without renaming anything.

- **Install** — one administrator. Hosts PromptPotter. Brands it (whitelabel slot at `projects/{install_id}/tenant.json`). Onboards web users. **OIDC mapping:** `Install` = the `iss` (issuer) URL of our future Stage-2 provider; in Stage 1 every install federates to upstream IdPs and `install_id` is install-local (the operator's own choice at deploy time).
- **User** — signs up over the web, casual auth. 1–2 connectors typically. Owns N projects. **OIDC mapping:** `User` = `sub` (subject) within the install's issuer; `user_id = f"{issuer}:{sub}"` per identity-foundation's `IdentityContext`. Stage 1: `sub` comes from Google / Apple / GitHub. Stage 2: from our own issuer. Each `User` record is a SCIM 2.0 Core resource (see [`ADR-0002 § Data model`](../adr/0002-identity-foundation.md#data-model--scim-20-core--enterpriseuser)) carrying the `Install`-scoped `org_id`; user records on disk use SCIM field names verbatim — no custom-named columns.
- **Project** — the three-drop unit: dataset + `context.md` (task framing) + `pipeline.json`. Today's `datasets/{name}/` is exactly this — just not surfaced as a project. **OIDC mapping:** project scope rides the custom `tenant_id` claim — one user's projects share a `tenant_id`; install-level cross-user measurement sharing happens at the dataset-scoped `archive/`, not in claims.
- **Campaign** — one optimization run inside a project. Multiple per project; user can compare them. Maps 1:1 to today's cycle.

## The chat surface

Chat is the **constant control surface**, not an onboarding wizard. Through it the user drops a project, configures a campaign (chat negotiates `campaign.json`), interrupts mid-cycle, steers mid-cycle, queries results, asks the optimizer about its own state. The dashboard survives as the **live-view companion** to chat — chat is where you talk to it, dashboard is where you watch it work.

## Ingest

The drop-three-things upload (dataset + framing + pipeline) is the user's entry into a Project. **Slice 1 = CSV-only ingest** — two required columns `query,ground_truth`. Later slices: XLSX (first sheet only), Parquet, JSON-lines.

### The committed artifact is an Origin

On commit the ingest produces an **Origin** in the tenant's collection — not just "a dataset". Origin is the existing PromptPotter domain word: per [`architecture.md §0`](../architecture.md), `cycle_{target_hash[:12]}` is content-addressed from `JobSearchPoint.content_hash(dataset)`, which is the rendered target prompt + dataset + target `pipeline_params`. The four committed files at `projects/{tenant}/datasets/{slug}/` compose into exactly that hash:

| File | Origin component |
|---|---|
| `cache.json` | dataset rows (the sample bank) |
| `pipeline.json` | target `pipeline_params` (initial `nodes.*.config` overlay) |
| `task_description.md` | rendered target prompt's framing |
| `prompts/default.json` | rendered target prompt template |

The co-located `campaign.json` is **not** part of the Origin — it is the default campaign config that ships alongside (connector, scoring composite, max-rounds, optimizer-prompt hash). Two tenants who ingest structurally-identical Origins produce identical `cycle_{target_hash[:12]}` ids by construction.

### Collection — `GET /datasets`

A tenant's Origins are listed under `projects/{tenant}/datasets/` and exposed via the existing identity-scoped `GET /datasets`. Each returned entry carries a `tier` field:

- `tier: "yours"` — user-owned Origins under `projects/{tenant}/datasets/{slug}/`. Always visible to the owning identity.
- `tier: "benchmark"` — repo-root `datasets/` (today's `aime_2025`, `bbeh`, `gsm8k`, `hotpotqa`, `justlogic`, `lca-termnorm`, `promptpotter-self`, `_optimizer`). Visible only when the identity holds the capability `datasets.benchmarks.read`.

Stage-0 capability grant: env var `PROMPTPOTTER_ADMIN=1` consumed by `default_identity()` (`shared/identity.py`) augments the default identity's capability set with `datasets.benchmarks.read`. Stage-1 OIDC grants the capability per-claim. The list endpoint is one read path; the surface is a flat list with a `tier` discriminator, not a two-section UI.

### Dashboard "New campaign" entry

The Dashboard's **New campaign** button is a two-mode entry against `GET /datasets`:

- **Empty collection** (no `tier: "yours"` entries) → routes into the chat-ingest flow below. This is the onboarding ritual that produces the first Origin.
- **Non-empty collection** → renders the collection as a list; the operator picks an Origin, then mints a campaign against it (the standard mint-campaign path, post-Origin). List-then-mint UX.

After onboarding, campaign launches are always list-then-mint against the existing collection. The chat-ingest flow is reachable from the list as a secondary "Add an Origin" action.

### Draft-campaign object

The server holds a canonical **draft-campaign** per ingest, mutated by both chat and the parameter panel until the operator commits. The name stays `DraftCampaign` (not `DraftOrigin`) because the chat surface negotiates BOTH the Origin components (slug, task_description, pipeline_overlay) AND the default campaign config (connector, scoring_composite, max_rounds). On commit, the Origin-shaped subset becomes the four content-hashed files; the campaign-config subset becomes `campaign.json`. Lifecycle:

1. **Created** on file drop (CSV uploaded → parsed → preview returned).
2. **Mutated** by chat (assistant proposes edits via the existing `POST /commands/{kind}` highway) or by direct panel edits (operator clicks **Apply** in the panel; the panel-edit command rides the same highway).
3. **Committed** on explicit operator action (chat "mint" verb or panel "Create campaign" button) — server writes the dataset dir + mints the campaign.
4. **Discarded** by TTL or explicit discard.

Shape (`DraftCampaign`):

| Field | Source | Lands as |
|---|---|---|
| `draft_id` | server-minted ULID | (not committed) |
| `tenant_id` | from `IdentityContext` | dir prefix |
| `slug` | `SafeName(filename_without_ext)` initially; operator-editable | dir name |
| `sample_preview` | first 10 parsed rows (read-only after ingest; full set persisted alongside) | `cache.json` (full set) |
| `n_samples` | parsed row count | (derived from `cache.json`) |
| `connector` | default `termnorm` (smart-default; operator-editable) | `campaign.json` |
| `scoring_composite` | default `exact_match` (smart-default; operator-editable) | `campaign.json` |
| `max_rounds` | default `5` (smart-default; operator-editable) | `campaign.json` |
| `task_description` | default empty string; chat negotiates content | `task_description.md` + `prompts/default.json` |
| `pipeline_overlay` | default `{}` (empty `nodes.*.config` overlay) | `pipeline.json` |
| `created_at`, `updated_at` | server timestamps | (not committed) |

Smart-default rationale: connector `termnorm` is the only registered connector today (per root CLAUDE.md); `exact_match` is the only universally-applicable scorer for `(query, ground_truth)` shape; `max_rounds=5` matches the M10 prompt-iteration framework default. Per-dataset model + `reasoning_effort` defaults are sourced from [`docs/operations/dataset-reasoning-matrix.md`](../operations/dataset-reasoning-matrix.md) at commit time — the draft does not pin model identity (the matrix is the source of truth).

### State binding

The chat and panel are **two views over the same `DraftCampaign`**. Mutations propagate via:

- **Panel → server → chat:** operator edits a field, clicks **Apply** → `POST /commands/edit-draft-campaign` (M13 wire addition, declared in `m12-api-openapi.yaml` separately from the slice 1 spec) → server updates the draft → SSE `DraftUpdatedRecord` reaches the chat surface so the assistant sees current state on its next turn.
- **Chat → server → panel:** assistant proposes edits via tool-call → same `POST /commands/edit-draft-campaign` → SSE `DraftUpdatedRecord` → panel re-renders.

Canonical state lives server-side. Neither surface holds the source of truth; both are reflections. The "Apply" button in the panel is the **explicit commit point for panel-local edits** — the panel is allowed to be temporarily out of sync with the server until Apply fires, so the operator can compose multi-field edits without round-tripping.

### `POST /datasets/ingest`

Slice 1's one new endpoint. Workspace-scoped mutation (creates a draft, not yet a dataset).

- **Request:** `multipart/form-data` with `file` (CSV blob) + optional `slug` (defaults to `SafeName(filename_without_ext)`).
- **200:** `DraftCampaign` JSON (shape above) + `sample_preview` (first 10 rows).
- **401:** unauthenticated (no `IdentityContext`).
- **409:** slug collision (`error: "slug_collision"`) — `details.slug` (the colliding name) + `details.suggested_slug` (smallest free `{slug}-{n}`). The chat turns this into an in-flow choice (use existing / save as new / replace); dataset-identity + the version-and-repoint Replace contract live in [`m13-dataset-bridge.md`](m13-dataset-bridge.md).
- **422:** parse failure (CSV malformed, missing required columns `query` / `ground_truth`, zero rows, …) — `details.reason` carries the specific failure.

Slug derivation: `SafeName` on the filename's basename minus extension; collisions resolved by operator picking the suggested `{slug}-{n}` or editing.

### Commit path

On operator commit (chat verb or panel button → `POST /commands/mint-campaign-from-draft`, declared in `m12-api-openapi.yaml`), the server writes — atomically, single-writer per `architecture.md §0` — to `projects/{tenant}/datasets/{slug}/`. The four Origin files compose into `JobSearchPoint.content_hash(dataset)`; `campaign.json` is the sibling default-config:

| File | Content | Origin? |
|---|---|---|
| `cache.json` | full parsed sample bank (the dataset rows) | yes |
| `pipeline.json` | initial overlay (empty `nodes.*.config` if operator did not override) | yes |
| `task_description.md` | chat-negotiated framing | yes |
| `prompts/default.json` | rendered target prompt template | yes |
| `campaign.json` | default-campaign config — `connector`, `scoring_composite`, `max_rounds`, `root_content_hash`, `optimizer_prompt_hash` | no (sibling) |

After commit, the standard mint-campaign path runs against the new Origin slug. The frontend chat+panel surface supersedes the earlier placeholder modal (now shipped as `components/ingest/IngestPane.tsx`).

**Open:** SSE event name (`DraftUpdatedRecord`) needs declaration in `m12-events-asyncapi.yaml` before a handler lands — out of slice 1 scope but on-deck for the wire-up PR.

First-run illustration: the chat pane's empty-state thread is a static mock (chip text at `webapp/components/chat/ChatPane.tsx`), so no checked-in CSV is needed to render it. Live ingest is exercised against the surviving `email-tagging` demo origin via `PROMPTPOTTER_AUTH=off` (the cheap authed-and-live harness — see [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § Testing posture), not a drag-drop fixture.

## Cross-user data leverage

Already works at the data layer: `archive/measurements/{content_hash}/` is content-addressed on `JobSearchPoint.content_hash(dataset)`. Two tenants who ingest structurally-identical Origins (same dataset rows + same target prompt + same `pipeline_params`) hash-collide into the same archive entries by construction — cross-tenant evidence pooling for free, no per-tenant duplication. What's missing is **surface** — the chat / project view doesn't show "this query was measured 14× by other users on this install." One read panel; no new persistence.

## Non-goals

Cross-install sharing (install is a hard isolation boundary; the RLS adapter from identity-foundation Stage 2 enforces this at the DB level via separate databases per install) · billing/quotas · project sharing UI between users (measurements share; configs don't) · the chat LLM's persona / system prompt (its own design pass) · **owning the password / passkey ceremony at Stage 1** (we federate to providers whose UX already works — per identity-foundation, Stage 1 leverages Google/Apple passkeys via OIDC, Stage 2 considered for native).

## Sequencing (not scheduled)

1. **Identity-foundation Stage 1 — SHIPPED.** OIDC client + middleware live (`middleware/oidc.py`, mounted `main.py:89`); Google + GitHub federation; `IdentityContext` carries the verified claims. **§0 `Identity` I/O kind amendment lands/landed with this step.** *(Stage 0.5 caveat: RLS / SCIM data isolation not yet enforced.)*
2. Project as first-class noun on disk (`projects/{tenant}/datasets/{slug}/` — `tenant_id` already collapses install + user per ADR-0002 no-drift gate #3; no extra `users/{uid}/projects/{pid}/` nesting). Identity-scoped per `IdentityContext`.
3. Webapp project view (drop-three-things upload; campaign comparison rides existing per-cycle data).
4. Chat shell, read-only (query optimizer state).
5. Chat write-path (steer / interrupt / fork) — **unblocked: the M12 control-plane endpoints it reuses are shipped** ([`ADR-0001`](../adr/0001-m12-control-plane.md)). Remaining work is wiring `ChatPane` to those verbs + querying state via `/api/v1/sessions/active/live-state` (∴ after state-sync P3).
6. Cross-user measurement panel.
7. **Identity-foundation Stage 2** — considered when self-hosters demand native identity (no third-party dependency) and/or B2B SSO / SCIM. Front Zitadel / Ory / Keycloak / Authentik as a sibling process; the OIDC client we already wrote re-targets to our own issuer URL. No application-code rewrite.

Steps 1–3 = minimum to be a product. Steps 4–6 = the differentiator. Step 7 = optional, demand-driven.

## Pre-flight notes

`Identity` I/O kind (per identity-foundation §0 amendment, lands with Stage 1 in M12) · new on-disk concept (`users/` + `projects/`) · `Install` distinct from `Tenant` · `project_*` collides with operator memory tree — on-disk dir name candidate `bench/{pid}/` or `work/{pid}/`, decide before code · all identity uses `IdentityContext` (per identity-foundation no-drift gate #3 — no bare `tenant_id`).
