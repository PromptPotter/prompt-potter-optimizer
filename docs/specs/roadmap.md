# Roadmap

> **Beta.** Forward todo in execution order; this file absorbs the per-milestone specs (git log holds their full prose). The two `m12-*.yaml` files + the ADRs are the only other live contracts.
>
> **Live now:** deployed at `https://app.promptpotter.com` (Cloudflare Tunnel + systemd, OIDC — see [`deploy-linux/`](../../deploy-linux/README.md)). **Open signup**: completing OIDC grants access, bounded by a per-account lifetime spend ceiling rather than an approval queue, on **one shared LLM key** from `.env` — so the sequence below is "harden a thing already serving users," not "prep before launch."
>
> **Three ways to run it (who operates it).** **(1) We run it** — the hosted beta above, allowlist-gated on one shared key; limited free (10 campaigns · 20 rounds each), then BYO key (the per-user coupon → BYO path is Lane A2 below, spec-only today). **(2) You run it** — local, Claude-operated via `/potter-run` on your own keys, unlimited, full source ([`docs/manual/02-install.md`](../manual/02-install.md)). **(3) Your team runs it** — the *same* self-hosted stack as the beta ([`deploy-linux/`](../../deploy-linux/README.md): Cloudflare Tunnel + OIDC allowlist), multi-user + whitelabel, yours to own (renaming it: [`whitelabel.md`](../developer/whitelabel.md)). **The developers and operator run tier 3** (self-hosted team-online); tiers 1 and 3 are one codebase + `deploy-linux/` stack, differentiated by who owns the box and whitelabel, not a fork. (Concurrent multi-user serving is `capacity=1`/sequential today — gated below.)

## Hard ordering (violate → rebuild)

- **Build every new webapp data panel on `dashboard.json` polling + the SSE ledger-tail.** That pair is the design, not an interim seam awaiting a cutover; there is no `live-state` endpoint to wait for.
- **BYO per-user API keys — now the load-bearing half, and unbuilt.** Signup is open and each account is metered against `FREE_TIER_SPEND_CAP_USD` (lifetime, `quota.py::lifetime_ceiling_usd`), so the host key is bounded — but a user who spends their ceiling has **nowhere to go**: hitting it is a dead end until BYO lands. That is the liability now, not the unbounded spend it replaced. Lane A2, and the coupon layer is optional beside it rather than a prerequisite.
- **HTTP-edge abuse protection is now due.** Cloudflare edge + the per-account ceiling + per-user `JobRegistry` quotas bound the public surface; with the approval queue gone, app-level rate-limiting (C6) is the remaining gap — nothing bounds the NUMBER of accounts, only what each one may spend.

Any new endpoint is multi-tenant by default.

## Lane 0 — daily hygiene

Drain before feature work: [`code-debt-cleanup`](code-debt-cleanup.md).

## Sequence

Sequenced into lanes by dependency, not milestone number. **Front priority = Lane A + the publication lane, concurrent** (no shared seam). Lane B is closed; Lane C follows A.

### Lane A — beta usable for free-tier web users, end-to-end

| # | Item | Status |
|---|---|---|
| A2 | Host coupon + BYO per-user API keys | pending — **overdue** (see § Host coupon + BYO); token HQ at `/auth/{quota-status,activity}` already shipped |

### Lane C — product differentiator + capability (after A)

| # | Item | Status |
|---|---|---|
| C1 | **Chat-first front door** — one thread: ingest/check-in → curated activity stream → inline decision buttons (existing verbs). | **Arc 1 shipped** (curated activity + SSE consumer + in-thread loop control, origin gate folded in); Arc 2 (conversation endpoint) deferred — [`chat-foundation.md`](chat-foundation.md) |
| C2 | Composite fitness P2–P4 (P1 = spend, done) — data rollup anytime; **scatter panel after P3** | pending (see § Connectors + L4) |
| C3 | L4 closure — the recursion + the L4 campaign + `proxy_lift_corr ≥ 0.6` re-validation | **recursion SHIPPED + live-validated** (`new promptpotter-self` mints + runs real inner campaigns via the in-process seam; the `llm_only` connector it also yielded is withdrawn — zero adopters). Open: the bounded cheap default config, and the `proxy_lift_corr` gate — which is itself gated on the panel being able to resolve one optimizer prompt from another (`rank-optimizer-prompts` reads `UNKNOWN` as of 2026-08-02: no state measured twice on any cell). The specialized outer prompt set and the inner-spend rollup both SHIPPED and were listed here as open long after — [`l4-outer-loop.md`](l4-outer-loop.md) § Finish line |
| C4 | Cross-user measurement panel (after P3) | pending (see § Ingest + chat-first web) |
| C5 | MCP server mode (= **agent-tool parity**, see § Agent-tool parity) · user-editable `pipeline.yaml` in UI | pending |
| C6 | Public-service hardening (Docker, metrics, rate-limit, billing) — `/health` shipped; **pull rate-limit/metrics forward if the beta opens past the allowlist** | pending |
| C7 | Non-prompt targets + evolutionary operators · multimodal · research extensions | pending |
| C8 | **Mask abstraction** — backend organizing structure (alternative-criterion + transferability); M1 = scoring-function-swap divergence + minimal visual clues, then migrate every divergence trigger onto it | M1 + abort shipped (see § Lineage mask) |

**Parallel lane — publication (front, concurrent with Lane A).** Engine exit gate (`rounds_to_95`) shipped → this is *running experiments + write-up*: BBEH primary, AIME in band, HotPotQA queued but unwired, GSM8K saturated — roster and the admission bar in [`../research/benchmarks.md`](../research/benchmarks.md); 3 seeds + Wilson CIs + McNemar vs CAPO/DSPy; ablation rows L1 / L1+L2 / full · scan · SearchMemory · critique · zero-signal-filter. Competitor + L4 numbers wait on C3. **Two publication blockers, both in [`../research/bbeh-comparison/README.md`](../research/bbeh-comparison/README.md): the peers and PromptPotter do not yet call the same model, and the 28% BBEH-mini reading sits far above the public anchor and is unverified.** Endpoint hardening P0 (auth dep on every router, pinned `ALLOWED_ORIGINS`, `extra=forbid` on request models, poll rate-limit) lands before any non-localhost open.

**Far-horizon (unscheduled).** Synthetic dataset from one hold-out question (removes the dataset-provision requirement; the real metric is synthetic→real transfer of *optimizer lift*, anchored on the single genuine hold-out) · AlphaEvolve code-harness.

## Permanent contracts (constitutions, not steps)

- **Identity foundation** — OIDC wire + PostgreSQL RLS; three-stage staging. → [`ADR-0002`](../adr/0002-identity-foundation.md)
- **Spend + tenancy** — `TokenUsageRecord` on the canonical ledger via `emit_token_usage`. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md)
- **Control plane** — Control-remote I/O kind; closed in/out sets ([`m12-api-openapi.yaml`](m12-api-openapi.yaml) + [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml)). → [`ADR-0001`](../adr/0001-m12-control-plane.md)
- **Frontend surface** — per-control behavior per auth/data state. → [`frontend-surface-contract`](frontend-surface-contract.md)
- **Verdict resolution** — the statistical model behind the live adaptive queue + `hard_samples.json`. → [`verdict-resolution`](../methods/verdict-resolution.md)

---

## Design notes (folded specs)

Terse landing for the per-milestone specs that were consolidated here. Status is truth; full original prose is in `git log`.

### Origin-resolution check-in
LLM proposer + deterministic readiness gate resolve a messy CSV into a complete origin (no hidden defaults, no literal-column requirement); `high`-confidence fields auto-promote `proposed→confirmed` before mint. Non-derivable kernels: reuses the `checkin/2` node (no separate `origin_resolve` node/model); **deliberately off the operator surface** — `reasoning_floor/ceiling` (backend-node-only); model/provider are always optimizer-locked (an invariant, no knob). Concept: [`../architecture.md`](../architecture.md) §0.5 (Origin vs check-in vs round-0/C0); mechanics in `git log`.

### Ingest + chat-first web
> **Chat-first front door** (thread model, activity-stream translator, copilot decision
> buttons, campaign-scoped persistence) has its own contract: [`chat-foundation.md`](chat-foundation.md).
> This note keeps only the ingest / draft-campaign detail.

Four nouns map to OIDC: Install=`iss`, User=`sub` (`user_id=f"{iss}:{sub}"`, SCIM 2.0 Core names verbatim), Project=`tenant_id` claim (today's `datasets/{name}/`), Campaign=cycle 1:1.
- **The committed artifact is a Dataset, not a campaign:** 4 content-hashed files at `projects/{tenant}/datasets/{slug}/` (`cache.json` rows, `pipeline.yaml` overlay, `task_description.md`, `prompts/default.yaml`) compose into `JobSearchPoint.content_hash`; the sibling `campaign.json` is NOT in the hash. Identical datasets → identical `cycle_{target_hash[:12]}` + a shared `measurements/` (free cross-tenant pooling).
- **Draft-campaign object:** `DraftCampaign` negotiates both the Dataset and campaign config; smart defaults `connector=termnorm`/`exact_match`/`max_rounds=5`; model + `reasoning_effort` resolved from the dataset-reasoning-matrix at *commit*, not pinned on the draft. Chat + panel are two views over one server-side draft, synced via `edit-draft-campaign` + SSE `DraftUpdatedRecord` (declare in asyncapi before the handler).
- **Endpoints:** `POST /datasets/ingest` (multipart; 409 `slug_collision`→`{slug,suggested_slug}`, version-and-repoint Replace never overwrites; 422 parse). `GET /datasets` flat list with `tier: yours|install` (install content is tracked in git and ungated; a tenant slug shadows an install one). **Durable check-in (shipped):** ingest mints a real disk-backed campaign in the `checkin` lifecycle on the first action (the draft's `draft_id` IS the `campaign_id`, working state under `campaigns/{id}/checkin/` via `CheckinDraftStore` — no in-memory registry); it shows in the sidebar + survives a restart. Start path = `start-checkin` (gate → commit dataset → flip `checkin`→`active` → run); the CLI `new <file>` shares the commit/flip body (`prepare_checkin_run`) and runs inline.

### Connectors + L4 inner-cycle execution
- **Connector contract:** `Connector` dataclass (`connectors/protocol.py`), 3 hooks `wire_adapter`/`session_factory`/`extract_experiment`; `backend_type` read from `pipeline.yaml`, never hardcoded. A third party registers one through the `promptpotter.connectors` entry-point group — no fork; built-ins stay a literal dict so a source-tree run is never backend-less, and no plugin may shadow one.
- **Execution mode (the L4 self-recursion seam)** — owned by [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode; what is still open rides [`l4-outer-loop.md`](l4-outer-loop.md) § Finish line. Concept: [`optimizer-of-the-optimizer`](../concepts/optimizer-of-the-optimizer.md).
- **Composite fitness phases:** P1 surface (done) · P2 per-candidate rollup + scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.yaml::scoring_post_aggregate` · P4 Pareto-PoBB (stretch).
- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types + L1/critique injection-echo validators + a repeat-detection circuit breaker.

### Agent-tool parity — PromptPotter as a callable tool inside an operating agent
Today PromptPotter is driven by a human or by Claude via `/potter-run` (the entry-points list is in [`../README.md`](../README.md)). The next invocation surface is **parity as a first-class agent-callable tool**: an *operating agent* — the user's own, or an ML-research agent like NVIDIA's AutoResearch — calls PromptPotter as one move in its toolbox. Mechanism already on the board: **C5 MCP server mode**; this note is the *why* + the *shape*.
- **PromptPotter as another agent's try-harness-first move.** An agent that improves models (NVIDIA's reaches straight for SFT/GRPO/DPO to change *weights*) would, given PromptPotter as a callable tool, often pick it first — cheaper (inference-only), faster, transferable across models, no weights to store. Its autoresearch loop already runs on markdown skills + a ledger, so PromptPotter drops in *beside* NeMo RL, not in place of it.
- **Weight-training as our agent's escalation.** The mirror: an operating agent driving PromptPotter should, at the harness ceiling (a failure no prompt/pipeline change fixes), route to SFT/GRPO/DPO — a policy handed to the driving agent, not a new loop mechanism.
- **Parity = the MCP tool exposes the CLI/skill lifecycle** — mint, run, supervise, read results — so an agent can operate a campaign end-to-end.

Full argument + a same-dataset, same-base-model head-to-head experiment: [related-work.md](../research/related-work.md) § PromptPotter × NVIDIA AutoResearch. Tracked as **C5**.

### Application radius — what PromptPotter EMITS, and the standing DSPy rule

Sibling to § Agent-tool parity: that one widens how PromptPotter is *invoked*, this one widens what it *emits*.

**The standing rule first, because it governs every future integration and not just this one: whenever we plan an ecosystem expansion — a new integration, export, registry, tracing sink, or callable surface — DSPy is the reference we read before designing.** Not because their code is better; a source study says plainly that it is not ([related-work.md](../research/related-work.md) § What a DSPy source study settles). **Their ecosystem beats ours while their code does not**, and that asymmetry is the whole lesson: reach is won by being consumable, not by being well-built. Read how DSPy gets consumed, then decide what we emit. Skipping that read is how we would build a good surface nobody plugs into.

**The gap, located.** PromptPotter today is *terminal* — it ends at "here is your prompt", and a human carries that to whatever is in production. DSPy is *ambient* — it is already what runs, and optimization is one operation performed against it. The gap is not features: it is that our output is a **report** where theirs is an **artifact a running system consumes**. Closing it is emitting something their ecosystem already knows how to read.

**The boundary, and it is not negotiable: we write a file and provide a reader. We never load, host, route, or hot-swap.** The swap belongs to the host — a registry alias, a path the app reads at boot, a committed artifact. Becoming a serving framework is out of scope, permanently.

**The artifact — SHIPPED.** `cycles/{id}/export.json`, projected from one `RoundResult` by `domain/export.py` and written by the same `mark_finished` call that stamps `index.json::final`. Its own file rather than a key under `final`: its readers are outside this package, and handing them the campaign index to dig through is not an artifact. Reader contract + the four rules as built: [`../developer/stable-api.md`](../developer/stable-api.md) § 5c. What each rule inverts, all verified in DSPy's source:

- **Field names always, never positional.** `Signature.dump_state` writes fields positionally and `load_state` zips them back with `strict=False`, so a signature that gained a field reloads a scrambled prompt with no error at all.
- **Provenance travels *in* the artifact** — campaign, cycle, the fitness **under its named formula**, n, CI, θ, matched-origin lift, the dataset fingerprint, the optimizer-manifest hash. DSPy's artifact carries none of this: you cannot ask it *"how good is this, and how do you know?"* This is the half we already compute and they cannot, and it turns the README's self-validation claim into a receipt that travels with the prompt.
- **An `artifact_version`,** so a reader can **refuse**. We owe no back-compat (root § STOP); refusing loudly is the point.
- **Model identity yes, credentials never. JSON only, never pickle** — it is text and scalars.

**Three consumers, one artifact.** (a) Our own runtime, via a loader returning a `PromptTemplate`. (b) A DSPy program — through a `to_dspy` view living in the `promptpotteropt` repo, never here (the dependency arrow is one-way, and that packaging boundary is [`ADR-0006`](../adr/0006-embeddable-core-and-extras.md)); it applies the winner to a *live* program and never emits DSPy state, which sidesteps the positional-zip corruption rather than inheriting it. (c) MLflow — targeting the **Prompt Registry** (`register_prompt` / `load_prompt` / `search_prompts`), **not a model flavor**, because we produce prompts, not programs.

**Dependency, settled:** the dataset fingerprint is load-bearing, not decorative — an exported fitness number is only as trustworthy as the identity of the rows it was measured on. B2 needed none (it compares the content each stored row already carries), so the artifact brought its own: `shared/hashing.py::dataset_hash`, the rows alone. Deliberately not a slice of `content_hash`, which mixes prompt and pipeline config into the same digest — right for a cache key, useless as an identity two campaigns can compare.

**Open — the three consumers.** (a) is built (`PromptExport.template()`); (b) `to_dspy` lives in the `promptpotteropt` repo and waits on Phase C; (c) MLflow's Prompt Registry is unwritten. So is § Captured "Export / copy from dashboard" — the same artifact behind a button.

### Schema-description axis — the one open step
The axis itself shipped (`fold_schema_descriptions`, `SCHEMA_RENAME_PARAM`, `effective_l1_field_names`, pinned by `test_integrity.py`); why the schema steers at all is [`../concepts/structured-output.md`](../concepts/structured-output.md). **Open: `new --sweep-batch` it on `justlogic-d234`** — promote at `proxy_lift_corr ≥ 0.6`, and a negative result closes the axis by reverting it.

### Fitness comparability — the slice-4 remainder
Slices 1–3 shipped (`fit_rasch_2pl`, `graduate_ruler_model`, `cumulative_theta`, the webapp headline). Open: the **cross-round headline surfaces** + the lineage `/N` badge ([`frontend-surface-contract.md`](frontend-surface-contract.md)), and **feeding graduated discrimination `aₛ` into `select_round_subset`**, which is still 1PL ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md)).

### Prompt-iteration framework + exit gate
- **Exit gate:** `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`; `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches (or modify the rules).
- `_mint_fork` (`resume_and_fork/fork_siblings.py`) is the single entry for all 7 `ForkTrigger` variants (one `ForkSpec` + `CycleSeed`); L2/L3 auto-rebase capped at `MAX_AUTO_REBASES = 10`/invocation, gated by `OptimizationConfig.rebase_capability`.
- **Round-1 verdict (conformance-anchored):** 0 ✗ → healthy · 1 ✗ → degraded · ≥2 ✗ → broken; behavior checks are pure `(round_dict, ctx) → CheckResult`. (Model/provider locking is not a behavior check — it's structural: `node_param_keys` never emits the axes, + the `validate_overrides` backstop.) The Track-7 L2 self-diagnosis rule turns a missing `evidence_grounding` citation into an L2 `task_context` nudge.
- Sweep batches: one fork per `OperatorSweepFile` under `datasets/{name}/sweep/*.json` via `new --sweep-batch` (`application/sweep.py`), landing under `campaigns/{id}/sweeps/{batch_id}`. There is no separate `sweep` verb — one harness for the job. Live self-improvement mechanism = **L4** (`new promptpotter-self`).

### Host coupon + BYO per-user API keys
Full contract: [`ADR-0003`](../adr/0003-spend-and-tenancy.md) § Host coupon. The host runs users on its own keys up to a **coupon** (fixed USD size + expiry, per user); past it, a user uploads their **own** key and continues on their own money. Two separated concerns: the **coupon** protects the host wallet; concurrency/rate-limit (`jobs/quota.py`) protects the machine — untouched.
- **Coupon (`grant.json`):** per user at `projects/{tenant}/grant.json` = `{amount_usd, issued_at, expires_at}`; remaining is **derived from the host-key ledger**, not a counter. Install defaults (size, validity window, `coupon_void_on_byo`) in `config/settings.py`.
- **Ledger:** `TokenUsageRecord` gains `key_source: host|user` so host-key spend sums separately (declared on `TokenUsagePayload` in the asyncapi).
- **Resolution order (one choke point):** (1) user has own key for provider → use it, **no coupon check** (their money); (2) else coupon alive → host key, metered to coupon; (3) else → `HostAllowanceExhaustedError` (**422 `host_allowance_exhausted`**, "add your own key"). Step 1 short-circuits per provider. The separate **422 `no_api_key`** = no user key *and* no host key configured. `get_llm_client(provider, *, api_key=None)` stays identity-free; `resolve_api_key(identity, provider, stores)` is the wrapper called from the one acquisition seam, `dispatch/llm_call/call.py::llm_call`, + `origin_resolve.py::resolve_origin_turn`.
- **BYO store:** `TenantApiKeyStore` at `projects/{tenant}/api_keys.json` — Fernet ciphertext (`SECRETS_FERNET_KEY`, no plaintext fallback) + plaintext `providers_set` index; key never echoed/logged/traced. On `PUT`, `coupon_void_on_byo` decides whether the remaining coupon dies or persists — host's choice.
- **Gate will read live (design, NOT built):** the per-cycle `BudgetGate` is to read coupon-remaining (re-summed every tick) via a new `StopReason.HOST_ALLOWANCE` — closing the "launch-snapshot only" gap. **None of the coupon exists in code today** — no `HOST_ALLOWANCE` member on `StopReason`, no `grant.json`, no `TenantApiKeyStore`; `key_source` is declared in the asyncapi only. **D1:** the coupon replaces the daily-cap path (`effective_spend_cap_usd`/`spend_budget_usd_daily` deleted — one wallet gate, not two).
- **Verbs (auth router):** ride the **auth router** as siblings of the shipped `/auth/{quota-status,user-settings,activity}` (account-scoped, NOT control-plane `/commands/*` — so NOT in `m12-api-openapi.yaml`, whose scope is the closed command set): `PUT/DELETE /auth/api-keys/{provider}` (204), `GET /auth/api-keys` (`providers_set` only), `GET /auth/coupon` (remaining + expiry); `GET /llm-providers` gains `key_source: user|host|none`. Only the event-surface change (`key_source` on `TokenUsagePayload`) is openapi/asyncapi-declared. **BYO lifts the host coupon; the abuse guards still apply.**

### Operator-steered fork
Rides the existing `fork-cycle` command (no new verb); payload extended to `{from_searchpoint, pipeline_overlay, origin_prompt_fields, config_overrides, steered_by}`. `config_overrides` is the fork's whole `OptimizationConfig` delta — run limits **plus** two policy toggles (`mechanisms.selection.per_round_resubset` and `schema_field_rename`), so a fork-at-offset-0 can A/B a behaviour knob in isolation (the "behaviour-knob change → sibling cycle" workflow) without touching the global default. `fork-cycle` **mints then launches** (minting alone left web forks idle). The override seed is appended to the fork's own ledger as a read-once `CycleSeedRecord` (read once at the runner seam via `read_cycle_seed`); origin resolves fork-seed-first; no dataset-origin mutation. `max_rounds` is an absolute target (the fork's counter continues from the parent), reconciled consumed-vs-remaining in the dialog.

### State-sync
**Teardown-only design was rejected — do not re-propose** (it reverses the folder-UI §0 commitment). The two state surfaces it concerns are owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

### Run admission + concurrent serving
Campaigns run in sequence behind the atomic `JobRegistry.reserve` slot (`capacity = settings.MACHINE_RUN_CAPACITY`, 1 today); a busy launch 409s `machine_busy` → `CriticalAlertBanner`.
- **Open / gated — `capacity > 1`:** hard predecessors are **Lane A2 (BYO per-user keys)** + a **per-tenant `RateLimiter`** (today process-global) + backend throughput. Raising capacity before these cross-bills the shared key and throttles everyone.
- **Open — durable cross-process lock:** the in-process lock only guards one process; `--workers > 1` or web↔CLI mutual exclusion needs a disk CAS slot file (`O_EXCL` create + heartbeat + stale-reclaim). Same `capacity` knob; durable substrate.
- **Distinct axis, partly shipped — sample look-ahead *within* one run.** `capacity` is how many CAMPAIGNS the box admits; look-ahead is how many SAMPLES one candidate's walk holds in flight, so it does not cross-bill (one user, their own key) and is gated separately on the host-admin `scoring.lookahead` cap. Shipped for `execution: remote_http` backends, armed per round from the browser remote. **Open — the L4 case:** on an in-process connector one "sample" is an entire inner campaign, so the depth is pinned to 1 there. Predecessors are measured peak-RSS headroom for two concurrent inner campaigns (there is an OOM post-mortem in `runner/inner/spawn.py`) and a per-tenant `RateLimiter`, since inner and outer already share the process-global one. Contract: [`m12-api-openapi.yaml::setSampleLookahead`](m12-api-openapi.yaml); boundary: [`../operations/access-model.md`](../operations/access-model.md) § Tier 1a.

### Lineage mask
The shipped read side is [`../operations/mask-projection.md`](../operations/mask-projection.md); code SoT `application/mask/`. Open here as **Lane C8**: the **write side — fork-from-divergence.** Mint a fork *at* a divergence point carrying the mask as its new criterion-of-record, a Control-remote command (declare schema in [`m12-api-openapi.yaml`](m12-api-openapi.yaml) before the handler). It is where persisted mask identity finally earns its keep, and the one honest way to follow a divergence: the alternative branch materialises only as a real, measured fork the operator chose to run — never a stored forecast tail. The deferred hard part is the substrate — the operator-fork seam roots at round + candidate (`ForkSpec`), requires `fork_from_round=0`, carries only the edited searchpoint (`CycleSeed`), and inherits no measurement order; a scoring or sample-set mask forks on those rails because it re-selects a *candidate*, while anything needing a mid-scoring write point needs a replayable order-seed too.

### Plus-backlog (opportunistic, unscheduled)
Hard-Sample Sorter Phase 2/3 (Phase 1 `build_hard_samples_artifact_from_observations` shipped) · Webapp Perf: SSE client cutover for the **dashboard** (backend `events:subscribe` shipped and the *chat* already consumes it via `useCycleEvents`; the dashboard still 2 s-polls), SWR/TanStack (blocked on a vitest harness — now present), strip redundant memos under React Compiler (keep `l1RoundsKey` fingerprints) · MCP server mode · research extensions.

## Captured — pending triage

- **Export / copy from dashboard** — one-click "copy" on the optimizer box (winning prompt + state). Shape is owned by § Application radius; this is that artifact behind a button.
- **Origin check-in plain-language recap** — folded into the origin check-in flow; pending review.

## Identity — live forward gap (non-derivable)

Identity is **Stage 0.5** — the OIDC wire is live but RLS / SCIM tenant isolation is **not yet enforced**.

## Non-functional requirements

| Requirement | Target |
|---|---|
| Single evaluation (500 items) | < 10 min |
| Full run (5 iters × 500 items) | < 60 min |
| Project store per campaign | < 10 MB |
| LLM providers | OpenAI-compatible (OpenRouter default) |
| Python | 3.13 |
| Crash recovery | incremental `.partial.jsonl`; resume cache-hits prior |
