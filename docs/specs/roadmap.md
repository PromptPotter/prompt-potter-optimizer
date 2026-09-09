# Roadmap

> **Beta.** Forward todo in execution order; this file absorbs the per-milestone specs, and `git log` holds their full prose. The two control-plane YAMLs + the ADRs are the only other live contracts.
>
> **Live now:** deployed at `https://app.promptpotter.com` (Cloudflare Tunnel + systemd, OIDC — see [`deploy-linux/`](../../deploy-linux/README.md)). **Open signup**: completing OIDC grants access, bounded by a per-account lifetime spend ceiling rather than an approval queue, on **one shared LLM key** from `.env` — so the sequence below is "harden a thing already serving users," not "prep before launch."
>
> **Three ways to run it, by who operates it.** *We* run it (the hosted beta above, limited free then BYO key — Lane A2, spec-only today) · *you* run it (local, Claude-operated via `/potter-run` on your own keys — [`../manual/02-install.md`](../manual/02-install.md)) · *your team* runs it (the same self-hosted stack as the beta, multi-user + whitelabel). The developers and operator run tier 3. Tiers 1 and 3 are one codebase plus the `deploy-linux/` stack, differentiated by who owns the box, not by a fork.

## Hard ordering (violate → rebuild)

- **Build every new webapp data panel on `dashboard.json` polling + the SSE ledger-tail.** That pair is the design, not an interim seam awaiting a cutover; there is no `live-state` endpoint to wait for. **One exemption, and its shape is the rule: a ONE-SHOT TOPOLOGY read may sit beside the poll when the poll is its INVALIDATION signal rather than its transport.** `GET /campaigns/{id}/pipeline` is the only one — a pipeline resolution cannot change without a new cycle or a new searchpoint, and `dashboard.json` announces both. A panel whose content changes *while nothing else does* fails that test and rides the poll.
- **BYO per-user API keys — now the load-bearing half, and unbuilt.** Signup is open and each account is metered against `FREE_TIER_SPEND_CAP_USD` + `FREE_TIER_TOKEN_CAP` (lifetime, `quota.py::lifetime_ceilings`), so the host key is bounded — but a user who spends their ceiling has **nowhere to go**. That is the liability now, not the unbounded spend it replaced. Lane A2.
- **HTTP-edge abuse protection is now due.** Cloudflare edge + the per-account ceiling + per-user `JobRegistry` quotas bound the public surface; with the approval queue gone, app-level rate-limiting (C6) is the remaining gap — nothing bounds the NUMBER of accounts, only what each one may spend.

Any new endpoint is multi-tenant by default.

## Lane 0 — daily hygiene

Drain before feature work: [`code-debt-cleanup`](code-debt-cleanup.md).

## Sequence

Sequenced into lanes by dependency, not milestone number. **Front priority = Lane A + the publication lane, concurrent** (no shared seam). Lane B is closed; Lane C follows A.

### Lane A — beta usable for free-tier web users, end-to-end

| # | Item | Status |
|---|---|---|
| A1 | First-run exhibit — a finished campaign a new account reads before supplying anything | **no working dataset** — `screen-taste-v0` is a stub whose origin read below its own chance floor; cutting a replacement is its own work-scope after the release. The mechanism that SERVES it (install-tier campaign root, `demo_mode_enabled` reader) is unbuilt — [`recommender-demo.md`](recommender-demo.md) |
| A2 | Host coupon + BYO per-user API keys | pending — **overdue** (see § Host coupon + BYO per-user API keys); token HQ at `/auth/{quota-status,activity}` already shipped |
| A3 | Anonymous preview tier — the public site's chat before sign-in | spec only, deliberately (see § Anonymous preview tier); blocked on the chat backend existing |

### Lane C — product differentiator + capability (after A)

| # | Item | Status |
|---|---|---|
| C1 | **Chat-first front door** — one thread: ingest/check-in → curated activity stream → inline decision buttons (existing verbs). | Arc 2 (conversation endpoint) deferred — [`chat-foundation.md`](chat-foundation.md) |
| C2 | Composite fitness P2–P4 (P1 = spend, done) — data rollup anytime; **scatter panel after P3** | pending (see § Connectors + L4) |
| C2b | **Judged + turn-structured scoring** — an LLM-as-judge as a measured observation, and the per-step ruler it opens | judges, the grading call path and the `retrieve → ground → answer` schema all SHIPPED (`promptpotter/judges/`); open: the per-step ruler (see § Judged and turn-structured scoring) |
| C3 | L4 closure — the recursion + the L4 campaign + `proxy_lift_corr ≥ 0.6` re-validation | Open: the bounded cheap default config, and the `proxy_lift_corr` gate — itself gated on the panel being able to resolve one optimizer prompt from another — [`l4-outer-loop.md`](l4-outer-loop.md) § Open |
| C4 | Cross-user measurement panel (after P3) | pending (see § Ingest + chat-first web) |
| C5 | MCP server mode (= **agent-tool parity**, see § Agent-tool parity) · user-editable `pipeline.yaml` in UI | pending — the editable half is gated on the served campaign pipeline resolution (`architecture.md` §0, two resolution seams): editing a value the surface cannot correctly READ is how the wrong scope gets written back |
| C6 | Public-service hardening (Docker, metrics, rate-limit, billing) — `/health` shipped; **pull rate-limit/metrics forward if the beta opens past the allowlist** | pending |
| C7 | Non-prompt targets + evolutionary operators · **agent harnesses** (§ Evolving agent harnesses) · multimodal · research extensions | pending — after v1 |
| C8 | **Mask abstraction** — backend organizing structure (alternative-criterion + transferability); M1 = scoring-function-swap divergence + minimal visual clues, then migrate every divergence trigger onto it | M1 + abort + the scoring write side shipped (see § Lineage mask) |

**Parallel lane — publication = M13, and it is now the project's closing focus** (the distributable-`promptpotter-self` milestone it followed is done). Dependency sequence, no dates, running record in `.scratch/m13-preprint.md`; manuscript in `paper/`. A **fourth** publication blocker joins the three below: the peers and PromptPotter do not grade with the same function — `matchers.py::_exact_match` runs `extract_last_bold` on both sides, the notebooks' inlined `exact_match` compares whole strings, and on a chain-of-thought benchmark that difference favours us.

 BBEH primary, AIME in band, HotPotQA queued but unwired, GSM8K's saturation verdict withdrawn and now a pilot candidate — roster and the admission bar in [`../research/benchmarks.md`](../research/benchmarks.md); 3 seeds + Wilson CIs + McNemar vs CAPO/DSPy; ablation rows L1 / L1+L2 / full · scan · cross-run archive · critique · zero-signal-filter. Competitor + L4 numbers wait on C3. **Three publication blockers, all in [`../research/bbeh-comparison/README.md`](../research/bbeh-comparison/README.md): the peers and PromptPotter do not yet call the same model, the optimization budget is not held constant either, and the 28% BBEH-mini reading was taken at a model the dataset no longer pins.** Endpoint hardening P0 (auth dep on every router, pinned `ALLOWED_ORIGINS`, `extra=forbid` on request models, poll rate-limit) lands before any non-localhost open.

**Far-horizon (unscheduled).** A synthetic dataset cut from one hold-out question, which would remove the dataset-provision requirement — the real metric there is synthetic→real transfer of *optimizer lift*, anchored on the single genuine hold-out · an AlphaEvolve code-harness · dogfooding against a self-hosted gateway, which would prove the "gateway routes, PromptPotter tunes what it routes to" pairing against a real system and exercise the REST surface as an external caller, forcing the inbound credential we do not have. Opportunistic, no date.

## Permanent contracts (constitutions, not steps)

- **Identity foundation** — OIDC wire + PostgreSQL RLS; three-stage staging. → [`ADR-0002`](../adr/0002-identity-foundation.md)
- **Spend + tenancy** — `TokenUsageRecord` on the canonical ledger via `emit_token_usage`. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md)
- **Control plane** — Control-remote I/O kind; closed in/out sets ([`api-openapi.yaml`](api-openapi.yaml) + [`events-asyncapi.yaml`](events-asyncapi.yaml)). → [`ADR-0001`](../adr/0001-m12-control-plane.md)
- **Frontend surface** — per-control behavior per auth/data state. → [`frontend-surface-contract`](frontend-surface-contract.md)
- **Verdict resolution** — the statistical model behind the live adaptive queue + `hard_samples.json`. → [`verdict-resolution`](../methods/verdict-resolution.md)

---

## Design notes (folded specs)

Terse landing for the per-milestone specs consolidated here. Status is truth; the original prose is in `git log`.

### Probe rounds — what they mean, and why the lever is not wired

**This section stays at full length — it is a written SPEC, not stale description of an unwired lever.** It reads as prose about nothing because `L2ContextOutput` carries no `action` field, so a doc-shrink pass scores it as dead. Operator-decided: don't compress it, don't fold it into a code pointer, and don't file the missing field as drift.

**A probe round spends its whole budget interrogating ONE thing** — one axis, one variable, one recurring mistake — instead of spreading a broad mutation set across the failure surface. The distinguishing move is **more candidates on a narrower question**, so the round returns a real answer about that one thing rather than one noisy sample of it.

**L2 cannot request one today: there is no `action` field on `L2ContextOutput`, deliberately.** The shipped version selected samples by *warning* while the prompt asked L2 which *axis* to probe, and the warning inventory behind it was later deleted — leaving a set that only fills on backend degradation. On a healthy run it is empty, so the round scored every candidate 0/0 and persisted `accuracy: 0.0`, indistinguishable downstream from a genuinely terrible candidate, while still paying its optimizer calls and consuming a round. `l2_targets_l1_surface` even counted the choice as conformant — L2 scored 100% precisely by picking the action that measured nothing.

The lever was removed rather than guarded, because a guard leaves a no-op action sitting on L2's menu. **Re-running warned queries is degradation triage, not a search action**; conflating the two is what put an empty-set path in the candidate scorer. Build the real thing as defined above: pick the target from measurement (a peaked axis, a recurring failure cluster), widen `n_variants` for that round only, and select samples by *relevance to the hypothesis* — never by a set that is empty whenever the run is healthy.

### Origin-resolution check-in
Three invariants outlive the build: it reuses the `checkin/2` node (never a separate `origin_resolve` node or model); `reasoning_floor`/`ceiling` stay **off the operator surface**, backend-node-only; and model and provider are optimizer-locked. Concept: [`../architecture.md`](../architecture.md) § Origin, parent, and check-in. Post-flip the same node consults in RUN mode, raising `pause-cycle` / `change-spend-budget` / `fork-cycle` instead of draft patches, over the `RaisedCommand` shape `datasets/origin_resolve.py` already carries. Lands with L4, not before.

### Ingest + chat-first web
> **Chat-first front door** (thread model, activity-stream translator, copilot decision buttons, campaign-scoped persistence) has its own contract: [`chat-foundation.md`](chat-foundation.md). This note keeps only the ingest / draft-campaign detail.

Four nouns map to OIDC: Install=`iss`, User=`sub` (`user_id=f"{iss}:{sub}"`, SCIM 2.0 Core names verbatim), Project=`tenant_id` claim, Campaign=cycle 1:1.

**The committed artifact is a Dataset, not a campaign:** 4 content-hashed files at `projects/{tenant}/datasets/{slug}/` (`cache.json` rows, `pipeline.yaml` overlay, `task_description.md`, `prompts/default.yaml`) compose into `JobSearchPoint.content_hash`; the sibling `campaign.json` is NOT in the hash. Identical datasets → identical `cycle_{target_hash[:12]}` + a shared `measurements/`, so cross-tenant pooling is free.

**That hash is dataset-scoped BY DESIGN; pipeline resolution is campaign-scoped. Do not collapse them.** The hash exists so two tenants running the same target pool their paid cells — it answers *are these the same measurement*. What a node runs answers *what is this campaign doing*, and five campaigns sharing one `pipeline.yaml` each run a different model. The moment the dataset file stops carrying concrete `nodes.*.config` (that config moves to the campaign), the hash covers the dataset's shape and the campaign covers its values — which is the split that was always meant, now spelled.

### Connectors + L4 inner-cycle execution
- **Connector contract** — owned by [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md); a third party registers one through the `promptpotter.connectors` entry-point group, and no plugin may shadow a built-in.
- **Execution mode (the L4 self-recursion seam)** — owned by that same file; what is still open rides [`l4-outer-loop.md`](l4-outer-loop.md) § Open.
- **Composite fitness phases:** P2 per-candidate rollup + scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.yaml::scoring_post_aggregate` · P4 Pareto-PoBB (stretch).
- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types + L1/critique injection-echo validators + a repeat-detection circuit breaker.

### Agent-tool parity — PromptPotter as a callable tool inside an operating agent

Two halves of one question, and they are siblings: parity widens how PromptPotter is **invoked**, § Application radius widens what it **emits**.

Today PromptPotter is driven by a human or by Claude via `/potter-run`. The next invocation surface is **parity as a first-class agent-callable tool**: an *operating agent* — the user's own, an agent harness like dsh, or an ML-research agent like NVIDIA's AutoResearch or verl — calls PromptPotter as one move in its toolbox. Those callers stack rather than compete: a harness is where an operator sits, verl and NeMo RL only start above the weights line, and a gateway sits below both. Parity means the MCP tool exposes the CLI/skill lifecycle — mint, run, supervise, read results — so an agent can operate a campaign end-to-end. Mechanism already on the board: **C5**.

**Deliberately held, not just unscheduled.** The REST API is the integration surface for now — protocol-agnostic, and callable by anything that can hold a browser session, which is the live constraint rather than a detail: there is no inbound bearer token or API key ([`code-debt-cleanup.md`](code-debt-cleanup.md) § Blocked — named blocker). MCP would need the same credential and would add a protocol on top of the gap, and the MCP spec itself is still moving. Revisit once the spec stabilizes and a concrete caller asks.

### Application radius — what PromptPotter EMITS, and the standing DSPy rule

**The standing rule governs every future integration, not just this one: whenever we plan an ecosystem expansion — a new integration, export, registry, tracing sink, or callable surface — DSPy is the reference we read before designing.** Not because their code is better; a source study says plainly that it is not. **Their ecosystem beats ours while their code does not**, and that asymmetry is the lesson: reach is won by being consumable, not by being well-built.

**The gap, located.** PromptPotter today is *terminal* — it ends at "here is your prompt", and a human carries that to production. DSPy is *ambient*: it is already what runs, and optimization is one operation performed against it. The gap is not features but that our output is a **report** where theirs is an **artifact a running system consumes**.

**The boundary is not negotiable: we write a file and provide a reader. We never load, host, route, or hot-swap.** The swap belongs to the host — a registry alias, a path the app reads at boot, a committed artifact. Becoming a serving framework is permanently out of scope.

**The artifact** is `cycles/{id}/export.json`; its reader contract and the four rules it obeys are owned by [`../developer/stable-api.md`](../developer/stable-api.md) § 5c. Two of those rules invert findings in DSPy's source: `Signature.dump_state` writes fields positionally and `load_state` zips them back with `strict=False`, so a signature that gained a field reloads a scrambled prompt with no error at all — hence **field names, never positions**; and their artifact carries no provenance, which is the half we compute and they do not.

**Open — two of the three consumers.** Our own runtime is built. `to_dspy` lives in the `promptpotteropt` repo, never here (the dependency arrow is one-way — [`ADR-0006`](../adr/0006-embeddable-core-and-extras.md)), waits on Phase C, and applies the winner to a *live* program rather than emitting DSPy state, sidestepping the positional-zip corruption. MLflow is unwritten and targets the **Prompt Registry** (`register_prompt` / `load_prompt` / `search_prompts`), **not a model flavor**, because we produce prompts, not programs.

### Ecosystem outreach — optional, unscheduled, no commitment implied

Places PromptPotter could get *found* by systems it complements. None are promises; the list exists so it is not lost, tiered by what blocks each.

- **Nothing blocking — DSPy.** `PromptPotterOpt` already exists and installs ([`dspy-optimizer.md`](../developer/dspy-optimizer.md)), so this is a real integration rather than a pitch, and the one item here worth not letting sit. A PR/listing pointing DSPy users at it, alongside `dspy.GEPA`, is next up.
- **No blocker, unsequenced — verl and NVIDIA AutoResearch / Karpathy's `autoresearch`.** Doc-level PRs making the harness-vs-weights case: try a frozen-model harness tune before spending on a training run. For AutoResearch a *skill* entry is stronger than a doc PR, since their loop already runs on markdown skills, and it does not need C5.
- **No blocker, a capability to build — bidirectional MLflow/Langfuse.** Today's sink is fan-out only, so the optimizer never reads it and it can never become load-bearing for the loop. **Write-back** pushes the winning prompt to MLflow's Prompt Registry or Langfuse's Prompt Management, so a team's production app picks up the winner with no PromptPotter dependency at request time — the existing Connector/sink shape, no new search mechanism. **Read-in** ingests a team's traces as a dataset source, chipping at the labeled-dataset requirement, but production traces carry no guaranteed labels, plus PII and format drift, so it needs real design rather than a flag.
- **Waits on C5 — OmniRoute and DeepSeek Harness.** OmniRoute is the gateway-pairing case, untried, and earns adoption only where eval spend is the binding constraint; its live router must never act on the eval path ([`../operations/backend-integration.md`](../operations/backend-integration.md) § Endpoints), because optimizing a routing *policy* is an axis while routing during a campaign voids the numbers. DeepSeek Harness is the operating-agent category itself, and its ecosystem already names MCP servers as a first-class submission category — likely the lowest-friction placement once C5 lands.

### Judged and turn-structured scoring

**Tracked as C2b, and most of it has SHIPPED.** The judges, the grading call path (reuse cache, 429 retry, heartbeat, metering, all decided once at `judges/call.py::ask`), plural judges keyed by the term the formula reads, and the `retrieve → ground → answer` step schema are all live; `promptpotter/judges/CLAUDE.md` owns every contract and reason behind them. It deliberately does NOT live in `pipeline.yaml::nodes` — `node_config_items` is the walk a bulk model steer follows, and that is exactly the boundary a judge may not sit behind.

It unblocks datasets no matcher can grade, and the first is wired and running: `datasets/sealqa-longseal-12/`. Two more record being stuck on it — `email-tagging`'s free-text CRM fields and `screen-taste-v0`'s rating.

**The conversation reaches a formula, and only as scalars.** `domain/scoring.py::turn_scalars` projects `n_turns`, `n_tool_calls` and `{step}_turns` at measure time, beside the `{step}_{reward}` terms the harbor connector banks. The raw `turns` list stays unreachable from a formula on purpose and must remain so: the compiler's AST allowlist has no subscript, no attribute access and no `len`, and `turns` is the first key `compact-archive` moves out of a cold row, so a formula that walked it would raise on every cell it had already scored. Two routes reach scoring from a conversation and there is no third — this projection, or a judge that reads the turns and banks a term.

**What remains, at three distances.** *Per-step difficulty* is the larger prize and the only part needing a run rather than a decision; its blockers and its one non-skippable precondition — bank each step's term separately, which the shipped schema now does — are at [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § Phase 3. The two authored rubrics are unscreened; read them on `seed-screen` / `noise-floor` before funding a campaign on them. *Scoring* one step is already config and step-agnostic. *Optimizing* one step is near: the candidate prompt is trial-scoped (one `SKILL.md` per episode), so an arm mutates the whole episode however narrowly it is scored, and the open design is whether a declared step becomes a NODE — one per step with its own `prompt_info` and `param_keys` — which makes the existing `campaign_config.exclude_nodes` the step selector and adds no new channel. It costs generalizing `connectors/harbor.py`'s singular `AGENT_NODE`. Separately: not RUNNING the later steps, where the cost saving lives, since Harbor runs every declared step and `_unscoreable_step` raises on a truncated one.

**PARTIAL PIPELINE GENERATION — far, and deliberately so.** Today L1 always proposes a COMPLETE pipeline. The feature is that it may propose a partial one, with *which* subset being **L2's** decision — the first place the escalation layer chooses a search SHAPE rather than a search direction.

- **Do not open it until a real pipeline is blocked without it.** With few steps a whole-pipeline proposal is both cheaper to reason about and strictly more expressive. The gain appears only where a pipeline has many steps and no way to tell which one moved the number.
- **Its trigger is a FORK, not a stall.** The signal is L2 reaching a two-way split it cannot choose between: two strategies, no evidence favouring either, and a whole-pipeline arm that would confound them. A plain stall belongs to the existing escalation ladder.
- **It is a human-in-the-loop design cycle, not an implementation ticket.** What a partial proposal may touch, how a partial arm compares against a whole one, and what a δ ruler does with arms of different shapes are all open and none is answerable from code.

One thing the cache deliberately does NOT fix: a grading that fails past its retry omits the term, so `rescore_results` raises inside `measure_sample` and the catch-all banks the cell as an ERROR — discarding a backend answer already paid for. The root is one `except Exception` that cannot tell a measurement failure from a scoring failure. Measure the residual rate before splitting it, and do not compensate downstream.

### Selection-clean reporting
**Why, and the statistical statement, are owned by [`../research/benchmarks.md`](../research/benchmarks.md) § The winner's own number is biased upward.** What this lane owes: a reserved per-dataset partition the loop never scores on, and two readers pointed at it — `verify` (which already re-scores a frozen candidate without touching the cycle, so it is the closest existing shape) and the reported fitness in `export.json`, whose provenance block advertises a deployment estimate it cannot currently claim. Sequenced with the publication lane, not before it: an in-sample headline is wrong in a direction that flatters us, so it costs credibility at publication rather than correctness in the loop. The published BBEH comparison is not what this fixes — its split already satisfies the requirement.

### Evolving agent harnesses
**Tracked as C7. Pulled forward ahead of v1 on an explicit operator call, and the connector half has SHIPPED** — `connectors/harbor.py` plus `datasets/harbor-tbench-regex-log/`. The artifact to evolve becomes an **agent harness** — an agent's prompts, tools and control flow — rather than only a single LLM call or a declared pipeline.

**Do not re-open "whose harness — dsh or LangChain".** That question binds the design to one vendor's harness and inherits its mutation surface. [Harbor](https://github.com/harbor-framework/harbor) sits a layer below — evaluation infrastructure, in `pytest`'s category — and drives ~30 agents behind one `BaseAgent` interface, so which harness is under test is one line in a dataset's `harbor_tasks.yaml` rather than a design commitment.

Two things in the remaining gap are real rather than cosmetic, and both shipped: a **labelless cell** (`Sample.ground_truth is None`, since a verifier grades where no label exists) and an **arm-time observation-key contract** (`Connector.required_observation_keys`). What is still open is a **per-cell spend ceiling** — the budget gate polls at the sample boundary (`scoring/query_loop.py`), so one episode is unbounded spend between polls, and Harbor's `task.toml` bounds wall clock rather than dollars. L4 solved this privately with `OUTER_SAMPLE_WALL_S_PER_ROUND`; nobody generalized it.

SkillOpt, DarwinX and AutoDesign already evolve harnesses for a frozen model, and DarwinX states our own one-armed-search argument back at us. What that comparison leaves standing is what a PromptPotter version must keep rather than re-derive — sequential elimination, cost-per-fitness, subset-invariant ability. Their benchmarks are agent environments `harbor` can now measure but PoBB's cost model still cannot bound, so adopting the target does **not** mean adopting their evaluation suite.

### Schema-description axis — the one open step
Why the schema steers at all is [`../concepts/structured-output.md`](../concepts/structured-output.md). **Open: `new --sweep-batch` it on `justlogic-d234`** — promote at `proxy_lift_corr ≥ 0.6`, and a negative result closes the axis by reverting it.

### Fitness comparability — the slice-4 remainder
Open: the **cross-round headline surfaces** + the lineage `/N` badge, and **feeding graduated discrimination `aₛ` into `select_round_subset`**, which is still 1PL ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md)).

### Exit gate
`rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`; `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches (or modify the rules).

### Host coupon + BYO per-user API keys
**The whole mechanism is owned by [`ADR-0003`](../adr/0003-spend-and-tenancy.md) § Host coupon + BYO keys** — `grant.json`, `key_source`, the three-step resolution order and its two 422s, `TenantApiKeyStore`, and the auth-router verbs. In one line: the host runs users on its own keys up to a per-user coupon; past it a user uploads their own key and continues on their own money. The coupon protects the host wallet, `jobs/quota.py` protects the machine, and the two stay separate.

**Status: none of the coupon exists in code today** — no `HOST_ALLOWANCE` member on `StopReason`, no `grant.json`, no `TenantApiKeyStore`; `key_source` is declared in the asyncapi only.

### Anonymous preview tier
Spec only — **do not build it before the chat backend exists** (`presentation/api/routers/chat.py` is absent), because an anonymous meter with no conversation to meter is a writer with no reader.

- **Its own issuer sentinel, and `is_anonymous` asked BEFORE the operator arm.** This is the trap, not a detail: `quota.py::spends_the_hosts_own_key` reads a missing issuer as "came through the terminal, which only the operator reaches", so an anonymous identity built by leaving the issuer unset resolves as the box operator and is metered by nothing. The sentinel makes the two distinguishable; the ordering makes the distinction bind.
- **Empty capability set.** No command verb, no launch, no ingest, no dataset write — it converses and reads, and every action surface is a sign-in prompt. Nothing is added to `CAMPAIGN_CAP_BY_NAME`, so `_require_capability_for` refuses each verb with no per-verb exception to keep in step.
- **One shared tenant.** `UserStore.get_or_create` writes a `user.json` per tenant, so a tenant per visitor is an unbounded directory-creation surface reachable without authentication. One tenant gives the spend a reader for free: a single row in `jobs/install_spend.py::read_install_spend`.
- **A global daily pool is the ceiling; the per-visitor allowance is UX.** A per-visitor cap is defeated by discarding the visitor, so the only figure that binds is one install-wide daily pool. `admit_launch`'s reservation does not apply — an anonymous turn is one call, and anonymous cannot launch a campaign at all.
- **No trustworthy origin signal ⇒ refuse the request**, or the pool becomes a scrape budget assembled from millions of small turns.

### Operator-steered fork
Two rules survive the build. `config_overrides` carries the fork's whole `OptimizationConfig` delta — run limits **plus** the policy toggles — so **a fork at offset 0 A/Bs a behaviour knob in isolation** without moving the global default; that is the standing "behaviour-knob change → sibling cycle" workflow. And `max_rounds` on a fork is an absolute target, not a remainder: the fork's counter continues from its parent.

### State-sync
**Teardown-only design was rejected — do not re-propose** (it reverses the folder-UI §0 commitment). The two state surfaces it concerns are owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

### Run admission + concurrent serving
Nothing is open in the mechanism. Campaigns run concurrently up to a resolved capacity, a full box QUEUES rather than refuses, and the queue drains least-served-first; every entry point takes the same slot from the same line, the terminal included. The substrate is an OS file lock, so admission is atomic across processes and a dead producer's slot returns at once — which is also C6's predecessor: `--workers > 1` and a containerised worker need no second mechanism. Owned by [`../operations/access-model.md`](../operations/access-model.md) § What bounds resource use.

- **Open — full-speed-each needs more provider quota, not a better divider.** Two busy users on one key each get roughly half the throughput however the window is shared; only **Lane A2** buys more. It is cheap once reached: an `api_key` dimension on `get_llm_client`'s cache key gives every tenant its own limiter.
- **Distinct axis — sample look-ahead *within* one run.** `capacity` is how many CAMPAIGNS the box admits; look-ahead is how many SAMPLES one candidate's walk holds in flight, so it does not cross-bill and is gated separately on `campaign.lookahead`. **Open:** the ceiling is a hand-set constant per connector rather than measured peak-RSS headroom (there is an OOM post-mortem in `runner/inner/spawn.py`), and inner and outer still share the process-global `RateLimiter` with no per-tenant slice. Boundary: [`../operations/access-model.md`](../operations/access-model.md) § host-admin ↔ user.

### Lineage mask
The read side is [`../operations/mask-projection.md`](../operations/mask-projection.md); code SoT `application/mask/`. The **write side — fork-from-divergence — shipped**: `fork-cycle` carries `keep_rounds`, which swaps the offshoot trigger for `OPERATOR_REWIND` so rounds `0..N-1` are lifted, and `ConfigOverrides.scoring` carries the criterion into the fork's effective config. The alternative branch still materialises only as a real, measured fork the operator chose to run — never a stored forecast tail — which is why persisted mask identity is still not a thing: the criterion rides the seed the fork already writes.

Open as **Lane C8**: the SAMPLE-SET half, and it is a different substrate rather than a second knob. A scoring mask forks on the existing rails because it re-selects a *candidate* from arms the run measured; a mask that changes which samples a round buys needs a replayable measurement ORDER seeded into the fork, which `CycleSeed` does not carry and `ForkSpec` does not root at. Until it does, a sample-subset mask previews and stops there.

### Rebase forfeit — pricing WHERE L2 rewinds to
UCB1 over normalized Rasch θ decides where a rebase fork re-expands from (`mask/backprop.py::select_rewind_round`) with **no cost term at all**, so an ancestor deep in the tree looks free. It is not — and the operator's own framing of the loss, "go back to the start and you lose all the data", is wrong in the half easiest to believe: measurements are content-addressed by `(node_configs, sample_id)`, so everything already measured replays free at any cut. What a rebase actually forfeits is (1) **search position** — the θ climbed between the target and the frontier, which mutations from the older parent must re-earn; (2) **evidence leverage** — visits above the cut stop informing the new branch; (3) **future cache misses** — candidates minted from an older parent are new content hashes, and the replay fraction is predictable from observed reuse at comparable cuts (`LineageNode.cached_samples`). Against all three sits the countervailing gain: a target whose adjacent child edges differ by ONE data-affecting knob (`application/knobs.py::classify_config_diff`) buys a controlled one-parameter contrast worth more than its raw θ.

Shape: a third pure fold beside `divergence.py` and `scenario.py`, over the same `SpineCycle` list UCB already loads, emitting a per-candidate-cut `ForfeitReading`. **Served diagnostic FIRST** — stamped on the tree's course nodes so the operator sees the price of each WHERE — and only then folded into the acquisition as `Q − λ·forfeit`, λ a settings constant beside `UCB_EXPLORATION_C`. It never reaches L2 as prose: WHETHER to fork is the LLM's call and WHERE stays UCB's arithmetic, so no panel enumerates ancestors.

### Plus-backlog (opportunistic, unscheduled)
Parent selection — collapse `elect_round_winner`'s greedy promotion and `select_rewind_round`'s UCB1 into one acquisition over the lineage tree, gated on a prior-vs-θ-rank correlation reading: [`parent-selection.md`](parent-selection.md) · Hard-Sample Sorter Phase 2/3 · Webapp perf: SSE client cutover for the **dashboard** (the *chat* already consumes `events:subscribe` via `useCycleEvents`; the dashboard still 2 s-polls), SWR/TanStack, strip redundant memos under React Compiler (keep `l1RoundsKey` fingerprints) · MCP server mode · research extensions.

## Captured — pending triage

- **Origin panel that drifts on the δ ruler** — the panel every green bar is read on is the first `sp_budget_ttest` cells C0 answered, fixed for the life of the cycle (`domain/results.py::origin_panel`). The far-out version lets it swap a cell once the ruler has settled, drifting far slower than the acquisition subset the winners are decided on. Zero priority: the fixed panel is what makes the bars comparable, and drift can only cost that.
- **Export / copy from dashboard** — one-click copy of the winning prompt + state on the optimizer box. The artifact is § Application radius's; this is it behind a button.
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
