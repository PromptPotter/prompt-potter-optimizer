# Spend Tracking — the first consumer of the identity foundation

> **Status:** Forward direction — partial scaffolding shipped (see Status section).
> **Depends on:** [`identity-foundation.md`](identity-foundation.md) Stage 0 (`IdentityContext` reification — `TenantContext` collapses into it).
> **First consumer of:** the identity-foundation seam. Demonstrates the seam works end-to-end with a working spend payload as proof.

Spend tracking is the **payload** that lands the Stage-0 `IdentityContext` seam through the codebase. The contracts — OIDC-shape identity, tenant-scoped storage — live in [`identity-foundation.md`](identity-foundation.md). This spec covers what flows through the seam first.

This is **not** "spend tracking with `tenant_id` sprinkled on." It is the spend feature riding the Stage-0 `IdentityContext` end-to-end — proving the seam carries a real payload before M12 / M13 build on it.

## Status (what's already on disk)

- `TenantContext` exists — `application/bootstrap/session.py:33` (`tenant_id`, `user_id`, `capabilities`); default-constructed, never plumbed end-to-end. Per `identity-foundation.md`, this collapses into `IdentityContext` in Stage 0.
- `build_stores(tenant_id=DEFAULT_TENANT_ID="default")` already roots under `projects/{tenant_id}/` — `infrastructure/store/stores.py:106`, `infrastructure/store/paths.py:DEFAULT_TENANT_ID`.
- `validate_path_component(tenant_id)` already called inside `build_stores` — but `tenant_id` is a bare `str`, not a newtype.
- `shared/spend.py` shipped — three-layer rate resolution (wire passthrough → `~/.promptpotter/rates.json` 24 h TTL → bundled `shared/data/rates.json`), stdlib-only fetcher, 8 MB cap.
- `TokenUsage.cost_usd` slot exists — `infrastructure/llm/models.py:51`; OpenRouter wire cost extracted in `application/optimization/dispatch/llm_call/call.py:360`.
- `--max_spend_usd` plumbed CLI → loop, `StopReason.MAX_SPEND` exists (`domain/phases.py:61`), `_probe_cycle_spend` in `application/runner/entry.py:146`.

**What's missing (Stage-0 identity-foundation reification + spend payload):** `TenantId` / `UserId` / `SafeName` newtypes (Stage-0 deliverables of identity-foundation); `IdentityContext` as the sole construction route for `Stores`; `OptimizationConfig.spend_budget_usd` config-side field (today's `--max_spend_usd` is CLI-only); explicit single-operator bootstrap path that makes the whole thing invisible at default settings; identity-scoped ledger event for backend spend.

## What's in scope

### 1. Newtypes — type-enforced identity boundary

Reifies the Stage-0 deliverables of [`identity-foundation.md`](identity-foundation.md):

- `TenantId(str)` — `NewType` at `domain/tenant.py` (new file, peer to `cycle_paths.py`). Constructed via one factory: `TenantId.parse(raw: str) → TenantId`, wrapping `validate_path_component`. `validate_path_component` becomes a consumer of the newtype, not its source of truth.
- `UserId(str)` — `NewType` at `domain/identity.py` (new file). Stage 0: always `UserId("default")`. Stage 1: `f"{issuer}:{sub}"` from the verified ID Token (per identity-foundation Contract A).
- `SafeName(str)` — same shape, for any user-supplied path segment that isn't a tenant or user id (campaign labels, future project ids). Distinct from `TenantId` / `UserId` so a function signature can't accept the wrong identity by accident.
- All `mypy --strict` clean (domain layer rule, `domain/CLAUDE.md`).

### 2. `IdentityContext` is the construction route for `Stores`

- `TenantContext` (`application/bootstrap/session.py:33`) is renamed/restructured to `IdentityContext` per identity-foundation. `Session.tenant: TenantContext | None` becomes `Session.identity: IdentityContext`. **Behavior change, no shim.**
- `build_stores(identity: IdentityContext, …)` replaces today's `build_stores(tenant_id=…)`. The newtype + the context object collapse into one parameter — no caller passes a raw string ever again. `build_stores` reads `identity.tenant_id` to root the file-based stores under `projects/{tenant_id}/`.
- Spend events that carry user identity use SCIM-named fields (`User.id`, `User.externalId`); the `org_id` tenant claim feeds `IdentityContext.tenant_id`. See [`identity-foundation.md` § Data model](identity-foundation.md#data-model--scim-20-core--enterpriseuser).

### 3. Single seam per entry point

The seam is where `IdentityContext` enters the process. Three entry points, three seams, **one** construction function (`resolve_identity_context() → IdentityContext`) so the rule "tenant prefix is derived from identity, never from a request field" is enforceable by `tests/test_invariants.py` (no-drift gate #4 from identity-foundation):

| Entry point | Seam | Single-operator default (Stage 0) |
|---|---|---|
| CLI (`new` / `resume` / `verify` / `reset` / `sweep`) | `presentation/cli/commands/_shared.py::init_services_cli` resolves `IdentityContext` from `args.tenant` (default `"default"`) before constructing `Stores`. | `IdentityContext(user_id=UserId("default"), tenant_id=TenantId("default"), issuer=None, claims={}, capabilities=frozenset())` — the existing `getattr(args, "tenant", "default")` path, made type-safe. |
| FastAPI (`presentation/api/`) | New `presentation/api/deps.py::identity_context` dependency; OIDC middleware (Stage 1 / M12) populates it, auth-off mode returns the Stage-0 default. | Auth-off mode is the single-operator path; one branch, not a feature flag. |
| Background jobs (fork, sweep, sweep_restore) | Inherit from parent `Session.identity` — already populated; never re-resolved from disk. `application/sweep/sweep_runner.py`, `application/optimization/resume_and_fork/fork_siblings.py`. | Same `IdentityContext` flows through; no extra ceremony. |

**Auth-off definition (Stage 0).** A single boolean at startup (`settings.AUTH_OFF`, default `True`). When set: the FastAPI dependency returns the Stage-0 `IdentityContext` default unconditionally and OIDC middleware is not mounted. The CLI is auth-off by definition (no request context). **If a reader of this spec can't convince themselves that today's `python -m promptpotter new aime` runs unchanged, the spec is wrong** — the only delta is internal types. Stage 1 (OIDC client) flips the default but does not rewrite this seam — same `IdentityContext`, different source.

### 4. Spend feature (absorbs `m11-spend-tracking.md`)

- **Per-cycle aggregator.** One `Spend` dataclass per cycle, owned by `LiveStateView` (already exists — see `infrastructure/projections/live_state/`). `accumulate_backend_spend` and `apply_token_usage` already there.
- **Resolution.** `shared/spend.py` shipped as-is — three layers, stdlib only.
- **Dashboard projection.** `dashboard.json::spend = {used_usd, budget_usd, by_kind, calls, unknown_calls}` — written by `LiveDashboardView._persist` (`infrastructure/projections/live_dashboard/view.py`). Bar, publication, and `log.md` all read this one number.
- **Budget config + halt.** Add `OptimizationConfig.spend_budget_usd: float | None`. Rename `StopReason.MAX_SPEND` → `StopReason.SPEND_BUDGET` (root `CLAUDE.md`: no back-compat). `_probe_cycle_spend` halts the **current cycle only** at round boundary; tenant-wide enforcement is M12 / `JobRegistry` work.
- **Ledger event shape.** `TokenUsageRecord` stays cycle-scoped (already keyed on the ledger which is per-cycle). Identity is resolved at aggregation time by reading `Session.identity` — no `tenant_id` field on the event. The cycle dir's tenant prefix is the ground truth; the event doesn't need to duplicate it.

### 5. Rate cache is install-scoped, not tenant-scoped

`~/.promptpotter/rates.json` is shared across tenants (one rate table per install — model pricing is global, not per-tenant). Confirmed: no per-tenant scoping. Documented in `shared/spend.py` module docstring.

### 6. Migration — existing `projects/{tenant}/` directory

On first upgrade: the on-disk layout is already `projects/{tenant}/` with `tenant_id = "default"` (CLI default has been `"default"` since the dir was introduced). **No migration step needed.** A startup check in `application/bootstrap/wiring.py` verifies the dir exists; if a non-`default` tenant dir is present (operator created one manually), it's used unchanged.

## What's explicitly out of scope

- **OIDC client implementation, IdP federation, session cookies, JWT verification** — all of Stage 1 from [`identity-foundation.md`](identity-foundation.md). This spec lands Stage 0 only. The seam is the same; the source flips at Stage 1.
- **PostgreSQL RLS adapter** — Stage 2 of identity-foundation; today's `projects/{tenant_id}/` is the Stage-0 form of the data-isolation contract.
- **RBAC, capability checks beyond a flat `frozenset[str]`** — `IdentityContext.capabilities` exists but stays empty in M11.
- **Billing, quotas, per-tenant rate limiting** — M13+ backlog.
- **`JobRegistry` identity-scoping** — lives in `m12-control-plane.md`. This spec is the seam; the work lives there.
- **Webapp tenant chips / install picker** — M12 (control plane) + M13 (multi-user UI).
- **M13 vocabulary shift (`Install` / `User` / `Project`)** — on-disk identity stays `tenant_id`; `Install` is what M13 *calls* it (mapped onto OIDC `iss` per identity-foundation). See `m13-chat-first-user-web.md` for the rename plan.
- **`state-sync-cleanup.md` Phase 1** (identity collapse, `index.json::campaign_id` removal) — dependency, not absorbed. Land Phase 1 first if you don't want to re-touch the same files.
- **Per-round / per-candidate spend breakdown, cross-cycle accounting in `archive/`** — original `m11-spend-tracking.md` out-of-scope, preserved.

## §0 amendment?

**No — this spec.** The Stage-0 `IdentityContext` reification is a refinement of the existing **Persistence** I/O kind (tenant-prefix on every store key); the seam itself rides existing wiring (bootstrap → Session). The §0 amendment for the new `Identity` I/O kind lands with **Stage 1 of identity-foundation** (OIDC ingress at the API boundary) — see [`identity-foundation.md`](identity-foundation.md) "§0 amendment".

## Anchors (every claim names a file)

| Concern | File |
|---|---|
| `TenantContext` (existing — collapses into `IdentityContext`) | `application/bootstrap/session.py:33` |
| `TenantId` / `UserId` / `SafeName` (new) | `domain/tenant.py`, `domain/identity.py` (new) |
| `IdentityContext` (new — supersedes `TenantContext`) | `application/bootstrap/session.py` |
| `build_stores` rooting | `infrastructure/store/stores.py:106` |
| Path-component validator | `infrastructure/store/base.py:16`, `infrastructure/store/paths.py:DEFAULT_TENANT_ID` |
| CLI seam | `presentation/cli/commands/_shared.py::init_services_cli`, `presentation/cli/commands/new.py:118` |
| API seam | `presentation/api/deps.py` (new `identity_context` dep), `presentation/api/routers/` (consumers) |
| Background job inheritance | `application/sweep/sweep_runner.py`, `application/optimization/resume_and_fork/fork_siblings.py` |
| Spend resolution | `shared/spend.py`, `shared/data/rates.json` |
| Token emit site | `application/optimization/dispatch/llm_call/call.py:360` |
| Token shape | `infrastructure/llm/models.py:51` |
| Dashboard projection | `infrastructure/projections/live_dashboard/view.py` |
| State aggregator | `infrastructure/projections/live_state/` |
| Budget config | `application/config.py::OptimizationConfig` |
| Stop reason | `domain/phases.py:61` (rename `MAX_SPEND` → `SPEND_BUDGET`) |
| Budget probe + halt | `application/runner/entry.py:146` |

## Cross-refs

- [`identity-foundation.md`](identity-foundation.md) — **the foundation this spec consumes.** The two contracts (OIDC wire + RLS data), the `IdentityContext` shape, the three-stage staging, the no-drift gates. Read it first.
- [`m12-control-plane.md`](m12-control-plane.md) — Stage-1 OIDC client, `JobRegistry` identity-scoping, hub mode (next consumer of identity-foundation).
- [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) — `Install` is the user-facing name for `TenantId` (mapped onto OIDC `iss`); vocabulary shifts, on-disk identity unchanged.
- [`state-sync-cleanup.md`](state-sync-cleanup.md) — Phase 1 dependency (identity collapse touches the same store-seam files).
