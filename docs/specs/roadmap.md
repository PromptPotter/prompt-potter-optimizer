# Roadmap

> **Beta.** Forward todo in execution order; this file absorbs the per-milestone specs (git log holds their full prose). The two `m12-*.yaml` files + the ADRs are the only other live contracts.
>
> **Live now:** deployed at `https://app.promptpotter.dev` (Cloudflare Tunnel + systemd, OIDC + allowlist — see [`deploy-linux/`](../../deploy-linux/README.md)). Allowlist-gated but internet-reachable, on **one shared LLM key** from `.env` — so the sequence below is "harden a thing already serving users," not "prep before launch."

## Hard ordering (violate → rebuild)

- **state-sync P3 (`GET /api/v1/sessions/active/live-state`) before any new webapp data panel** — chat state-queries, composite-fitness scatter, cross-user panel. Anything on `dashboard.json` polling is rewritten at the cutover; substrate-free rollups are exempt. *(P3 itself shipped — this guards new panels built on the old seam.)*
- **Host coupon + BYO per-user API keys — overdue, not a future gate.** The beta serves allowlisted users on one shared `.env` key today; every user spends the operator's quota with no per-user ceiling. The fix: a per-user **coupon** (host-key spend up to a fixed size + expiry), then **BYO keys** to continue on their own money. Present liability → Lane A2.
- **HTTP-edge abuse protection scales with the allowlist.** Cloudflare edge + allowlist + per-user `JobRegistry` quotas bound the public surface now; app-level rate-limiting (C6) is due the moment the allowlist is removed.

Historical (satisfied): spend shipped before composite/state-sync; identity Stage 0 + 2nd connector shipped; control plane + identity Stage 1 shipped → the chat write-path is unblocked, not gated. Any new endpoint is multi-tenant by default.

## Lane 0 — daily hygiene

Drain before feature work: [`code-debt-cleanup`](code-debt-cleanup.md).

## Sequence

Sequenced into lanes by dependency, not milestone number. **Front priority = Lane A + the publication lane, concurrent** (no shared seam). Lane B is closed; Lane C follows A.

### Lane A — beta usable for allowlisted web users, end-to-end

| # | Item | Status |
|---|---|---|
| A2 | Host coupon + BYO per-user API keys | pending — **overdue** (see § Host coupon + BYO); token HQ at `/auth/{quota-status,activity}` already shipped |

### Lane B — foundations (closed)

state-sync P1–P4 shipped 2026-05-30 (per-cycle `dashboard.json`, `GET /api/v1/sessions/active/live-state`, sidecar delete). See § State-sync.

### Lane C — product differentiator + capability (after A)

| # | Item | Status |
|---|---|---|
| C1 | **Chat-first front door** — one thread: ingest/check-in → curated activity stream → inline decision buttons (existing verbs). | **Arc 1 shipped** (curated activity + SSE consumer + in-thread loop control, origin gate folded in); Arc 2 (conversation endpoint) deferred — [`chat-foundation.md`](chat-foundation.md) |
| C2 | Composite fitness P2–P4 (P1 = spend, done) — data rollup anytime; **scatter panel after P3** | pending (see § Connectors + L4) |
| C3 | L4 closure — inner-cycle dispatch + the L4 campaign + `proxy_lift_corr ≥ 0.6` re-validation (connector registered) | pending (see § Connectors + L4) |
| C4 | Cross-user measurement panel (after P3) | pending (see § Ingest + chat-first web) |
| C5 | MCP server mode · user-editable `pipeline.json` in UI | pending |
| C6 | Public-service hardening (Docker, metrics, rate-limit, billing) — `/health` shipped; **pull rate-limit/metrics forward if the beta opens past the allowlist** | pending |
| C7 | Non-prompt targets + evolutionary operators · multimodal · research extensions | pending |
| C8 | **Mask abstraction** — backend organizing structure (alternative-criterion + transferability); M1 = scoring-function-swap divergence + minimal visual clues, then migrate every divergence trigger onto it | M1 + abort shipped (see § Lineage mask) |

**Parallel lane — publication (front, concurrent with Lane A).** Engine exit gate (`rounds_to_95`) shipped → this is *running experiments + write-up*: BBEH primary (headroom on `gpt-oss-120b`), HotPotQA pending a saturation probe, GSM8K/AIME deprioritized; 3 seeds + Wilson CIs + McNemar vs CAPO/DSPy; ablation rows L1 / L1+L2 / full · scan · SearchMemory · critique · zero-signal-filter. Competitor + L4 numbers wait on C3. **Verify the [BBEH score anomaly](../research/) before publishing.** Endpoint hardening P0 (auth dep on every router, pinned `ALLOWED_ORIGINS`, `extra=forbid` on request models, poll rate-limit) lands before any non-localhost open.

**Far-horizon (unscheduled).** Synthetic dataset from one hold-out question (removes the dataset-provision requirement; the real metric is synthetic→real transfer of *optimizer lift*, anchored on the single genuine hold-out) · AlphaEvolve code-harness · L3 fork authority → AlphaZero-shaped MCTS over the lineage.

## Permanent contracts (constitutions, not steps)

- **Identity foundation** — OIDC wire + PostgreSQL RLS; three-stage staging. → [`ADR-0002`](../adr/0002-identity-foundation.md)
- **Spend + tenancy** — `TokenUsageRecord` on the canonical ledger via `emit_token_usage`. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md)
- **Control plane** — Control-remote I/O kind; closed in/out sets ([`m12-api-openapi.yaml`](m12-api-openapi.yaml) + [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml)). → [`ADR-0001`](../adr/0001-m12-control-plane.md)
- **Frontend surface** — per-control behavior per auth/data state. → [`frontend-surface-contract`](frontend-surface-contract.md)
- **Verdict resolution** — the statistical model behind the live adaptive queue + `hard_samples_*.json`. → [`verdict-resolution`](verdict-resolution.md)

---

## Design notes (folded specs)

Terse landing for the per-milestone specs that were consolidated here. Status is truth; full original prose is in `git log`.

### Origin-resolution check-in — SHIPPED 2026-05-30
LLM proposer + deterministic readiness gate resolve a messy CSV into a complete origin (no hidden defaults, no literal-column requirement); `high`-confidence fields auto-promote `proposed→confirmed` before mint. Non-derivable kernels: reuses the `checkin/2` node (no separate `origin_resolve` node/model); **deliberately off the operator surface** — `reasoning_floor/ceiling` (backend-node-only) and `model_locked` (= `forbidden_axes_strict`, a dev policy). Concept: root `CLAUDE.md` § Origin & check-in; mechanics in `git log`.

### Ingest + chat-first web — partially shipped (Ingest Slice 1 done; chat Arc 1 done — activity + control)
> **Chat-first front door** (thread model, activity-stream translator, copilot decision
> buttons, campaign-scoped persistence) has its own contract: [`chat-foundation.md`](chat-foundation.md).
> This note keeps only the ingest / draft-campaign detail.

Four nouns map to OIDC: Install=`iss`, User=`sub` (`user_id=f"{iss}:{sub}"`, SCIM 2.0 Core names verbatim), Project=`tenant_id` claim (today's `datasets/{name}/`), Campaign=cycle 1:1.
- **The committed artifact is a Dataset, not a campaign:** 4 content-hashed files at `projects/{tenant}/datasets/{slug}/` (`cache.json` rows, `pipeline.json` overlay, `task_description.md`, `prompts/default.json`) compose into `JobSearchPoint.content_hash`; the sibling `campaign.json` is NOT in the hash. Identical datasets → identical `cycle_{target_hash[:12]}` + shared `archive/measurements/` (free cross-tenant pooling).
- **Draft-campaign object:** `DraftCampaign` negotiates both the Dataset and campaign config; smart defaults `connector=termnorm`/`exact_match`/`max_rounds=5`; model + `reasoning_effort` resolved from the dataset-reasoning-matrix at *commit*, not pinned on the draft. Chat + panel are two views over one server-side draft, synced via `edit-draft-campaign` + SSE `DraftUpdatedRecord` (declare in asyncapi before the handler).
- **Endpoints:** `POST /datasets/ingest` (multipart; 409 `slug_collision`→`{slug,suggested_slug}`, version-and-repoint Replace never overwrites; 422 parse). `GET /datasets` flat list with `tier: yours|benchmark` (benchmark gated by `datasets.benchmarks.read`, Stage-0 via `PROMPTPOTTER_ADMIN=1`). **Durable check-in (shipped):** ingest mints a real disk-backed campaign in the `checkin` lifecycle on the first action (the draft's `draft_id` IS the `campaign_id`, working state under `campaigns/{id}/checkin/` via `CheckinDraftStore` — no in-memory registry); it shows in the sidebar + survives a restart. Start path = `start-checkin` (gate → commit dataset → flip `checkin`→`active` → run); the CLI `new <file>` shares the commit/flip body (`prepare_checkin_run`) and runs inline.

### Connectors + L4 inner-cycle execution — partially shipped (boundary + TermNorm + self-connector + control plane + composite-P1 done; L4 run + composite P2–P4 + competitor numbers open)
- **Connector contract:** `Connector` dataclass (`connectors/protocol.py`), 3 hooks `wire_adapter`/`session_factory`/`extract_experiment`; `backend_type` read from `pipeline.json`, never hardcoded.
- **Execution mode (the L4 self-recursion seam):** `Connector.execution = remote_http (default) | in_process`; `BackendClient.run_query` dispatches on the *declared mode*, never the connector name. The `promptpotter` connector declares `in_process` → raises a pointed `NotImplementedError` until Lane C3. C3's open choice: localhost `POST /inner/matches` vs in-process dispatch to `runner.run_optimization` under `.runtime/inner/`. Concept: [`optimizer-of-the-optimizer`](../concepts/optimizer-of-the-optimizer.md).
- **Composite fitness phases:** P1 surface (done) · P2 per-candidate rollup + scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate` · P4 Pareto-PoBB (stretch).
- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types + L1/critique injection-echo validators + a repeat-detection circuit breaker.

### Prompt-iteration framework + exit gate — partially shipped (gate not yet met)
- **Exit gate:** `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`; `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches (or modify the rules).
- `_mint_fork` (`resume_and_fork/fork_siblings.py`) is the single entry for all 7 `ForkTrigger` variants (one `ForkSpec` + `CycleSeed`); L2/L3 auto-rebase capped at `MAX_AUTO_REBASES = 10`/invocation, gated by `OptimizationConfig.rebase_capability`.
- **Round-1 verdict (conformance-anchored):** 0 ✗ → healthy · 1 ✗ → degraded · ≥2 ✗ → broken; behavior checks are pure `(round_dict, ctx) → CheckResult`. (Model/provider locking is not a behavior check — it's the single `forbidden_axes_strict` bit at the schema surface + the `validate_overrides` backstop.) The Track-7 L2 self-diagnosis rule turns a missing `evidence_grounding` citation into an L2 `task_context` nudge.
- Sweep results: `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/{verb}_{ts}.json`; `sweep rank` keys `(l1_generate_hash, rounds_to_95 asc, behavior_pass_rate desc)`. Live mechanism = `/potter-l1-meta-campaign`.

### Host coupon + BYO per-user API keys — spec-only (Lane A2)
Full contract: [`ADR-0003`](../adr/0003-spend-and-tenancy.md) § Host coupon. The host runs users on its own keys up to a **coupon** (fixed USD size + expiry, per user); past it, a user uploads their **own** key and continues on their own money. Two separated concerns: the **coupon** protects the host wallet; concurrency/rate-limit (`jobs/quota.py`) protects the machine — untouched.
- **Coupon (`grant.json`):** per user at `projects/{tenant}/grant.json` = `{amount_usd, issued_at, expires_at}`; remaining is **derived from the host-key ledger**, not a counter. Install defaults (size, validity window, `coupon_void_on_byo`) in `config/settings.py`.
- **Ledger:** `TokenUsageRecord` gains `key_source: host|user` so host-key spend sums separately (declared on `TokenUsagePayload` in the asyncapi).
- **Resolution order (one choke point):** (1) user has own key for provider → use it, **no coupon check** (their money); (2) else coupon alive → host key, metered to coupon; (3) else → `HostAllowanceExhaustedError` (**422 `host_allowance_exhausted`**, "add your own key"). Step 1 short-circuits per provider. The separate **422 `no_api_key`** = no user key *and* no host key configured. `get_llm_client(provider, *, api_key=None)` stays identity-free; `resolve_api_key(identity, provider, stores)` is the wrapper called from `application/config.py::create_llm_client` + `origin_resolve.py::resolve_origin_turn`.
- **BYO store:** `TenantApiKeyStore` at `projects/{tenant}/api_keys.json` — Fernet ciphertext (`SECRETS_FERNET_KEY`, no plaintext fallback) + plaintext `providers_set` index; key never echoed/logged/traced. On `PUT`, `coupon_void_on_byo` decides whether the remaining coupon dies or persists — host's choice.
- **Gate is live:** the per-cycle `BudgetGate` reads coupon-remaining (re-summed every tick), new `StopReason.HOST_ALLOWANCE` — closes the "launch-snapshot only" gap. **D1:** the coupon replaces the daily-cap path (`effective_spend_cap_usd`/`spend_budget_usd_daily` deleted — one wallet gate, not two).
- **Verbs (auth router):** ride the **auth router** as siblings of the shipped `/auth/{quota-status,user-settings,activity}` (account-scoped, NOT control-plane `/commands/*` — so NOT in `m12-api-openapi.yaml`, whose scope is the closed command set): `PUT/DELETE /auth/api-keys/{provider}` (204), `GET /auth/api-keys` (`providers_set` only), `GET /auth/coupon` (remaining + expiry); `GET /llm-providers` gains `key_source: user|host|none`. Only the event-surface change (`key_source` on `TokenUsagePayload`) is openapi/asyncapi-declared. **BYO lifts the host coupon; the abuse guards still apply.**

### Operator-steered fork — SHIPPED
Rides the existing `fork-cycle` command (no new verb); payload extended to `{from_searchpoint, pipeline_overlay, origin_prompt_fields, limit_overrides, steered_by}`. `fork-cycle` **mints then launches** (minting alone left web forks idle). The override seed is written to the fork's own cycle dir (`.overrides/seed.json`, read once at the runner seam via `CycleOverrideMixin`); origin resolves fork-seed-first; no dataset-origin mutation. `max_rounds` is an absolute target (the fork's counter continues from the parent), reconciled consumed-vs-remaining in the dialog.

### State-sync — SHIPPED 2026-05-30
Collapsed 5 state surfaces → 2: `active_session.json` (ground truth for what's running: `{tenant_id, session_id, cycle_id}`, sole writers mint/fork/sweep_restore via `save_active_pointer`) + per-cycle `dashboard.json` (live read-out, ≤0.25s debounce + synchronous round-boundary flush). **Teardown-only design was rejected — do not re-propose** (it reverses the folder-UI §0 commitment). Directory name IS the cycle id (`index.json::campaign_id` removed). Run-state is owned: the runner declares `running/paused/stopping/terminal` as a `control` `PhaseRecord` → `dashboard.json::run_phase` (`RunPhase`, `domain/phases.py`), read off the 2s poll via `derive_run_phase`; the old `/runstate` probe is gone.

### Run admission + concurrent serving — partially shipped (capacity-1 admission seam) / spec-only (N>1)
Multiple allowlisted users share one process (`uvicorn --workers 1`) that runs campaigns **in sequence**. A launch while a run is in flight is rejected with 409 `machine_busy`; the webapp surfaces it as a critical-alert banner before the click.
- **As-built (shipped):** `JobRegistry.reserve` is an atomic, lock-guarded count-then-claim — the slot count read and the pending-reservation write share one lock with no `await` between, so two near-simultaneous launches can't both pass (the launch race is closed). `capacity = settings.MACHINE_RUN_CAPACITY` (1 today) is the lever; the per-user gates (`check_launch_quotas`) stay orthogonal. Operator surface: 409 `machine_busy` (holder presence record on `details`) + the `GET /machine-status` poll → `CriticalAlertBanner` (`machine_holder` excludes self, so the banner is cross-user).
- **Open / gated — `capacity > 1` (serve N at once):** hard predecessors are **Lane A2 (BYO per-user keys)** + a **per-tenant `RateLimiter`** (today process-global at `infrastructure/llm/rate_limit.py`) + backend (TermNorm) throughput. The admission seam is the single lever; the isolation is the blocker — raising capacity before these cross-bills the shared key and throttles everyone.
- **Open — durable cross-process lock:** the in-process `threading.Lock` only guards one process. `--workers > 1` or web↔CLI mutual exclusion (CLI `new`/`resume` never register a `Job`) needs a disk CAS slot file (`O_EXCL` create + heartbeat + stale-reclaim, mirroring the existing `server_restart` stale sweep). Same `capacity` knob; durable substrate.

### Lineage mask — M1 + abort verdict shipped (read-side); write-side deferred
Full design: [`mask-projection.md`](mask-projection.md). A **mask** is a **backend organizing abstraction** — one uniform structure for "an alternative criterion over the campaign's computed data, and what transfers vs what doesn't." Its purpose is backend cleanliness (it unifies the scattered what-if / order-collision / constraint / projection treatments); it's **background infrastructure** (the operator needn't know it exists), and any display is a thin downstream consumer. Scoring stays backend ([R-36]). The realized tree is **the record**; applied to it, a mask partitions data into **invariant** (carries over unchanged) vs **divergent** (counterfactual); the fork is the **divergence point** (emergent), and the divergent descendant subtree renders dimmed.
- **The shape:** one shared tree-recursive fold `find_divergences(record, verdict) -> {divergences, divergent}` (a read-time `application/` service the API calls, [R-14]); each mask is a **verdict strategy** in its home; `_resolve_verdict(lens)` (`routers/campaigns/lineage.py`) is the thin API-edge selector (function + strategy, not an internal switch, not a class hierarchy). No persistence (criterion is a request param). "Mask" is internal naming.
- **Milestone 1 (foundation) — SHIPPED:** the **scoring verdict** re-runs the realized election under a swapped *formula*, re-scoring each entity from its **stored evaluator namespace** (`value_with_mask_applied = compile_round_scorer` over those values — schema-free; `PipelineSchema` is never persisted) and reproducing the realized composite exactly; divergence = first round the re-elected leader ≠ `is_winner`, **tree-recursive**; **self-consistency by construction** (realizing formula reproduces `is_winner`, eligibility filter verbatim). Served on `GET /lineage?lens=score:<formula>`; frontend = a "Lens" selector + divergence marker + dimmed divergent subtree.
- **First migration — abort mask (second consumer, proves the fold) — SHIPPED:** a different verdict (log-read over `MaskCandidate.abort`, classified from `elimination_context.leader_locked`) on the *same* fold — `make_abort_verdict(suppress ⊆ {epsilon, lock_in})`, "did a switched-off PoBB contributor fire here?"; invariant up to the first firing, no re-run; served as `?lens=abort:epsilon_off|lock_in_off|all_off`. *Suppressing* a fired contributor is record-computable; *adding* an un-fired one needs the per-step `p_best` stream → that's the real sibling-cycle run, not the read-side mask.
- **Deferred / hypotheses:** constraint masks (config-check verdict) and the **order** mask (per-step `SampleOrderStep` — granularity on the per-round fold unverified). Future write-side = fork-from-divergence (substrate change), where persisted mask identity finally earns its keep (decision #1).

### Plus-backlog (opportunistic, unscheduled)
Hard-Sample Sorter Phase 2/3 (Phase 1 `build_hard_samples_artifact` shipped) · Webapp Perf: SSE client cutover (backend `events:subscribe` shipped, client still 2s poll), SWR/TanStack (blocked on a vitest harness — now present), `HardSamplesTable` virtualization, strip redundant memos under React Compiler (keep `l1RoundsKey` fingerprints) · MCP server mode · research extensions.

## Captured — pending triage

- **Export / copy from dashboard** — one-click "copy" on the optimizer box (winning prompt + state); first slice of broader export.
- **Origin check-in plain-language recap** — folded into the origin check-in flow; pending review.

## Already shipped

Verified against code; `git log` + ADRs hold the detail. Engine (verdict-resolution P1 `c714bffd`, `rounds_to_95` exit gate) · spend / composite-P1 (`emit_token_usage`, `TokenUsageRecord`) · identity Stage 1 (OIDC Google+GitHub, allowlist, per-user quotas) · control-plane highway (`CommandDispatcher` + `routers/commands.py` + SSE) · connector boundary + 2nd connector (`termnorm` + `promptpotter`, config-driven `backend_type`) · origin check-in + Ingest Slice 1 · webapp read-only surface served at root. ADRs: [0001](../adr/0001-m12-control-plane.md) · [0002](../adr/0002-identity-foundation.md) · [0003](../adr/0003-spend-and-tenancy.md).

**Live forward gap (non-derivable):** identity is **Stage 0.5** — OIDC wire is live but RLS / SCIM tenant isolation is **not yet enforced**.

## Non-functional requirements

| Requirement | Target |
|---|---|
| Single evaluation (500 items) | < 10 min |
| Full run (5 iters × 500 items) | < 60 min |
| Project store per campaign | < 10 MB |
| LLM providers | OpenAI-compatible (OpenRouter default) |
| Python | 3.13 |
| Crash recovery | incremental `.partial.jsonl`; resume cache-hits prior |
