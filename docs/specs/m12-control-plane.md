# M12: Control Plane — Multi-User SaaS Hardening

> **Status:** Forward direction — spec only, no shipped code. Depends on M10 operator control loop landing first.

Depends on [`m10-operator-control-loop.md`](m10-operator-control-loop.md) (single-operator write surface this milestone hardens into a SaaS).

## What this covers

M10's mini-milestone gives one operator on one machine a full write surface (launch / stop / resume / fork from webapp, SSE, `Control-remote` I/O kind, in-process `JobRegistry`). M12 turns that into a **hub**: one deployment serving N signed-in users with per-tenant isolation, login, and a whitelabel slot. Control machinery already exists — M12 adds the auth boundary, tenant-scoped storage, and the multi-user UI.

Out of scope: distributed / out-of-process workers (post-M13); the multi-connector / competitor / L4 / fitness tracks ([`m12-multi-connector.md`](m12-multi-connector.md)); the single-operator write surface itself.

## Open items

- **Auth middleware** populates `TenantContext` per request; control + tenant-scoped reads reject unauthenticated; static shell + login route open. Local "MS Word" mode runs auth-**off** (one implicit `default` tenant). Provider (local password vs external IdP) is a Track 1 design call; middleware seam is provider-agnostic.
- **`TenantId` / `SafeName` newtypes** plumbed through every store constructor in one coordinated diff (mirrors `CycleDir` / `RootCycleDir`). Type-enforced multi-tenant boundary at every store entry point. Lite path-validation (`validate_path_component`) already landed in `infrastructure/store/paths.py`; this work makes the type the source of truth, not the validator.
- **Tenant path prefixes unforgeable.** `build_stores()` already roots under `projects/{tenant_id}/`; M12 makes that prefix non-optional and derived from `TenantContext`, never from a request field.
- **`JobRegistry` tenant-scoped.** Jobs carry their `tenant_id`; control routes reject cross-tenant `job_id`; SSE fans only the caller's tenant's jobs.
- **Hub mode + whitelabel.** `projects/{install_id}/tenant.json` carries brand (name, logo, palette) the webapp shell reads at load. Cross-user data leverage already works at the data layer (content-addressed `archive/measurements/`); M12 surfaces it.
- **Chat-panel launcher** alongside the configuration form. Form for power users + reproducibility; chat panel for low-friction onboarding (drop dataset → preview → toggle quiet evolution on). Wires to the `checkin` optimizer node.

## Code surface

| Area | Files |
|---|---|
| Tenant seam | `application/bootstrap/session.py::TenantContext`, `domain/tenant.py` |
| Store rooting | `infrastructure/store/stores.py::build_stores`, `Stores` |
| Webapp shell | `webapp/app/`, `webapp/lib/workspace.tsx` |
| Chat panel | `webapp/components/dashboard/ChatPane.tsx` |

## Cross-refs

[`m12-multi-connector.md`](m12-multi-connector.md) (prompt-injection Phase 2 detail) · [`state-sync-cleanup.md`](state-sync-cleanup.md) Phase 1 prereq · [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) (end-state product surface).
