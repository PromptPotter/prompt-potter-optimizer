# M13: Chat-First Multi-User Web

**Status:** spec only — no code. Replaces the abandoned `m12-multi-tenant-session-skeleton.md` (sidebar-tree shape mis-targeted product direction). Parent: [`m12-multi-connector.md`](m12-multi-connector.md) (Track 3 webapp Phase 2 unblocks this).

## What this covers

End-state product surface: claude.ai-shape. One admin self-hosts the install; end-users sign up casually over the web; they share one install's accumulated data. Today's file-tree sidebar is the developer's surface, not the product's.

## The four nouns

- **Install** — one administrator. Hosts PromptPotter. Brands it (whitelabel slot at `projects/{install_id}/tenant.json` — the on-disk tenant directory survives as the install scope; the *user-facing* concept "tenant" is gone). Onboards web users.
- **User** — signs up over the web, casual auth. 1–2 connectors typically. Owns N projects.
- **Project** — the three-drop unit: dataset + `context.md` (task framing) + `pipeline.json`. Today's `datasets/{name}/` is exactly this — just not surfaced as a project. Per-user; shared visibility for measurements.
- **Campaign** — one optimization run inside a project. Multiple per project; user can compare them. Maps 1:1 to today's cycle.

## The chat surface

Chat is the **constant control surface**, not an onboarding wizard. Through it the user drops a project, configures a campaign (chat negotiates `campaign.json`), interrupts mid-cycle, steers mid-cycle, queries results, asks the optimizer about its own state. The dashboard survives as the **live-view companion** to chat — chat is where you talk to it, dashboard is where you watch it work.

## Cross-user data leverage

Already works at the data layer: `archive/measurements/{content_hash}/` is content-addressed on `JobSearchPoint.content_hash(dataset)`. What's missing is **surface** — the chat / project view doesn't show "this query was measured 14× by other users on this install." One read panel; no new persistence.

## Non-goals

Cross-install sharing (install is a hard isolation boundary) · real authn/authz at scale (M13 ships casual auth — email+password / magic link / one-provider OAuth) · billing/quotas · project sharing UI between users (measurements share; configs don't) · the chat LLM's persona / system prompt (its own design pass).

## Sequencing (not scheduled)

1. Auth + user identity (Control-remote I/O kind; §0 amendment lands first).
2. Project as first-class noun on disk (`projects/{install}/users/{uid}/projects/{pid}/`; `datasets/{name}/` migrates here).
3. Webapp project view (drop-three-things upload; campaign comparison rides existing per-cycle data).
4. Chat shell, read-only (query optimizer state).
5. Chat write-path (steer / interrupt / fork; reuses M12 Track 3 control-plane endpoints).
6. Cross-user measurement panel.

Steps 1–3 = minimum to be a product. Steps 4–6 = the differentiator.

## Pre-flight notes

New §0 I/O kind (Control-remote — depends on M12) · new on-disk concept (`users/` + `projects/`) · `Install` distinct from `Tenant` · `project_*` collides with operator memory tree — on-disk dir name candidate `bench/{pid}/` or `work/{pid}/`, decide before code.
