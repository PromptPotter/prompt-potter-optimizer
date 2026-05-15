# M13: Chat-First Multi-User Web

**Version:** 0.1.0
**Date:** 2026-05-15
**Status:** Spec only — no code in this milestone yet. Replaces the
abandoned `m12-multi-tenant-session-skeleton.md` (sidebar-tree shape
mis-targeted the product direction; see the commit that introduced
this file for the rationale).
**Parent:** `m12-multi-connector.md` (Track 3 webapp Phase 2 unblocks
this).
**Owner:** Webapp product surface.

## Why

The end-state surface is **claude.ai-shape**: one admin self-hosts the
install; end-users sign up casually over the web; they share one
install's accumulated data. The current dashboard shape (file-tree
view of sessions and cycles) is the developer's surface, not the
product's.

The directional shift, summarised:

| Concept            | Today (developer)                 | M13 (product)                          |
|--------------------|-----------------------------------|----------------------------------------|
| **Tenant**         | per-customer isolation boundary   | the install. One per deploy. Invisible.|
| **User**           | does not exist                    | casual web login. N per install.       |
| **Project**        | implicit (`datasets/{name}/`)     | named first-class noun. Three-drop unit.|
| **Campaign**       | `optimize --config …` invocation  | run inside a project. Multiple per project.|
| **Cycle**          | persistence directory             | implementation detail. Hidden.         |
| **Operator nav**   | sidebar tree (sessions → cycles)  | chat list (claude.ai-shape).           |
| **Operator control** | CLI                             | chat (constant surface).               |

## The four nouns

- **Install** — one administrator. Hosts PromptPotter. Brands it
  (whitelabel slot at `projects/{install_id}/tenant.json` — note: the
  on-disk tenant directory survives as the install scope; the *user-
  facing* concept "tenant" is gone). Onboards web users.
- **User** — signs up over the web, casual auth. Has 1–2 connectors
  typically. Owns N projects.
- **Project** — the three-drop unit: a **dataset**, a **context.md**
  (task framing), a **pipeline.json** (backend pipeline + tunable
  surface). Today's `datasets/{name}/` directory is exactly this — it
  just isn't surfaced as a project. Per-user; shared visibility for
  measurements (see below).
- **Campaign** — one optimization run inside a project. Multiple
  campaigns per project; user can compare them. Maps 1:1 to today's
  cycle.

## The chat surface

Chat is the **constant control surface**, not an onboarding wizard.
Through it the user:

- Drops a project (paste / upload dataset, context, pipeline.json).
- Configures a campaign (the chat negotiates `campaign.json` with the
  user — this is the conversational wrapper over the existing config
  shape).
- **Interrupts mid-cycle** ("pause", "skip this round", "force L2").
- **Steers mid-cycle** ("focus on the verbose-output samples",
  "scrap the temperature axis").
- **Queries results** ("why did round 3 stall?", "show me the hardest
  samples", "compare campaigns A and B on overlap").
- **Asks the optimizer** about its own state (the LLM-side of chat has
  read access to `archive/measurements/`, `dashboard.json`, the per-
  cycle audit trail).

The dashboard surface (current Next.js dashboard) survives as the
**live-view companion** to chat — chat is where you talk to it,
dashboard is where you watch it work.

## Cross-user data leverage

This already works at the data layer:
`archive/measurements/{content_hash}/` is content-addressed on
`JobSearchPoint.content_hash(dataset)`. Two users running the same
backend, same pipeline params, same query reuse the same measurement.

What's missing is **surface**: the chat / project view doesn't show
"this query was measured 14× by other users on this install." The
data is there, the read is cheap, the UI doesn't expose it.

No new persistence; the leverage piece is one read panel.

## Non-goals (this milestone)

- **Cross-install sharing.** Install is still a hard isolation
  boundary; one install's measurements never leak to another.
- **Real authn/authz at scale** (SAML, SSO, RBAC). M13 ships casual
  auth — email + password, magic link, or oauth-via-one-provider. The
  hardened version is M13+ backlog.
- **Billing / quotas.** Out forever for the self-host story;
  separate billing surface if a hosted offering ships later.
- **Project sharing UI between users.** Measurements share; project
  configs do not. Two users can have separate projects whose
  measurements happen to overlap.
- **The chat LLM itself.** Slice 1 of build-out wires the chat shell +
  the read tools; the LLM persona / system prompt is its own design
  pass.

## Sequencing (not yet scheduled)

This is a multi-month surface — design pass, then sliced build.
Listed for context, not as a commitment:

1. **Auth + user identity.** New I/O kind (Control-remote). §0
   amendment lands first (the four-LLM-call invariant survives;
   Control-remote becomes the documented fourth I/O kind alongside
   Persistence / Display / Control-local).
2. **Project as a first-class noun on disk.** `projects/{install}/users/{uid}/projects/{pid}/`
   layout, or similar. `datasets/{name}/` migrates to this shape;
   no parallel paths.
3. **Webapp project view.** Drop-three-things upload. Lists past
   campaigns per project. Compare-campaigns view rides existing
   per-cycle data.
4. **Chat shell.** Read-only first: chat can query the optimizer's
   state but not steer. Sidebar shrinks to a chat list.
5. **Chat write-path.** Steering, interrupting, forking via chat.
   This reuses the M12 Track 3 control-plane endpoints (the daemon
   exposes stop / pause / fork / rewind; chat is one client of it).
6. **Cross-user measurement panel.** One read panel on the project
   view: "this query has been measured by N users; overlap recap."

Steps 1–3 are the "minimum to be a product." Steps 4–6 are the
differentiator.

## Pre-flight gate

Re-read **CLAUDE.md "Pre-flight gate"** §1–8 before any code lands
under this spec. Already-flagged items:

- **§0 bucket: NEW I/O kind.** Control-remote is named in §0 today
  as M12's territory; this spec depends on it. The auth + chat
  shell PR amends §0 before any code.
- **§0 bucket: NEW on-disk concept.** `users/` and `projects/`
  directories are new. The directory layout PR amends §0.
- **Names:** `Project` is distinct from `JobSearchPoint`,
  `Campaign`, `Cycle`, `Session`. `Install` is distinct from
  `Tenant` (replaces the user-facing meaning of "tenant" — the
  on-disk tenant directory keeps its name as an install-scope
  storage detail).
- **Vocabulary collision check:** `project_*` is already used in
  the user's memory tree (`project_whitelabel.md`, etc.). The
  on-disk dir name should be something else — candidate:
  `bench/{pid}/` or `work/{pid}/`. Decide before any code.

## What this spec replaces

- `docs/specs/m12-multi-tenant-session-skeleton.md` (deleted).
  Its core mistake was making "tenant" a visible navigation tier;
  the right shape is one-install, multi-user, chat-driven.
- The whitelabel infrastructure that commit `e036981f` introduced
  (tenant.json reader, /tenants endpoints, BrandStrip) was reverted
  alongside this spec. The install-level whitelabel slot returns
  when chat lands — at that point it's a topbar element, not a
  sidebar tier.

## Pointers

- Whitelabel direction memory: `project_whitelabel.md`.
- Launch positioning memory: `project_launch_positioning.md`.
- Cross-user leverage substrate: `archive/measurements/` (already
  content-hashed; nothing new to build).
- Today's dashboard surface: `webapp/app/page.tsx`.
- Today's CLI control surface (what chat eventually subsumes):
  `promptpotter/presentation/cli/`.
