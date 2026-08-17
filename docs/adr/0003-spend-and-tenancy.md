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
- `shared/pricing.py` shipped — three-layer rate resolution (wire passthrough → `~/.promptpotter/rates.json` 24 h TTL → bundled `shared/data/rates.json`), stdlib-only fetcher, 8 MB cap.
- `TokenUsageRecord` is the sole cross-ledger shape for per-call cost telemetry (`domain/run_records.py::TokenUsageRecord`). `TokenUsage` dataclass deleted. Wire cost is extracted where the provider is spoken to — `infrastructure/llm/openai_compat.py::_attempt_cost` onto `LLMResponse.cost_usd` — and `call.py` passes it as the `cost_usd=` kwarg. This line read "extracted in `call.py`" until 2026-08-13, and `call.py` did read a `usage["cost"]` key; the client never wrote one, so the field was `None` on every optimizer call ever recorded. An ADR asserting a mechanism shipped is why three downstream surfaces explained the symptom as the provider sending no cost.
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

- **Resolution.** `shared/pricing.py` shipped as-is — three layers, stdlib only.
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

**D1 — ONE host-wallet gate, expressed in two units; whichever trips first.** Two *mechanisms*
guarding one concern is the "no redundant mechanism" rule (root `CLAUDE.md`), so there is exactly
one: `admit_launch` composing `Settings.FREE_TIER_SPEND_CAP_USD` /
`FREE_TIER_TOKEN_CAP` (overridable per account at `User.spend_budget_usd_total` /
`token_budget_total`) against what the account has used over its WHOLE ledger. It became
load-bearing when signup stopped requiring approval — it is now the only thing standing between a
stranger and the host's provider key, which is why it is a lifetime allowance rather than the
per-day one it replaced: a daily cap resets, and a stranger with a resetting cap is unbounded
given patience. If the coupon below is ever built it **replaces** this path rather than joining
it; whichever exists is the host ceiling, never both.

*Two units, not two gates.* A price needs a rate on file and a token count never does, so the USD
arm alone cannot answer for a call `compute_usd` returns `None` for — it reads $0.00 for real
spend, and a gate that under-counts admits more. The token arm is the same ceiling asked in the
unit that survives, which is why the per-cycle `BudgetGate` has always run both. The USD arm falls
back to `Settings.UNPRICED_GRACE_USD` once an account's total is known to be a floor: a bound on
the blindness, never a price invented for it.

*Admitted whole, or refused.* A launch that declares more than the account can cover is refused —
it is **not** clamped down to the remainder, because a clamped launch starts, spends and halts
mid-campaign, which is the outcome the ceiling exists to prevent rather than to cause. So what a
run is admitted at is what it runs to, and no account ceiling moves under a campaign already in
flight. Declaring nothing declares the headroom.

*The one read-down is a delegate's grant.* A sub-principal's `spend_ceiling_usd` claim (ADR-0005
§5) is composed into the declaration rather than refused against it, and that IS a clamp with the
mid-campaign halt this rule otherwise forbids. It is accepted because the two ceilings answer
different questions: the account remainder is a wallet, which the operator can top up, while a
grant is an **authority bound** the delegate cannot argue with — refusing instead would leave a
delegate whose habit exceeds their grant unable to launch anything at all. Attenuation is the
point; the halt is its price.

*A run in flight holds its ceiling.* The admitted caps are stamped on the `Job`
(`JobRegistry.set_caps`) and subtracted from the next admission's headroom for as long as that job
runs; ending it releases them. Without the reservation, two concurrent launches are each admitted
against the same remainder and the pair spends double the ceiling — which `MACHINE_RUN_CAPACITY`
above 1 makes reachable.

*The residue is the operator's number.* Admission bounds what a run may declare, not what a round
boundary overshoots or what an unpriced call turns out to have cost, so `SpendCeilings.overrun`
answers what went past the ceiling anyway. Every refusal names it, and `/quota-status` never serves
it: the account sees its allowance, the host sees what the allowance failed to hold.

*Every ceiling-setting path composes here.* `change-spend-budget` writes the file `_usd_cap`
prefers over the launch-composed cap, so a gate at the launch seams alone is three quarters of one.
It **clamps** where a launch refuses (`clamp_budget_change`) — the campaign is already admitted, so
the only question is how far the operator may move its ceiling, and lowering one must always work.
It excludes the cycle's own reservation, or the cycle would be denied headroom it holds itself.

*And every path that composes one is READ by something.* Two ways of writing a ceiling nothing
would ever poll both ended in the same silence — the command acked `applied`, the number reached
the dashboard, and the run spent past it to completion. So the per-cycle gate is armed
**unconditionally** (`entry.py::_build_budget_gate`): a run that declared no ceiling may still be
given one mid-flight, and an unset arm costs nothing because `tripped` skips a `None` cap.
And the file is scoped to the run it was written in — `clear_run_control_flags` drops it with the
other polled flags, since it was clamped against the account as it stood at write time and
outliving its run would let a ceiling the account can no longer afford govern each later resume —
but it is the one polled flag that can carry a decision the run has not acted on yet, because
`change-spend-budget` applies to a PAUSED cycle too. The sweep therefore **returns what it
dropped** and the launch composes it through `_tighten_budgets` as a second `min`: it may tighten
the run and never raise it, the same bound a `CycleSeed` answers to. Reading it inside the sweep
rather than beside it is what stops the two drifting into the wrong order.

**A running ceiling lives in two homes, and one function writes both.** The JOB carries what the
account has committed while the run is in flight — the only home a mint has, since it reserves its
slot before its cycle exists — and `spend_cap.json` carries what the run may spend right now. An
absent arm on a change means "leave it alone", so each home needs a prior, and the two priors are
NOT interchangeable: the job's pair is complete from admission while the file's starts empty and
reads an untouched arm as unmetered. `quota.py::hold_ceiling` therefore takes the running job as
the single prior and writes the file as its projection; the file's shape is spelled once, by
`runtime_flags.py`'s `read_spend_caps` / `write_spend_caps` pair.

*Deleting the data does not un-spend the money.* The per-cycle ledgers ARE the lifetime record, and
`delete_campaign` takes them under both `keep_results` arms, so the ceiling was re-earnable by
deleting whatever you spent it on. `bank_spend` sums the subject first and writes a
`SpendTombstoneRecord` to the workspace ledger, which `account_ledgers` folds back in.
Totals, not rows: the rows are a chronology nobody can act on once their cycle is gone, and a
synthetic `TokenUsageRecord` standing in for them would be a fabricated measurement.

**Two destroyers, one bank, and it lives INSIDE them.** The stub delete takes a cycle tree the same
way — and a stub is deletable at `n_rounds == inherited`, which an origin-scored fork reaches having
already paid for round 0 — so `delete_campaign` and `try_delete_stub_cycle` each call `bank_spend`
themselves (`infrastructure/store/account_spend.py`, which is why that module sits in the store
layer rather than above it). No caller can take a ledger without banking it, and none is asked to
remember: `cleanup-empty-cycles`, `delete-cycle` and the runner's own cleanup all just delete.

The ordering inside each destroyer is the rest of the design. Banking runs AFTER the guard that
decides the delete will proceed and BEFORE the rows go: after the `rmtree` a crash loses the money
outright, and before the guard every refused sweep banks a cycle that keeps its rows. The residual
crash window therefore double-counts, which is the safe direction, and the re-bank guard closes it
— a subject (`campaign_id`, `cycle_id`) already carrying a tombstone is never banked twice.

**`reset` is the third path, and the bank stays inside the store.** The host-only CLI verb removes
each tenant's whole `campaigns/` tree without going through either destroyer, so it asks the store
to bank first — `bank_all_before_removal`, which states its precondition in its own name, because
banking a subject that KEEPS its rows counts the money twice. The walk sits beside the destroyers'
own calls rather than in the CLI: a caller pairing ledgers with a campaign_id its own way is a third
spelling of that pairing, free to drift from how a delete does it. This is the worst path to forget
on — it takes `--all-tenants`, which walks every tenant on the box, so an unbanked reset re-earns
every account's ceiling in one gesture — and the re-bank guard is what makes running it twice safe.
`reset` drops the data, never the money.

*A request may lower a ceiling and never raise one.* A `CycleSeed`'s `config_overrides` wins over
run-scoped values for every policy knob it carries, but the budget arms are an authority bound
rather than a preference, so `runner/entry.py::_bound_by_admitted_caps` composes them as a `min`
against what the wallet admitted. The seed arrives over `fork-cycle` as request input from anyone
holding `campaign.run` — which signup grants — so an override there is a stranger naming the
ceiling their own run halts on.

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
| Spend resolution | `promptpotter/shared/pricing.py` |
| Token emit (optimizer) | `promptpotter/application/optimization/dispatch/llm_call/call.py` |
| Token emit (backend) | `promptpotter/application/scoring/sample_measurement.py` |
| Token emit helper + ContextVars | `promptpotter/infrastructure/llm/telemetry.py` |
| ContextVar lifecycle | `promptpotter/application/run_observers.py` |
| Token shape | `promptpotter/domain/run_records.py` |
| Sole spend writer + halt accessor | `promptpotter/infrastructure/projections/live_dashboard/view.py` |
| Bucket shapes + cycle totals | `promptpotter/infrastructure/projections/live_dashboard/state.py` |
| Budget config | `promptpotter/application/campaign_config.py` |
| Stop reason | `promptpotter/domain/phases.py` |
| Budget probe + halt | `promptpotter/application/runner/entry.py` |
| CLI flag | `promptpotter/presentation/cli/parsers.py` |

### Cross-refs

- [`0002-identity-foundation.md`](0002-identity-foundation.md) — **the foundation this ADR consumes.** The two contracts (OIDC wire + RLS data), the `IdentityContext` shape, the three-stage staging, the no-drift gates. Read it first.
- [`0001-m12-control-plane.md`](0001-m12-control-plane.md) — Stage-1 OIDC client, `JobRegistry` identity-scoping, hub mode (next consumer of identity-foundation).
- [`../specs/roadmap.md`](../specs/roadmap.md) — `Install` is the user-facing name for `TenantId` (mapped onto OIDC `iss`); vocabulary shifts, on-disk identity unchanged.
- [`../specs/roadmap.md`](../specs/roadmap.md) — Phase 1 dependency (identity collapse touches the same store-seam files).
