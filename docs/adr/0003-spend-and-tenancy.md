---
status: accepted
date: 2026-05-26
deciders: [maintainer]
consulted: [identity-foundation, m12-control-plane]
informed: []
relates:
  - docs/adr/0002-identity-foundation.md
  - docs/adr/0001-m12-control-plane.md
supersedes: []
superseded-by: []
tags: [spend, tokens, ledger, identity, highway]
---

# Spend Tracking — the first consumer of the identity foundation

## Context and Problem Statement

[`0002-identity-foundation.md`](0002-identity-foundation.md) decides the identity contract (OIDC wire + RLS data + SCIM model). Its Stage-0 deliverable — the `IdentityContext` seam — needs a real payload riding it before downstream consumers ([`0001-m12-control-plane.md`](0001-m12-control-plane.md), [`../specs/roadmap.md`](../specs/roadmap.md)) build on it. Spend tracking (LLM token cost) is the chosen first payload: every LLM call (optimizer-loop AND backend) already exists in the codebase and emits one record per call; the question is *what path that record takes through the system*.

Two questions resolve here. **(1)** Does spend ride the canonical per-cycle ledger (`.runtime/ledger.jsonl`) like every other record, or a parallel pipeline? **(2)** Does identity attach to each record, or ride the cycle dir's tenant prefix?

How do we route token-cost telemetry from emit site to dashboard projection such that the seam is provably end-to-end while staying §0-clean (one I/O kind, sole writer per surface, no parallel pipelines)?

## Decision Drivers

* **Canonical ledger only.** §0 names Persistence as the sole writeable I/O kind for state; spend is state. Adding a `spend.json` parallel pipeline duplicates the ledger and bypasses the audit trail every other record gets.
* **Sole writer per surface.** Every projection in the codebase has exactly one writer (`LiveDashboardView` for `dashboard.json`, `AuditTrailView` for `round_NNNN.json`). Spend must not invent a parallel writer.
* **Kwargs-only emit-helper template.** ContextVar-scoped, no wrapper dataclass, builds the record inline. The shape is the template every future per-call telemetry kind copies (mirrors `emit_token_usage` → `emit_command` → `emit_command_ack` → future `emit_*`).
* **Identity from path, not per-record.** The per-cycle ledger already sits under `projects/{tenant_id}/`; the OS-enforced prefix is the ground truth. Stamping each event with a redundant `tenant_id` field grows the audit shape without changing semantics.
* **Halt probe must be clean.** Spend-budget enforcement reads the dashboard's `spend_total_used_usd` accessor — no `state["spend"]` peek, no dict reach-through.

## Considered Options

* **A: Canonical ledger via `emit_token_usage` over `_CYCLE_LEDGER` ContextVar + sole `LiveDashboardView` writer.** Tokens ride the per-cycle ledger as `TokenUsageRecord`; `LiveDashboardView._handle_token_usage` is sole writer of `dashboard.json::spend`; identity rides the cycle dir's tenant prefix.
* **B: Process global `_token_usage_sink`.** Emit-site looks up the sink; the sink batches and flushes to disk on its own schedule.
* **C: Wrapper dataclass `TokenUsage` with separate `apply_token_usage` chain.** Emit produces a `TokenUsage` value; `apply_token_usage` walks it into `add_to_spend_bucket`; the dashboard projection reads the bucket.
* **D: Separate `SpendProjection` + `spend.json` file.** Spend gets its own projection peer to `LiveDashboardView`, its own atomic file, its own poll endpoint.
* **E: Per-record `tenant_id` on `TokenUsageRecord`.** Every event self-declares its tenant; aggregation joins on the field.

## Decision Outcome

Chosen option: **A — canonical ledger via `emit_token_usage` over `_CYCLE_LEDGER` ContextVar + sole `LiveDashboardView` writer.**

Tokens ride the canonical per-cycle ledger (`.runtime/ledger.jsonl`) alongside every other record — no parallel pipeline. The "highway" is the existing Persistence stream; this arc promoted the path tokens take through that highway to the optimal sequence by eliminating four middlemen (process global, wrapper dataclass, dual writer, multi-hop apply chain). Both routes — backend-LLM cost and optimizer-loop token cost — flow parallel through the same ledger as `TokenUsageRecord` distinguished by `kind`. `AuditTrailView` continues to record them into `round_NNNN.json` (audit trail). `LiveDashboardView._handle_token_usage` projects them into `dashboard.json::spend` (display surface) and is sole writer. Identity scope rides the ledger path (tenant prefix on the per-cycle directory) — no per-record `tenant_id` field.

The halt probe at `application/runner/entry.py` reads `observers.dashboard.spend_total_used_usd` — a clean property accessor on the dashboard projection, not a `state["spend"]` peek. Operator-accepted Display→Control short-circuit: the dashboard owns spend semantics, so reading it back is not a parallel pipeline.

### Consequences

* **Good** — per-asyncio-task isolation comes free from the `_CYCLE_LEDGER` ContextVar; concurrent cycles (M12+) isolate without ceremony.
* **Good** — audit trail shape identical to every other record; one stream of truth.
* **Good** — `emit_token_usage` becomes the template every subsequent per-call telemetry kind copies (`emit_command`, `emit_command_ack`, future `emit_*`).
* **Good** — halt probe is a one-line property accessor; no fragile dict reach-through.
* **Good** — adding new `*Record` types is additive (new emit helper, new `_handle_*` branch on the projection); no schema churn elsewhere.
* **Neutral** — `LiveDashboardView` owns spend semantics (Display→Control short-circuit). Operator-accepted: the dashboard is the authoritative spend rollup, the halt probe just reads it back.
* **Bad** — none on disk; the arc shipped clean.

### Confirmation

Three layers of confirmation:

1. **Seam holds.** Gates #3 / #4 / #6 from [`0002-identity-foundation.md`](0002-identity-foundation.md) are enforced by the typed `IdentityContext` seam this ADR reifies (a wrong `build_stores` signature fails to typecheck) + review — no standing test (the structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).
2. **Halt probe round-trip.** `application/runner/entry.py` reads `observers.dashboard.spend_total_used_usd`; live campaigns demonstrate halt-at-budget with no `state["spend"]` peek anywhere in the codebase.
3. **Audit trail integrity on disk.** `round_NNNN.json` continues to carry `TokenUsageRecord` entries; `dashboard.json::spend` continues to render bar + publication + `log.md` values from one rollup. No divergence between display and audit.

## Pros and Cons of the Options

### A — Canonical ledger + sole writer (chosen)

* **Good** — one stream of truth; one writer per surface; one emit shape templated across all future per-call telemetry.
* **Good** — identity from path prefix avoids per-record duplication.
* **Neutral** — `LiveDashboardView` owns spend semantics; halt probe reads it back.
* **Bad** — none observed on disk after Phases 1-3 shipped.

### B — Process global `_token_usage_sink`

* **Good** — emit-site lookup is one global.
* **Bad** — concurrent cycles (M12+) collide on the global; isolation requires bolt-on per-task scoping.
* **Bad** — flushes desynchronize from cycle teardown; resume can lose unflushed events.

### C — Wrapper dataclass + multi-hop apply chain

* **Good** — explicit type at the boundary.
* **Bad** — three sites maintain the same shape (emit → wrap → apply); divergence is silent.
* **Bad** — three function calls for one logical operation; profile shows the hops.

### D — Separate `SpendProjection` + `spend.json`

* **Good** — clean separation of dashboard from spend.
* **Bad** — parallel pipeline by construction; spend events bypass the ledger.
* **Bad** — `dashboard.json` and `spend.json` race during cycle close; the operator must reconcile two sources.

### E — Per-record `tenant_id`

* **Good** — events self-describe.
* **Bad** — duplicates the cycle dir's prefix on every record.
* **Bad** — invites "events from multiple tenants in one ledger" — exactly the leak the cycle dir's prefix prevents structurally.

## More Information

### Status (what's already on disk)

- **`IdentityContext` seam shipped** — `promptpotter/shared/identity.py` carries the 5-field frozen dataclass; `default_identity(tenant_id="default")` is the Stage-0 factory; `TenantId` / `UserId` / `Issuer` / `SafeName` newtypes live in `promptpotter/domain/identity.py`; `safe_name()` validator gates path-segment use. **`TenantContext` deleted**; `Session.identity: IdentityContext` replaces `Session.tenant`.
- **`build_stores(identity, *, projects_root=…, benchmarks_root=…)` shipped** — `infrastructure/store/stores.py`. `Stores.identity` is the sole tenant-scope source; `Stores.tenant_id` is a derived `@property` returning the `TenantId` newtype. `DEFAULT_TENANT_ID` removed.
- **FastAPI seam shipped** — `presentation/api/deps.py::resolve_identity` returns the Stage-0 default; `IdentityDep` / `build_stores_from_identity` / `StoreDep` chain it. Stage 1 (M12 OIDC client) replaces only `resolve_identity`.
- **CLI seam shipped** — `presentation/cli/commands/_shared.py::identity_from_args` reads `args.tenant`; `init_services_cli(identity=…)` + `init_services(identity=…)` accept the `IdentityContext`. All 8 `build_stores` call sites migrated.
- **Seam enforced by types + review** — no-drift gates #3 (`build_stores` signature), #4 (`Stores.identity` sole tenant source), #6 (SCIM-named field set) ride the typed seam; no standing test (structural/contract suite cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).
- `shared/spend.py` shipped — three-layer rate resolution (wire passthrough → `~/.promptpotter/rates.json` 24 h TTL → bundled `shared/data/rates.json`), stdlib-only fetcher, 8 MB cap.
- `TokenUsageRecord` is the sole cross-ledger shape for per-call cost telemetry (`domain/run_records.py::TokenUsageRecord`). `TokenUsage` dataclass deleted. OpenRouter wire cost extracted in `application/optimization/dispatch/llm_call/call.py` and passed as `cost_usd=` kwarg.
- `--spend-budget` plumbed CLI → loop, `StopReason.SPEND_BUDGET` exists (`domain/phases.py`), halt probe at `application/runner/entry.py` reads `observers.dashboard.spend_total_used_usd` (clean accessor on the dashboard projection; no `state["spend"]` peek).

**What landed (Phases 2–3 of the token-unification arc):** backend cost flows through `emit_token_usage(kind="backend", ...)` at the per-node call boundary in `application/scoring/sample_measurement.py` (one `TokenUsageRecord` per pipeline node per uncached sample, alongside optimizer-kind records on the same per-cycle ledger); `accumulate_backend_spend` deleted; `LiveDashboardView._handle_token_usage` is the sole writer for both buckets (`backend` + `loop`) and the sole rollup point for `dashboard.json::spend::total_used_usd`; the 3-hop chain (`_handle → apply → bucket`) collapsed into one method; `MAX_SPEND` → `SPEND_BUDGET` rename complete (StopReason + CLI flag + config field); `OptimizationConfig.spend_budget_usd: float | None = None` is the config-side budget (CLI `--spend-budget` overrides when both are supplied); halt probe goes through the `LiveDashboardView.spend_total_used_usd` property (operator-accepted Display→Control short-circuit — the dashboard owns spend semantics, so reading it back is not a parallel pipeline).

### Highway architecture (what shipped)

Tokens ride the canonical ledger alongside every other record — not a parallel pipeline. The "highway" is the existing per-cycle `.runtime/ledger.jsonl` stream; what this arc did is **promote the path tokens take through that highway to the optimal sequence**, by eliminating four middlemen:

1. **Process global gone** — `_token_usage_sink` deleted. `emit_token_usage` reads the active ledger from `_CYCLE_LEDGER: ContextVar[CycleEventLog | None]` (`infrastructure/llm/telemetry.py`); `build_run_observers` calls `set_cycle_ledger(ledger)` at cycle start; `RunObservers.drain_all` resets it on teardown. Per-asyncio-task isolation — concurrent cycles (M12+) get isolation for free.
2. **Wrapper dataclass gone** — `TokenUsage` deleted. `emit_token_usage(*, node, kind, input_tokens, output_tokens, duration_s, model=None, cost_usd=None)` is kwargs-only; builds `TokenUsageRecord` directly inside the helper. Round is read from `_CURRENT_ROUND: ContextVar[int | None]` (set by `RunCallbacks.set_round`).
3. **Sole spend writer** — `LiveDashboardView._handle_token_usage` routes by `record.kind` (`optimizer` → `loop`, `backend` → `backend`), adds to the bucket, recomputes `total_used_usd` — all inline. No `apply_token_usage` / `add_to_spend_bucket` chain.
4. **No dual writer** — backend cost flows through `emit_token_usage(kind="backend", ...)` at the `measure_sample` per-step site (over `step_tokens`, uncached only). `accumulate_backend_spend` deleted; the `view.py::_absorb_sample_scored` site no longer touches `state['spend']`.

Both cost routes — backend-LLM cost and optimizer-loop token cost — flow parallel through the same ledger as `TokenUsageRecord` with different `kind`. `AuditTrailView` continues to record them into `round_NNNN.json` like every other record (audit trail). `LiveDashboardView` projects them into `dashboard.json::spend` (display surface). Identity scope rides the ledger path (tenant prefix on the per-cycle directory) — no per-record `tenant_id` field.

Forward direction for matching-shape flows: every other `RunCallbacks.on_*` method that wraps a per-call event in a `*Record` and appends is a candidate for the same `emit_*` shape after this arc proves the template — see [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) for the catalogue entry.

### In-scope deliverables

#### 1. Newtypes — type-enforced identity boundary

Reifies the Stage-0 deliverables of [`0002-identity-foundation.md`](0002-identity-foundation.md):

- `TenantId(str)` — `NewType` at `promptpotter/domain/identity.py` (shipped, alongside `UserId` / `Issuer` / `SafeName`). Constructed indirectly: callers receive a `TenantId` only by reading `IdentityContext.tenant_id` — no raw factory. `safe_name(raw: str) → SafeName` validates slug-strict path segments at the identity-layer boundary.
- `UserId(str)` — `NewType` at `promptpotter/domain/identity.py` (shipped). Stage 0: always `UserId("default")`. Stage 1: `f"{issuer}:{sub}"` from the verified ID Token (per identity-foundation Contract A).
- `SafeName(str)` — same shape, for any user-supplied path segment that isn't a tenant or user id (campaign labels, future project ids). Distinct from `TenantId` / `UserId` so a function signature can't accept the wrong identity by accident.
- All `mypy --strict` clean (domain layer rule, `domain/CLAUDE.md`).

#### 2. `IdentityContext` is the construction route for `Stores`

- `TenantContext` deleted; `Session.identity: IdentityContext` (`application/initialization/session.py`) replaces `Session.tenant: TenantContext | None`. Behavior change, no shim. **(shipped)**
- `build_stores(identity, *, projects_root=…, benchmarks_root=…)` (`infrastructure/store/stores.py`) is the only construction route. The newtype + the context object collapse into one positional argument — no caller passes a raw string. `build_stores` reads `identity.tenant_id` to root the file-based stores under `projects/{tenant_id}/`. **(shipped)**
- Spend events that carry user identity use SCIM-named fields (`User.id`, `User.externalId`); the `org_id` tenant claim feeds `IdentityContext.tenant_id`. See [`0002-identity-foundation.md` § Data model](0002-identity-foundation.md#data-model--scim-20-core--enterpriseuser).

#### 3. Single seam per entry point

The seam is where `IdentityContext` enters the process. Three entry points, two construction functions (`default_identity()` in `shared/identity.py` for the auth-off default; `identity_from_args(args)` in `presentation/cli/commands/_shared.py` for the CLI seam) so the rule "tenant prefix is derived from identity, never from a request field" rides the typed seam (no-drift gates #3 + #4 from identity-foundation; enforced by types + review, no standing test):

| Entry point | Seam | Single-operator default (Stage 0) |
|---|---|---|
| CLI (`new` / `resume` / `verify` / `reset`) | `presentation/cli/commands/_shared.py::identity_from_args(args)` derives the `IdentityContext` from `args.tenant`; `init_services_cli(identity=…)` threads it into `init_services` and then `build_stores`. | `default_identity()` → `IdentityContext(user_id=UserId("default"), tenant_id=TenantId("default"), issuer=None, claims={}, capabilities=frozenset())`. |
| FastAPI (`presentation/api/`) | `presentation/api/deps.py::resolve_identity` returns the Stage-0 default; `IdentityDep` / `build_stores_from_identity` / `StoreDep` chain it for routers. Stage-1 OIDC middleware replaces only `resolve_identity`. | Same `default_identity()` value; auth-off mode is one branch, not a feature flag. |
| Background jobs (fork, sweep, sweep_restore) | Inherit from parent `Session.identity` — already populated; never re-resolved from disk. `application/sweep.py`, `application/optimization/resume_and_fork/fork_siblings.py`. | Same `IdentityContext` flows through; no extra ceremony. |

**Auth-off definition (Stage 0).** A single boolean at startup (`settings.AUTH_OFF`, default `True`). When set: the FastAPI dependency returns the Stage-0 `IdentityContext` default unconditionally and OIDC middleware is not mounted. The CLI is auth-off by definition (no request context). **If a reader of this ADR can't convince themselves that today's `python -m promptpotter new aime` runs unchanged, the ADR is wrong** — the only delta is internal types. Stage 1 (OIDC client) flips the default but does not rewrite this seam — same `IdentityContext`, different source.

#### 4. Spend feature

- **Resolution.** `shared/spend.py` shipped as-is — three layers, stdlib only.
- **Dashboard projection.** `dashboard.json::spend` is the per-cycle aggregator: a `SpendRollup` over two `SpendBucket`s (`domain/results.py` owns the shape — read it there, it moves), sole writer `LiveDashboardView._handle_token_usage` (see § Highway architecture). **The same shape is `CycleResult.spend`**, so there is no bucket→cycle-total map to keep in step; the totals are properties on the rollup. Bar, publication, and `log.md` all read `total_used_usd`; the budget lives on `run_limits.spend_budget_usd`, not in the spend block.
- **Budget config + halt.** `OptimizationConfig.spend_budget_usd: float | None`. `StopReason.SPEND_BUDGET` (root `CLAUDE.md`: no back-compat). `_probe_cycle_spend` halts the **current cycle only** at round boundary; the **per-user, cross-cycle** host-wallet gate is the **coupon** (see § Host coupon below), not a daily cap.
- **Ledger event shape.** `TokenUsageRecord` stays cycle-scoped (already keyed on the ledger which is per-cycle). Identity is resolved at aggregation time by reading `Session.identity` — no `tenant_id` field on the event. The cycle dir's tenant prefix is the ground truth; the event doesn't need to duplicate it.

#### 5. Rate cache is install-scoped, not tenant-scoped

`~/.promptpotter/rates.json` is shared across tenants (one rate table per install — model pricing is global, not per-tenant). Confirmed: no per-tenant scoping.

#### 6. Migration — existing `projects/{tenant}/` directory

On first upgrade: the on-disk layout is already `projects/{tenant}/` with `tenant_id = "default"` (CLI default has been `"default"` since the dir was introduced). **No migration step needed.** A startup check in `application/initialization/wiring.py` verifies the dir exists; if a non-`default` tenant dir is present (operator created one manually), it's used unchanged.

### Host coupon + BYO keys — the per-user wallet gate (Lane A2)

The shipped spend feature *measures* cost; it does not *bound a user against the host's
wallet*. The beta runs every allowlisted user on one shared `.env` key — each one spends the
operator's quota with no per-user ceiling. This section is the contract that closes that gap.
Two concerns stay **clearly separated**:

- **Wallet protection (host's money)** → the **coupon**. Meters host-key spend only.
- **Abuse protection (shared single-process machine)** → concurrency + campaigns/day +
  rate-limit (`application/jobs/quota.py`, `JobRegistry`). Key-source-agnostic; orthogonal;
  unchanged.

**Ledger gains a `key_source`.** `TokenUsageRecord` gains `key_source: "host" | "user"` so
host-key spend sums **separately** from user-key spend — the one field that makes
host-only metering and real provenance on `/auth/activity` possible. It is the *one* allowed
exception to "identity from path, not per-record" (option E): `key_source` is not identity,
it is which wallet paid, and the coupon math needs it on the record. Declared on
`TokenUsagePayload` in [`../specs/m12-events-asyncapi.yaml`](../specs/m12-events-asyncapi.yaml).

**The coupon (`grant.json`).** Per user at `projects/{tenant}/grant.json`:
`{amount_usd, issued_at, expires_at}`. A **coupon/voucher** — a fixed size + an expiration
date, issued per user (default on allowlist-add; operator-adjustable). Remaining is **derived
from the ledger** (`amount_usd − host_key_spend_since(issued_at)`), never a decrementing
counter — one source of truth, consistent with option A. Install-global defaults
(`amount_usd`, validity window, `coupon_void_on_byo`) live in `config/settings.py`; the
per-user instance lives in `grant.json`.

**BYO keys (`api_keys.json`).** Per user at `projects/{tenant}/api_keys.json` — Fernet
ciphertext (`SECRETS_FERNET_KEY`, no plaintext fallback) + a plaintext `providers_set` index;
the key is never echoed / logged / traced.

**Resolution order — the one choke point.** `resolve_api_key(identity, provider, stores) →
ResolvedKey{key, source}`, the identity-aware wrapper over the identity-free
`get_llm_client(provider, *, api_key=None)`:

1. **User has own key for this provider** → use it. `source = user`. **No coupon check** —
   their money, the host gate does not apply (this is what "BYO lifts the host coupon" means).
2. **Else coupon alive** (remaining > 0 and not expired) → host key. `source = host`. Metered
   to the coupon.
3. **Else** → `HostAllowanceExhaustedError` → **422 `host_allowance_exhausted`**, guiding the
   user to add their own key. (Distinct from **422 `no_api_key`** = auth-off / no user key
   *and* no host key configured at all.)

Step 1 short-circuits **per provider** — a user with a key for provider X still burns the
coupon on provider Y unless the coupon is voided.

**Coupon-void-on-BYO (the host's choice).** On `PUT /auth/api-keys/{provider}`: if
`settings.coupon_void_on_byo` is set, the user's `grant.json::expires_at` is set to now (the
remaining coupon dies the moment they go self-serve); otherwise it persists as spendable free
credit. Entirely the host's policy.

**D1 — the coupon is the single host-wallet gate.** Shipping the coupon *and* keeping the
daily-cap path (`effective_spend_cap_usd` / `User.spend_budget_usd_daily`) would be two
mechanisms guarding one concern — the "no redundant mechanism" rule (root `CLAUDE.md`). The
daily-cap path is **deleted**; coupon-remaining is the host ceiling. (Tracked in
[`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md).)

**D2 — live, not a mint-time snapshot.** The per-cycle `BudgetGate`
(`application/runner/termination.py`) reads coupon-remaining (re-summed from the host-key
ledger every tick) instead of the daily-cap snapshot — closing the "launch-snapshot only"
liveness gap. New `StopReason.HOST_ALLOWANCE`.

The `/auth/api-keys` + `/auth/coupon` verbs ride the **auth router** (account-scoped siblings
of the shipped `/auth/{quota-status,user-settings}`), **not** the control-plane
`m12-api-openapi.yaml` — whose scope is the closed `/commands/*` set. Only the event-surface
change (`key_source` on `TokenUsagePayload`) is asyncapi-declared. Build direction + status:
[`../specs/roadmap.md`](../specs/roadmap.md) § Host coupon + BYO per-user API keys.

### §0 amendment?

**No — this ADR.** The Stage-0 `IdentityContext` reification is a refinement of the existing **Persistence** I/O kind (tenant-prefix on every store key); the seam itself rides existing wiring (init → Session). The §0 amendment for the new `Identity` I/O kind lands with **Stage 1 of [`0002-identity-foundation.md`](0002-identity-foundation.md)** (OIDC ingress at the API boundary) — see that ADR's "§0 amendment" section.

### Anchors

Every claim names a file. A stale path here fails loud as a broken link — verified by review, no standing test (see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

| Concern | File |
|---|---|
| `IdentityContext` (shipped — supersedes deleted `TenantContext`) | `promptpotter/shared/identity.py` |
| `TenantId` / `UserId` / `Issuer` / `SafeName` newtypes + `safe_name` (shipped) | `promptpotter/domain/identity.py` |
| `Session.identity` field (shipped) | `promptpotter/application/initialization/session.py` |
| `build_stores(identity, …)` rooting (shipped) | `promptpotter/infrastructure/store/stores.py` |
| Path-component validator | `promptpotter/infrastructure/store/io.py` |
| CLI seam (shipped) | `promptpotter/presentation/cli/commands/_shared.py` |
| API seam (shipped) | `promptpotter/presentation/api/deps.py` |
| Background job inheritance | `promptpotter/application/sweep.py` |
| Spend resolution | `promptpotter/shared/spend.py` |
| Token emit (optimizer) | `promptpotter/application/optimization/dispatch/llm_call/call.py` |
| Token emit (backend) | `promptpotter/application/scoring/sample_measurement.py` |
| Token emit helper + ContextVars | `promptpotter/infrastructure/llm/telemetry.py` |
| ContextVar lifecycle | `promptpotter/application/run_observers.py` |
| Token shape | `promptpotter/domain/run_records.py` |
| Sole spend writer + halt accessor | `promptpotter/infrastructure/projections/live_dashboard/view.py` |
| Bucket shapes + cycle totals | `promptpotter/infrastructure/projections/live_dashboard/state.py` |
| Budget config | `promptpotter/application/config.py` |
| Stop reason | `promptpotter/domain/phases.py` |
| Budget probe + halt | `promptpotter/application/runner/entry.py` |
| CLI flag | `promptpotter/presentation/cli/parsers.py` |

### Cross-refs

- [`0002-identity-foundation.md`](0002-identity-foundation.md) — **the foundation this ADR consumes.** The two contracts (OIDC wire + RLS data), the `IdentityContext` shape, the three-stage staging, the no-drift gates. Read it first.
- [`0001-m12-control-plane.md`](0001-m12-control-plane.md) — Stage-1 OIDC client, `JobRegistry` identity-scoping, hub mode (next consumer of identity-foundation).
- [`../specs/roadmap.md`](../specs/roadmap.md) — `Install` is the user-facing name for `TenantId` (mapped onto OIDC `iss`); vocabulary shifts, on-disk identity unchanged.
- [`../specs/roadmap.md`](../specs/roadmap.md) — Phase 1 dependency (identity collapse touches the same store-seam files).
