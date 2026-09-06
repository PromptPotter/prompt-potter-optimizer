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

[`0002-identity-foundation.md`](0002-identity-foundation.md) decides the identity contract (OIDC wire + RLS data + SCIM model). Its Stage-0 deliverable — the `IdentityContext` seam — needs a real payload riding it before downstream consumers ([`0001-m12-control-plane.md`](0001-m12-control-plane.md), [`../specs/roadmap.md`](../specs/roadmap.md)) build on it. Spend tracking is the chosen first payload: every LLM call, optimizer-loop and backend alike, already emits one record per call, so the only open question is *what path that record takes through the system*.

Two questions resolve here. **(1)** Does spend ride the canonical per-cycle ledger (`.runtime/ledger.jsonl`) like every other record, or a parallel pipeline? **(2)** Does identity attach to each record, or ride the cycle dir's tenant prefix?

## Decision Drivers

* **Canonical ledger only.** §0 names Persistence as the sole writeable I/O kind for state; spend is state. A `spend.json` parallel pipeline duplicates the ledger and bypasses the audit trail every other record gets.
* **Sole writer per surface.** Every projection has exactly one writer (`LiveDashboardView` for `dashboard.json`, `AuditTrailView` for `round_NNNN.json`). Spend must not invent a parallel one.
* **Kwargs-only emit-helper template.** ContextVar-scoped, no wrapper dataclass, builds the record inline — the shape every future per-call telemetry kind copies.
* **Identity from path, not per-record.** The per-cycle ledger already sits under `projects/{tenant_id}/`; the OS-enforced prefix is the ground truth.
* **Halt probe must be clean.** Spend-budget enforcement reads the dashboard's `spend_total_used_usd` accessor — no `state["spend"]` peek, no dict reach-through.

## Considered Options

* **A: Canonical ledger via `emit_token_usage` over `_CYCLE_LEDGER` ContextVar + sole `LiveDashboardView` writer.**
* **B: Process global `_token_usage_sink`** — the sink batches and flushes on its own schedule.
* **C: Wrapper dataclass `TokenUsage` + a separate `apply_token_usage` chain.**
* **D: Separate `SpendProjection` + `spend.json`** — its own projection, file and poll endpoint.
* **E: Per-record `tenant_id` on `TokenUsageRecord`** — aggregation joins on the field.

## Decision Outcome

Chosen: **A.** Tokens ride the canonical per-cycle ledger alongside every other record. The "highway" is the existing Persistence stream, and this arc promoted the path tokens take through it to the optimal sequence by eliminating four middlemen — the process global (B), the wrapper dataclass (C), the dual writer (D) and the multi-hop apply chain. Both cost routes, backend-LLM and optimizer-loop, flow through the same ledger as `TokenUsageRecord` distinguished by `kind`. `AuditTrailView` records them into `round_NNNN.json`; `LiveDashboardView._handle_token_usage` projects them into `dashboard.json::spend` and is sole writer. Identity scope rides the ledger path — no per-record `tenant_id`.

The halt probe at `application/runner/entry.py` reads `observers.dashboard.spend_total_used_usd`. Operator-accepted Display→Control short-circuit: the dashboard owns spend semantics, so reading it back is not a parallel pipeline.

### Consequences

* **Good** — per-asyncio-task isolation comes free from the `_CYCLE_LEDGER` ContextVar; concurrent cycles isolate without ceremony.
* **Good** — audit trail shape identical to every other record; one stream of truth.
* **Good** — `emit_token_usage` becomes the template every subsequent per-call telemetry kind copies (`emit_command`, `emit_command_ack`, future `emit_*`). Forward direction: every other `RunCallbacks.on_*` that wraps a per-call event in a `*Record` and appends is a candidate for the same shape — catalogued in [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md).
* **Good** — adding new `*Record` types is additive; no schema churn elsewhere.
* **Neutral** — `LiveDashboardView` owns spend semantics. Operator-accepted: it is the authoritative rollup, and the halt probe just reads it back.
* **Bad** — none on disk; the arc shipped clean.

### Why the others lost

* **B** — concurrent cycles collide on the global, and flushes desynchronize from cycle teardown, so resume can lose unflushed events.
* **C** — three sites maintain one shape (emit → wrap → apply) and divergence is silent.
* **D** — a parallel pipeline by construction; `dashboard.json` and `spend.json` race during cycle close.
* **E** — duplicates the cycle dir's prefix on every record, and invites events from multiple tenants in one ledger, exactly the leak the prefix prevents structurally.

### Confirmation

1. **Seam holds.** Gates #3 / #4 / #6 from [`0002-identity-foundation.md`](0002-identity-foundation.md) ride the typed `IdentityContext` seam this ADR reifies — a wrong `build_stores` signature fails to typecheck — plus review; no standing test ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).
2. **Halt probe round-trip.** Live campaigns demonstrate halt-at-budget with no `state["spend"]` peek anywhere in the codebase.
3. **Audit trail integrity on disk.** `round_NNNN.json` carries `TokenUsageRecord` entries and `dashboard.json::spend` renders bar, publication and `log.md` from one rollup, with no divergence between display and audit.

## More Information

### The lesson this ADR bought

Wire cost is extracted where the provider is spoken to — `infrastructure/llm/openai_compat.py::_attempt_cost` onto `LLMResponse.cost_usd` — and `call.py` passes it as the `cost_usd=` kwarg. **This line read "extracted in `call.py`" until 2026-08-13**, and `call.py` did read a `usage["cost"]` key the client never wrote, so the field was `None` on every optimizer call ever recorded. An ADR asserting a mechanism shipped is why three downstream surfaces explained the symptom as the provider sending no cost. An ADR states the decision; whether the code matches it is checked in the code.

### Host coupon + BYO keys — the per-user wallet gate (Lane A2)

The shipped spend feature *measures* cost; it does not *bound a user against the host's wallet*. Two concerns stay clearly separated: **wallet protection** (the host's money) is the coupon, metering host-key spend only, while **abuse protection** on a shared machine is concurrency + campaigns/day + rate-limit (`application/jobs/quota.py`, `JobRegistry`) — key-source-agnostic and orthogonal.

Three decisions outlive the build; the shapes and status are [`../specs/roadmap.md`](../specs/roadmap.md) § Host coupon + BYO per-user API keys.

- **`TokenUsageRecord` gains `key_source: "host" | "user"`** — the *one* allowed exception to identity-from-path (option E), because `key_source` is not identity but which wallet paid, and the coupon math needs it on the record. Declared on `TokenUsagePayload` in [`../specs/events-asyncapi.yaml`](../specs/events-asyncapi.yaml).
- **Coupon remaining is derived from the ledger**, never a decrementing counter — one source of truth, consistent with option A.
- **`resolve_api_key(identity, provider, stores)` is the one choke point**, wrapping the identity-free `get_llm_client`. A user's own key for this provider wins with no coupon check (their money); else a live coupon uses the host key; else `HostAllowanceExhaustedError` → 422 `host_allowance_exhausted`, distinct from 422 `no_api_key` (no user key *and* no host key at all). The short-circuit is **per provider**, so a user with a key for X still burns the coupon on Y unless `settings.coupon_void_on_byo` retires it.

The `/auth/api-keys` + `/auth/coupon` verbs ride the **auth router** — account-scoped siblings of `/auth/{quota-status,user-settings}` — not the control-plane [`api-openapi.yaml`](../specs/api-openapi.yaml), whose scope is the closed `/commands/*` set. Only the event-surface change is asyncapi-declared.

**D1 — ONE host-wallet gate, expressed in two units; whichever trips first.** Two *mechanisms* guarding one concern is the "no redundant mechanism" rule (root `CLAUDE.md`), so there is exactly one: `admit_launch` composing `Settings.FREE_TIER_SPEND_CAP_USD` / `FREE_TIER_TOKEN_CAP` (overridable per account at `User.spend_budget_usd_total` / `token_budget_total`) against what the account has used over its WHOLE ledger. It became load-bearing when signup stopped requiring approval — it is now the only thing standing between a stranger and the host's provider key, which is why it is a lifetime allowance rather than the per-day one it replaced: a daily cap resets, and a stranger with a resetting cap is unbounded given patience. If the coupon is ever built it **replaces** this path rather than joining it; whichever exists is the host ceiling, never both.

*Two units, not two gates.* A price needs a rate on file and a token count never does, so the USD arm alone cannot answer for a call `compute_usd` returns `None` for — it reads $0.00 for real spend, and a gate that under-counts admits more. The token arm is the same ceiling asked in the unit that survives, which is why the per-cycle `BudgetGate` has always run both. The USD arm falls back to `Settings.UNPRICED_GRACE_USD` once an account's total is known to be a floor: a bound on the blindness, never a price invented for it.

*Admitted whole, or refused.* A launch that declares more than the account can cover is refused, **not** clamped to the remainder, because a clamped launch starts, spends and halts mid-campaign — the outcome the ceiling exists to prevent rather than to cause. What a run is admitted at is what it runs to, and no account ceiling moves under a campaign in flight. Declaring nothing declares the headroom.

*The one read-down is a delegate's grant.* A sub-principal's `spend_ceiling_usd` claim (ADR-0005 §5) is composed into the declaration rather than refused against it, and that IS a clamp with the mid-campaign halt this rule otherwise forbids. The two ceilings answer different questions: the account remainder is a wallet the operator can top up, while a grant is an **authority bound** the delegate cannot argue with — refusing instead would leave a delegate whose habit exceeds their grant unable to launch anything at all. Attenuation is the point; the halt is its price.

*A run in flight holds its ceiling.* Admitted caps are stamped on the `Job` (`JobRegistry.set_caps`) and subtracted from the next admission's headroom until that job ends. Without the reservation, two concurrent launches are each admitted against the same remainder and the pair spends double. A launch that has reserved but not yet stamped is the same hole with a shorter window, so an account with an earlier unstamped sibling is refused rather than quoted (`quota.py::_outstanding_reservations`). A QUEUED launch holds nothing in either sense.

*The residue is the operator's number.* Admission bounds what a run may declare, not what a round boundary overshoots or what an unpriced call turns out to have cost, so `SpendCeilings.overrun` answers what went past the ceiling anyway. Every refusal names it, and `/quota-status` never serves it: the account sees its allowance, the host sees what the allowance failed to hold.

*Every ceiling-setting path composes here.* `change-spend-budget` writes the file `_usd_cap` prefers over the launch-composed cap, so a gate at the launch seams alone is three quarters of one. It **clamps** where a launch refuses (`clamp_budget_change`) — the campaign is already admitted, so the only question is how far the operator may move its ceiling, and lowering one must always work. It excludes the cycle's own reservation, or the cycle would be denied headroom it holds itself.

*And every path that composes one is READ by something.* Two ways of writing a ceiling nothing would ever poll both ended in the same silence — the command acked `applied`, the number reached the dashboard, and the run spent past it to completion. So the per-cycle gate is armed **unconditionally, and before the first sample is scored** (`entry.py::_arm_run_controls`, the one owner, called from `_prepare_run` ahead of the origin pass and again per fork iteration): a run that declared no ceiling may still be given one mid-flight, and an unset arm costs nothing because `tripped` skips a `None` cap. Arming only at the round loop left round 0 — the longest stretch of a fresh launch — spending against a `budget_tripped` that was still `None`. The file is scoped to the run it was written in (`clear_run_control_flags` drops it with the other polled flags, since it was clamped against the account as it stood at write time), but it is the one polled flag that can carry a decision the run has not acted on yet, because `change-spend-budget` applies to a PAUSED cycle too. The sweep therefore **returns what it dropped** and the launch composes it through `_tighten_budgets` as a second `min`: it may tighten the run and never raise it. Reading it inside the sweep rather than beside it is what stops the two drifting into the wrong order.

**A running ceiling lives in two homes, and one function writes both.** The JOB carries what the account has committed while the run is in flight — the only home a mint has, since it reserves its slot before its cycle exists — and `spend_cap.json` carries what the run may spend right now. An absent arm on a change means "leave it alone", so each home needs a prior, and the two priors are NOT interchangeable: the job's pair is complete from admission while the file's starts empty and reads an untouched arm as unmetered. `quota.py::hold_ceiling` therefore takes the running job as the single prior and writes the file as its projection; the file's shape is spelled once, by `runtime_flags.py`'s `read_spend_caps` / `write_spend_caps` pair.

*Deleting the data does not un-spend the money.* The per-cycle ledgers ARE the lifetime record, and `delete_campaign` takes them under both `keep_results` arms, so the ceiling was re-earnable by deleting whatever you spent it on. `bank_spend` sums the subject first and writes a `SpendTombstoneRecord` to the workspace ledger, which `account_ledgers` folds back in. Totals, not rows: the rows are a chronology nobody can act on once their cycle is gone, and a synthetic `TokenUsageRecord` standing in for them would be a fabricated measurement.

**Three destroyers, one bank, and it lives INSIDE them.** The stub delete takes a cycle tree the same way — and a stub is deletable at `n_rounds == inherited`, which an origin-scored fork reaches having already paid for round 0 — so `delete_campaign` and `try_delete_stub_cycle` each call `bank_spend` themselves (`infrastructure/store/account_spend.py`, which is why that module sits in the store layer rather than above it). The third is `delete_inner_sandbox`, which the campaign delete, the orphan reaper and `reset` all route through: an L4 sandbox is a SIBLING of the tenant tree, so no account-wide walk reaches it and nothing else would ever bank what it holds. No caller can take a ledger without banking it, and none is asked to remember: `cleanup-empty-cycles`, `delete-cycle` and the runner's own cleanup all just delete.

**A subject may hold money that already reached another ledger, so the bank takes the RESIDUE.** An inner cycle forwards onto its outer cycle as it runs and records how far it got; summing its rows whole on the way out would bill that money twice, and a tombstone is indistinguishable from spend that reached nowhere else. Absent mark ⇒ nothing forwarded, so every ordinary cycle banks exactly as before.

The ordering inside each destroyer is the rest of the design. Banking runs AFTER the guard that decides the delete will proceed and BEFORE the rows go: after the `rmtree` a crash loses the money outright, and before the guard every refused sweep banks a cycle that keeps its rows. The residual crash window double-counts, which is the safe direction, and the re-bank guard closes it — a subject (`campaign_id`, `cycle_id`) already carrying a tombstone is never banked twice.

**`reset` takes TWO trees, and the bank stays inside the store for both.** The host-only CLI verb removes each tenant's whole `campaigns/` tree without going through a destroyer, so it asks the store to bank first — `bank_all_before_removal`, which states its precondition in its own name, because banking a subject that KEEPS its rows counts the money twice. The walk sits beside the destroyers' own calls rather than in the CLI: a caller pairing ledgers with a campaign_id its own way is a third spelling of that pairing, free to drift. Its second tree is the off-tree sandboxes, routed through `delete_inner_sandbox` rather than re-spelled: a per-tenant walk cannot reach a sibling of `projects/`, and a sandbox left standing is silently CONTINUED by the next content-addressed run instead of re-minted, so the reset that looked clean was not. A sandbox whose `owner.json` names no valid workspace is KEPT and reported — there is nothing to bank it into, and this verb acts on a fact. This is the worst path to forget on — it takes `--all-tenants`, which walks every tenant on the box, so an unbanked reset re-earns every account's ceiling in one gesture — and the re-bank guard is what makes running it twice safe. `reset` drops the data, never the money.

*A request may lower a ceiling and never raise one.* A `CycleSeed`'s `config_overrides` wins over run-scoped values for every policy knob it carries, but the budget arms are an authority bound rather than a preference, so `runner/entry.py::_bound_by_admitted_caps` composes them as a `min` against what the wallet admitted. The seed arrives over `fork-cycle` as request input from anyone holding `campaign.run` — which signup grants — so an override there is a stranger naming the ceiling their own run halts on.

**D2 — live, not a mint-time snapshot.** The per-cycle `BudgetGate` (`application/runner/termination.py`) reads coupon-remaining, re-summed from the host-key ledger every tick, instead of a launch snapshot — closing the liveness gap. New `StopReason.HOST_ALLOWANCE`.

### §0 amendment?

**No — this ADR.** The Stage-0 `IdentityContext` reification refines the existing **Persistence** I/O kind (tenant-prefix on every store key), and the seam rides existing wiring. The §0 amendment for the new `Identity` I/O kind lands with **Stage 1 of [`0002-identity-foundation.md`](0002-identity-foundation.md)** (OIDC ingress at the API boundary) — see that ADR's "§0 amendment" section.

### Cross-refs

- [`0002-identity-foundation.md`](0002-identity-foundation.md) — **the foundation this ADR consumes.** The two contracts (OIDC wire + RLS data), the `IdentityContext` shape, the three-stage staging, the no-drift gates. Read it first.
- [`0001-m12-control-plane.md`](0001-m12-control-plane.md) — Stage-1 OIDC client, `JobRegistry` identity-scoping, hub mode.
- [`../specs/roadmap.md`](../specs/roadmap.md) — Lane A2 status, and `Install` as the user-facing name for `TenantId` (a vocabulary shift; on-disk identity unchanged).
