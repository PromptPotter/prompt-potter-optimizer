# M12: Control Plane — Multi-User SaaS Hardening

**Status:** Specced. Webapp Phase 2; extracted from
[`m12-multi-connector.md`](m12-multi-connector.md) Tracks 3 + 3.5.
**Depends on:** [`m10-operator-control-loop.md`](m10-operator-control-loop.md)
— the single-operator write surface this milestone hardens into a SaaS.

## Why

The M10 Operator Control Loop mini-milestone gives one operator on one machine
a full write surface: launch / stop / resume / fork from the webapp, live SSE
reactivity, the `Control-remote` I/O kind, the in-process `JobRegistry`. That
is the "MS Word for yourself" install — every machine self-hosts and runs its
own loop.

M12 turns that install into a **hub**: one deployment serving N signed-in
users, with per-tenant isolation, login, and a whitelabel slot. The control
machinery is already built; M12 adds the auth boundary, the tenant-scoped
storage, and the multi-user UI on top of it. Whitelabel — PromptPotter sold
under a partner brand — becomes viable here.

Distributed / out-of-process workers stay **post-M13**. M12's `JobRegistry`
remains in the single API process; "more async" is a later milestone (see
[`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) and beyond).

## Tracks

### Track 1 — Authentication

Activates the `TenantContext` seam shaped in M9
(`application/bootstrap/session.py` — `tenant_id`, `user_id`, `capabilities`).

- **Auth middleware** populates `TenantContext` per request from a session
  cookie / token. Unauthenticated requests to control or tenant-scoped read
  routes are rejected; the static webapp shell and the login route stay open.
- **Login / logout UI** — the sidebar `Log out` affordance (already stubbed
  in the webapp) lights up; a login screen gates the control surface.
- **Local "MS Word" mode** — a single-machine install runs with auth
  **off**: one implicit `default` tenant, no login screen. Auth is a
  deployment toggle, not a hard dependency — the mini-milestone's
  single-operator experience is unchanged when auth is disabled.
- Provider choice (local password store vs. an external IdP) is a Track 1
  design decision; the middleware seam is provider-agnostic.

### Track 2 — Multi-tenant isolation

- **`TenantId` / `SafeName` newtypes** — the `DEFERRED-M12` item in
  [`security-audit.md`](security-audit.md) § SafeName / TenantId. A
  `SafeName = NewType("SafeName", str)` whose only constructor runs
  `validate_path_component`, and a `TenantId` plumbed through every store
  constructor — mirroring the existing `CycleDir` / `RootCycleDir` newtypes.
  Lite path-validation already landed; this is the structural migration.
- **`{tenant_id}/` path prefixes enforced** at the `infrastructure/store/`
  boundary. `build_stores()` (`infrastructure/store/stores.py`) already roots
  a `Stores` bundle under `projects/{tenant_id}/`; M12 makes that prefix
  non-optional and unforgeable — every store path derives from the
  `TenantContext`, never from a request field.
- **`JobRegistry` becomes tenant-scoped** — a job carries its `tenant_id`;
  control routes reject cross-tenant `job_id` access; the registry SSE channel
  fans only the caller's tenant's jobs.
- This is the single coordinated diff `security-audit.md` deferred: the
  newtype migration touches every store, so it lands once, with the
  multi-tenant rollout, not twice.

### Track 3 — Hub mode + whitelabel

- **Hub mode** — one install serves N connecting users. Tenancy is the
  isolation boundary; within a deployment, the admin provisions tenants.
- **Whitelabel slot** — `projects/{install_id}/tenant.json` carries the
  brand (name, logo, palette) the webapp shell reads at load. Whitelabel is a
  per-install config file, not a code fork.
- **Cross-user data leverage** already works at the data layer:
  `archive/measurements/{content_hash}/` is content-addressed on
  `JobSearchPoint.content_hash(dataset)`, so two users running the same
  pipeline params on the same query reuse the same measurement. M12 surfaces
  it; it does not build it.
- The full chat-first multi-user end state is
  [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md); M12 ships the
  install / tenant / user mechanics that M13's product surface rides.

### Track 4 — Chat-panel launcher

The M10 mini-milestone ships the campaign **configuration form** (dataset +
launch). M12 adds the second launcher shape — the **chat panel**: drop a
dataset → see a preview → toggle quiet evolution on. It wires to the
`restructure` optimizer node as the user-facing surface and matches the
"fix a broken LLM pipeline in half a day, then it just works" positioning.
Both shapes coexist; the form serves power users + reproducibility, the chat
panel serves low-friction onboarding.

## Out of scope

- **Connector / competitor / L4 / fitness work** — Tracks 1, 2, 4, 5 of
  [`m12-multi-connector.md`](m12-multi-connector.md), untouched by this spec.
- **The single-operator write surface** — [`m10-operator-control-loop.md`](m10-operator-control-loop.md).
- **Distributed / out-of-process workers; API ⇄ worker-fleet split** —
  post-M13.

## Entry / exit

**Entry:** the M10 Operator Control Loop mini-milestone shipped (`JobRegistry`,
`Control-remote`, SSE, webapp control surface live).

**Exit:**
- [ ] Auth middleware populates `TenantContext`; control routes reject
      unauthenticated requests; login / logout UI live; local auth-off mode
      verified.
- [ ] `TenantId` / `SafeName` newtypes plumbed through every store; tenant
      path prefixes unforgeable; `JobRegistry` tenant-scoped.
- [ ] Two tenants run concurrent campaigns with no cross-tenant data or
      control bleed.
- [ ] Whitelabel slot (`projects/{install_id}/tenant.json`) drives the webapp
      brand.
- [ ] Chat-panel launcher ships alongside the configuration form.

## Key existing code

| Area | Files |
|---|---|
| Tenant seam | `application/bootstrap/session.py` (`TenantContext`); `domain/tenant.py` |
| Store rooting | `infrastructure/store/stores.py` (`build_stores`, `Stores`) |
| Deferred newtypes | [`security-audit.md`](security-audit.md) § SafeName / TenantId |
| Control surface (from M10 mini-milestone) | `JobRegistry`, `Control-remote` routes, SSE channels — `m10-operator-control-loop.md` |
| Webapp shell | `webapp/app/`, `webapp/lib/workspace.tsx` (sidebar / `Log out` stub) |
| Chat panel | `webapp/components/dashboard/ChatPane.tsx` |

## Risks

| Risk | Mitigation |
|---|---|
| Newtype migration touches every store at once | Deliberate — `security-audit.md` deferred it precisely so it lands once with multi-tenant, not twice. |
| Multi-tenant activation breaks existing single-tenant data | Default-tenant migration before activation; auth-off local mode is the unchanged path. |
| Auth provider lock-in | Middleware seam is provider-agnostic; the provider is swappable behind `TenantContext`. |
| Tenant isolation depends on cycle-id integrity | `state-sync-cleanup.md` Phase 1 (dir name *is* the cycle id) is already a prerequisite of the M10 mini-milestone — isolation rests on it. |
