# Roadmap

> **Beta.** Forward todo in execution order; this file absorbs the per-milestone specs (git log holds their full prose). The two `m12-*.yaml` files + the ADRs are the only other live contracts.
>
> **Live now:** deployed at `https://app.promptpotter.com` (Cloudflare Tunnel + systemd, OIDC — see [`deploy-linux/`](../../deploy-linux/README.md)). **Open signup**: completing OIDC grants access, bounded by a per-account lifetime spend ceiling rather than an approval queue, on **one shared LLM key** from `.env` — so the sequence below is "harden a thing already serving users," not "prep before launch."
>
> **Three ways to run it (who operates it).** **(1) We run it** — the hosted beta above, allowlist-gated on one shared key; limited free (10 campaigns · 20 rounds each), then BYO key (the per-user coupon → BYO path is Lane A2 below, spec-only today). **(2) You run it** — local, Claude-operated via `/potter-run` on your own keys, unlimited, full source ([`docs/manual/02-install.md`](../manual/02-install.md)). **(3) Your team runs it** — the *same* self-hosted stack as the beta ([`deploy-linux/`](../../deploy-linux/README.md): Cloudflare Tunnel + OIDC allowlist), multi-user + whitelabel, yours to own (renaming it: [`whitelabel.md`](../developer/whitelabel.md)). **The developers and operator run tier 3** (self-hosted team-online); tiers 1 and 3 are one codebase + `deploy-linux/` stack, differentiated by who owns the box and whitelabel, not a fork. (Concurrent multi-user serving is `capacity=1`/sequential today — gated below.)

## Hard ordering (violate → rebuild)

- **Build every new webapp data panel on `dashboard.json` polling + the SSE ledger-tail.** That pair is the design, not an interim seam awaiting a cutover; there is no `live-state` endpoint to wait for.
- **BYO per-user API keys — now the load-bearing half, and unbuilt.** Signup is open and each account is metered against `FREE_TIER_SPEND_CAP_USD` + `FREE_TIER_TOKEN_CAP` (lifetime, `quota.py::lifetime_ceilings`), so the host key is bounded — but a user who spends their ceiling has **nowhere to go**: hitting it is a dead end until BYO lands. That is the liability now, not the unbounded spend it replaced. Lane A2, and the coupon layer is optional beside it rather than a prerequisite.
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
| A2 | Host coupon + BYO per-user API keys | pending — **overdue** (see § Host coupon + BYO); token HQ at `/auth/{quota-status,activity}` already shipped |
| A3 | Anonymous preview tier — the public site's chat before sign-in | spec only, deliberately (see § Anonymous preview tier); blocked on the chat backend existing |

### Lane C — product differentiator + capability (after A)

| # | Item | Status |
|---|---|---|
| C1 | **Chat-first front door** — one thread: ingest/check-in → curated activity stream → inline decision buttons (existing verbs). | Arc 2 (conversation endpoint) deferred — [`chat-foundation.md`](chat-foundation.md) |
| C2 | Composite fitness P2–P4 (P1 = spend, done) — data rollup anytime; **scatter panel after P3** | pending (see § Connectors + L4) |
| C3 | L4 closure — the recursion + the L4 campaign + `proxy_lift_corr ≥ 0.6` re-validation | Open: the bounded cheap default config, and the `proxy_lift_corr` gate — itself gated on the panel being able to resolve one optimizer prompt from another — [`l4-outer-loop.md`](l4-outer-loop.md) § Open |
| C4 | Cross-user measurement panel (after P3) | pending (see § Ingest + chat-first web) |
| C5 | MCP server mode (= **agent-tool parity**, see § Agent-tool parity) · user-editable `pipeline.yaml` in UI | pending |
| C6 | Public-service hardening (Docker, metrics, rate-limit, billing) — `/health` shipped; **pull rate-limit/metrics forward if the beta opens past the allowlist** | pending |
| C7 | Non-prompt targets + evolutionary operators · **agent harnesses** (§ Evolving agent harnesses) · multimodal · research extensions | pending — after v1 |
| C8 | **Mask abstraction** — backend organizing structure (alternative-criterion + transferability); M1 = scoring-function-swap divergence + minimal visual clues, then migrate every divergence trigger onto it | M1 + abort + the scoring write side shipped (see § Lineage mask) |

**Parallel lane — publication (front, concurrent with Lane A).** *Running experiments + write-up*: BBEH primary, AIME in band, HotPotQA queued but unwired, GSM8K's saturation verdict withdrawn and now a pilot candidate — roster and the admission bar in [`../research/benchmarks.md`](../research/benchmarks.md); 3 seeds + Wilson CIs + McNemar vs CAPO/DSPy; ablation rows L1 / L1+L2 / full · scan · SearchMemory · critique · zero-signal-filter. Competitor + L4 numbers wait on C3. **Three publication blockers, all in [`../research/bbeh-comparison/README.md`](../research/bbeh-comparison/README.md): the peers and PromptPotter do not yet call the same model, the optimization budget is not held constant either, and the 28% BBEH-mini reading was taken at a model the dataset no longer pins.** Endpoint hardening P0 (auth dep on every router, pinned `ALLOWED_ORIGINS`, `extra=forbid` on request models, poll rate-limit) lands before any non-localhost open.

**Far-horizon (unscheduled).** Synthetic dataset from one hold-out question (removes the dataset-provision requirement; the real metric is synthetic→real transfer of *optimizer lift*, anchored on the single genuine hold-out) · AlphaEvolve code-harness · **optional: dogfood against a self-hosted gateway** (stand up something like OmniRoute, then run PromptPotter against it) — proves the "gateway routes, PromptPotter tunes what it routes to" pairing with a real system instead of only a README paragraph, and exercises the REST integration surface as an actual external caller would — which is also the thing that would force the inbound credential, since today an external caller can only reach it with auth off. Opportunistic, not committed to a date. Sibling list of optional, not-yet-committed places to get PromptPotter *found* (DSPy, verl, NVIDIA AutoResearch, DeepSeek Harness, and this same OmniRoute dogfood): § Ecosystem outreach, above.

## Permanent contracts (constitutions, not steps)

- **Identity foundation** — OIDC wire + PostgreSQL RLS; three-stage staging. → [`ADR-0002`](../adr/0002-identity-foundation.md)
- **Spend + tenancy** — `TokenUsageRecord` on the canonical ledger via `emit_token_usage`. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md)
- **Control plane** — Control-remote I/O kind; closed in/out sets ([`api-openapi.yaml`](api-openapi.yaml) + [`events-asyncapi.yaml`](events-asyncapi.yaml)). → [`ADR-0001`](../adr/0001-m12-control-plane.md)
- **Frontend surface** — per-control behavior per auth/data state. → [`frontend-surface-contract`](frontend-surface-contract.md)
- **Verdict resolution** — the statistical model behind the live adaptive queue + `hard_samples.json`. → [`verdict-resolution`](../methods/verdict-resolution.md)

---

## Design notes (folded specs)

Terse landing for the per-milestone specs that were consolidated here. Status is truth; full original prose is in `git log`.

### Origin-resolution check-in
Three invariants outlive the build: it reuses the `checkin/2` node (never a separate `origin_resolve` node or model); `reasoning_floor`/`ceiling` stay **off the operator surface**, backend-node-only; model and provider are optimizer-locked — an invariant, not a knob. Concept: [`../architecture.md`](../architecture.md) §0.5 (Origin vs check-in vs round-0/C0). Post-flip, the same node consults in RUN mode — raising `pause-cycle` / `change-spend-budget` / `fork-cycle` instead of draft patches, over the `RaisedCommand` shape `datasets/origin_resolve.py` already carries. Lands with L4, not before.

### Ingest + chat-first web
> **Chat-first front door** (thread model, activity-stream translator, copilot decision
> buttons, campaign-scoped persistence) has its own contract: [`chat-foundation.md`](chat-foundation.md).
> This note keeps only the ingest / draft-campaign detail.

Four nouns map to OIDC: Install=`iss`, User=`sub` (`user_id=f"{iss}:{sub}"`, SCIM 2.0 Core names verbatim), Project=`tenant_id` claim (today's `datasets/{name}/`), Campaign=cycle 1:1.
- **The committed artifact is a Dataset, not a campaign:** 4 content-hashed files at `projects/{tenant}/datasets/{slug}/` (`cache.json` rows, `pipeline.yaml` overlay, `task_description.md`, `prompts/default.yaml`) compose into `JobSearchPoint.content_hash`; the sibling `campaign.json` is NOT in the hash. Identical datasets → identical `cycle_{target_hash[:12]}` + a shared `measurements/` (free cross-tenant pooling).

### Connectors + L4 inner-cycle execution
- **Connector contract** — owned by [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md); a third party registers one through the `promptpotter.connectors` entry-point group, and no plugin may shadow a built-in.
- **Execution mode (the L4 self-recursion seam)** — owned by that same file § Execution mode; what is still open rides [`l4-outer-loop.md`](l4-outer-loop.md) § Open.
- **Composite fitness phases:** P2 per-candidate rollup + scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.yaml::scoring_post_aggregate` · P4 Pareto-PoBB (stretch).
- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types + L1/critique injection-echo validators + a repeat-detection circuit breaker.

### Agent-tool parity — PromptPotter as a callable tool inside an operating agent
Today PromptPotter is driven by a human or by Claude via `/potter-run` (the entry-points list is in [`../README.md`](../README.md)). The next invocation surface is **parity as a first-class agent-callable tool**: an *operating agent* — the user's own, an agent harness like dsh, or an ML-research agent like NVIDIA's AutoResearch or verl — calls PromptPotter as one move in its toolbox. Those callers stack rather than compete: a harness is where an operator sits, verl and NeMo RL only start above the weights line, and a gateway sits below both. Mechanism already on the board: **C5 MCP server mode**; this note is the *why* + the *shape*.
- **PromptPotter as another agent's try-harness-first move.** An agent that improves models (NVIDIA's reaches straight for SFT/GRPO/DPO to change *weights*) would, given PromptPotter as a callable tool, often pick it first — cheaper (inference-only), faster, transferable across models, no weights to store. Its autoresearch loop already runs on markdown skills + a ledger, so PromptPotter drops in *beside* NeMo RL, not in place of it.
- **Weight-training as our agent's escalation.** The mirror: an operating agent driving PromptPotter should, at the harness ceiling (a failure no prompt/pipeline change fixes), route to SFT/GRPO/DPO — a policy handed to the driving agent, not a new loop mechanism.
- **Parity = the MCP tool exposes the CLI/skill lifecycle** — mint, run, supervise, read results — so an agent can operate a campaign end-to-end.

**Deliberately held, not just unscheduled.** The REST API is the integration surface for now — protocol-agnostic, and callable by anything that can hold a browser session, which is the live constraint rather than a detail: there is no inbound bearer token or API key ([`code-debt-cleanup.md`](code-debt-cleanup.md) § Blocked — named blocker). **That does not argue for MCP first** — MCP would need the same credential and would add a protocol on top of the gap. MCP only earns its cost once a real caller's own performance need makes protocol self-description worth the session/handshake overhead over plain REST, and the MCP spec itself is still moving (a newer version just landed and isn't settled/widely adopted yet) — building the wrapper now risks building against ground that shifts under it. Revisit once the spec stabilizes and/or a concrete caller asks for it.

Full argument + a same-dataset, same-base-model head-to-head experiment: [related-work.md](../research/related-work.md) § PromptPotter × NVIDIA AutoResearch. Tracked as **C5**.

### Ecosystem outreach — optional, unscheduled, no commitment implied

Places PromptPotter could get *found* by the systems it's complementary to, not competitive with — none of these are promises, just the current shortlist so it isn't lost. Three tiers by what's actually blocking each one, not by importance:

**Tier 0 — send soon, nothing blocking.**
- **DSPy.** `PromptPotterOpt` already exists and installs (`pip install promptpotter[dspy]`, [`dspy-optimizer.md`](../developer/dspy-optimizer.md)) — this is a real integration already, not a pitch, which makes it the one item here worth *not* letting sit. A PR/listing pointing DSPy users at it (alongside `dspy.GEPA`) is next up.

**Tier 1 — no blocker, but not yet sequenced against each other (messaging PRs + a capability to build — unclear which to do first).**
- **verl** ([HybridFlow](https://arxiv.org/abs/2409.19256), the general RLHF post-training framework — bigger and more independent than any one user of it, e.g. NVIDIA AutoResearch runs on NeMo RL, a separate framework) — a doc/README-level PR making the harness-vs-weights case: try a frozen-model harness tune before spending on a training run.
- **NVIDIA AutoResearch** ([dev blog](https://developer.nvidia.com/blog/how-to-run-an-autoresearch-workflow-with-rl-agent-skills-and-nvidia-nemo/)) / **Karpathy's `autoresearch`** ([repo](https://github.com/karpathy/autoresearch)) — same pitch as verl, aimed at the smaller/more approachable repo; full argument already in [related-work.md](../research/related-work.md) § PromptPotter × NVIDIA AutoResearch. A *skill* entry (their loop already runs on markdown skills) is stronger than a doc PR and doesn't strictly need C5 either.
- **Bidirectional MLflow/Langfuse** — a capability to build, not a PR to send. Today's sink (`architecture.md` § Tracing) is fan-out only — "the optimizer never reads it, so it can never become load-bearing for the loop." Two new directions, split because they're not equally hard: **write-back** pushes the winning prompt to MLflow's Prompt Registry or Langfuse's Prompt Management instead of only a PromptPotter artifact, so a team's production app (already wired to pull prompts from there) picks up the winner with no PromptPotter dependency at request time — same shape as the existing Connector/sink pattern, no new search mechanism; **read-in** ingests a team's existing traces as a dataset source, chipping at the "requires a labeled dataset" Limitation, but production traces are messy (no guaranteed labels/scores, PII, format drift across teams) so this needs real design, not a flag flip.

**Tier 2 — waits on C5 (MCP / agent-callable tool) to have something concrete to offer.**
- **OmniRoute** ([repo](https://github.com/diegosouzapw/OmniRoute)) — the gateway-pairing case from [`code-debt-cleanup.md`](code-debt-cleanup.md) § Blocked — named blocker (the REST entry) and the Far-horizon dogfood item below. Also needs real hands-on use first — not yet tried. It is one slot with OpenRouter and a direct provider key, so it earns adoption only where eval spend is the binding constraint; and its live router must never act on the eval path ([`../operations/backend-integration.md`](../operations/backend-integration.md) § Endpoints). Optimizing a routing *policy* is a separate ask from routing during a campaign — the first is an axis, the second voids the numbers.
- **DeepSeek Harness** ([repo](https://github.com/deepseek-ai/deepseek-harness)) — viral agent-runtime ("everything is a plugin": model adapter, tool registry, sandbox, agent loop), not a router or optimizer — the *operating-agent* category itself, same slot as Claude Code/Cursor/OpenCode. Its ecosystem (`dsh-plugin` tag, `awesome-deepseek-harness` lists) already names **MCP servers** as a first-class submission category, so a PromptPotter MCP tool is a near-direct fit once C5 lands — likely the lowest-friction Tier-2 placement given how fast the plugin catalog is currently forming.

### Application radius — what PromptPotter EMITS, and the standing DSPy rule

Sibling to § Agent-tool parity: that one widens how PromptPotter is *invoked*, this one widens what it *emits*.

**The standing rule first, because it governs every future integration and not just this one: whenever we plan an ecosystem expansion — a new integration, export, registry, tracing sink, or callable surface — DSPy is the reference we read before designing.** Not because their code is better; a source study says plainly that it is not ([related-work.md](../research/related-work.md) § What a DSPy source study settles). **Their ecosystem beats ours while their code does not**, and that asymmetry is the whole lesson: reach is won by being consumable, not by being well-built. Read how DSPy gets consumed, then decide what we emit. Skipping that read is how we would build a good surface nobody plugs into.

**The gap, located.** PromptPotter today is *terminal* — it ends at "here is your prompt", and a human carries that to whatever is in production. DSPy is *ambient* — it is already what runs, and optimization is one operation performed against it. The gap is not features: it is that our output is a **report** where theirs is an **artifact a running system consumes**. Closing it is emitting something their ecosystem already knows how to read.

**The boundary, and it is not negotiable: we write a file and provide a reader. We never load, host, route, or hot-swap.** The swap belongs to the host — a registry alias, a path the app reads at boot, a committed artifact. Becoming a serving framework is out of scope, permanently.

**The artifact** is `cycles/{id}/export.json`; its reader contract and the four rules it obeys are owned by [`../developer/stable-api.md`](../developer/stable-api.md) § 5c. Two rules there invert findings in DSPy's source and only read correctly with them: `Signature.dump_state` writes fields positionally and `load_state` zips them back with `strict=False`, so a signature that gained a field reloads a scrambled prompt with no error at all — hence **field names, never positions**. And their artifact carries no provenance, so you cannot ask it *"how good is this, and how do you know?"* — the half we compute and they do not, which is what makes ours a receipt that travels with the prompt.

**Open — two of the three consumers.** (a) our own runtime is built. (b) `to_dspy` lives in the `promptpotteropt` repo, never here (the dependency arrow is one-way — [`ADR-0006`](../adr/0006-embeddable-core-and-extras.md)), and waits on Phase C; it applies the winner to a *live* program and never emits DSPy state, sidestepping the positional-zip corruption rather than inheriting it. (c) MLflow is unwritten, and targets the **Prompt Registry** (`register_prompt` / `load_prompt` / `search_prompts`), **not a model flavor**, because we produce prompts, not programs. So is § Captured "Export / copy from dashboard" — the same artifact behind a button.

### Selection-clean reporting
**Why, and the statistical statement, are owned by [`../research/metrics.md`](../research/metrics.md) § The winner's own number is biased upward.** What this lane owes: a reserved per-dataset partition the loop never scores on, and two readers pointed at it — `verify` (which already re-scores a frozen candidate without touching the cycle, so it is the closest existing shape) and the reported fitness in `export.json`, whose provenance block advertises a deployment estimate it cannot currently claim. The published BBEH comparison is not what this fixes — its split already satisfies the requirement ([`../research/bbeh-comparison/README.md`](../research/bbeh-comparison/README.md) § The protocol).

Sequenced with the publication lane, not before it: an in-sample headline is wrong in a direction that flatters us, so it costs credibility at publication rather than correctness in the loop.

### Evolving agent harnesses
**Tracked as C7, deliberately after v1.** The artifact to evolve becomes an **agent harness** — an agent's prompts, tools and control flow — rather than only a single LLM call or a declared pipeline. **Ask the operator WHOSE harness — dsh or LangChain — before designing the connector**: it is their call and it fixes the mutation surface, since LangChain exposes system-prompt blocks, tool descriptions and middleware ([related-work.md](../research/related-work.md) § Adjacent work has that loop run by hand) where dsh exposes plugin manifests and a tool registry. The gap is small by design: a harness is a configuration space like any other, and `Connector` already abstracts what a backend *is*, so this is a new connector + a new mutation surface, not a new loop. The priority is low; it does not compete with Lane A or the publication lane.

Read the neighbours before designing — SkillOpt, DarwinX and AutoDesign already evolve harnesses for a frozen model, and DarwinX states our own one-armed-search argument back at us: [related-work.md](../research/related-work.md) § Agent-harness evolution. What that comparison leaves standing is what a PromptPotter version must keep rather than re-derive — sequential elimination, cost-per-fitness, subset-invariant ability. Their benchmarks are agent environments outside the connector boundary and PoBB's cost model, so adopting the target does **not** mean adopting their evaluation suite.

### Schema-description axis — the one open step
Why the schema steers at all is [`../concepts/structured-output.md`](../concepts/structured-output.md). **Open: `new --sweep-batch` it on `justlogic-d234`** — promote at `proxy_lift_corr ≥ 0.6`, and a negative result closes the axis by reverting it.

### Fitness comparability — the slice-4 remainder
Open: the **cross-round headline surfaces** + the lineage `/N` badge, and **feeding graduated discrimination `aₛ` into `select_round_subset`**, which is still 1PL ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md)).

### Exit gate
`rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`; `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches (or modify the rules).

### Host coupon + BYO per-user API keys
**The whole mechanism is owned by [`ADR-0003`](../adr/0003-spend-and-tenancy.md) § Host coupon + BYO keys** — `grant.json`, `key_source`, the three-step resolution order and its two 422s, `TenantApiKeyStore`, and the auth-router verbs. The shape in one line: the host runs users on its own keys up to a per-user coupon; past it a user uploads their own key and continues on their own money. The coupon protects the host wallet, `jobs/quota.py` protects the machine, and the two stay separate.

What belongs here is status and the two sequencing decisions. **None of the coupon exists in code today** — no `HOST_ALLOWANCE` member on `StopReason`, no `grant.json`, no `TenantApiKeyStore`; `key_source` is declared in the asyncapi only. **D1:** the coupon REPLACES the free-tier path (`admit_launch` + the `User.*_total` ceilings deleted — one wallet gate, not two). **D2:** the per-cycle `BudgetGate` is to read coupon-remaining live, re-summed every tick, which is what closes today's launch-snapshot-only gap.

### Anonymous preview tier
Spec only — **do not build it before the chat backend exists** (`presentation/api/routers/chat.py` is absent), because an anonymous meter with no conversation to meter is a writer with no reader. The reachability it plans for is the public site's chat answering a visitor who has not signed in.

- **Its own issuer sentinel, and `is_anonymous` asked BEFORE the operator arm.** This is the trap, not a detail: `quota.py::spends_the_hosts_own_key` reads a missing issuer as "came through the terminal, which only the operator reaches", so an anonymous identity built by simply leaving the issuer unset resolves as the box operator and is metered by nothing at all. The sentinel is what makes the two distinguishable; the ordering is what makes the distinction bind.
- **Empty capability set — anonymous does absolutely nothing.** No command verb, no launch, no ingest, no dataset write. It converses and it reads; every action surface is a sign-in prompt. Nothing is added to `CAMPAIGN_CAP_BY_TIER` for it, so `_require_capability_for` refuses each verb without a per-verb exception to keep in step.
- **One shared tenant.** `UserStore.get_or_create` writes a `user.json` per tenant, so a tenant per visitor is an unbounded directory-creation surface reachable without authentication. One tenant, one account row, one ledger — which also gives the spend a reader for free: it is a single row in `jobs/install_spend.py::read_install_spend`.
- **A global daily pool is the ceiling; the per-visitor allowance is UX.** A per-visitor cap is defeated by discarding the visitor — new address, cleared cookie — so the only figure that binds is one install-wide daily pool (its own `Settings` field, alongside `FREE_TIER_SPEND_CAP_USD`). What the widget shows a visitor before asking them to sign in is a courtesy, never the boundary. `admit_launch`'s reservation does not apply: an anonymous turn is one call, not a campaign, and anonymous cannot launch a campaign at all.
- **No trustworthy origin signal ⇒ refuse the request.** Serving something that presents nothing linkable is what turns the pool into a scrape budget assembled from millions of small turns.

BYO keys are the escape hatch past both tiers and are likewise **spec only** — they need `TokenUsageRecord.key_source`, which is the coupon lane's item above.

### Operator-steered fork
Two rules survive the build. `config_overrides` carries the fork's whole `OptimizationConfig` delta — run limits **plus** the policy toggles — so **a fork at offset 0 A/Bs a behaviour knob in isolation** without moving the global default; that is the standing "behaviour-knob change → sibling cycle" workflow. And `max_rounds` on a fork is an absolute target, not a remainder: the fork's counter continues from its parent.

### State-sync
**Teardown-only design was rejected — do not re-propose** (it reverses the folder-UI §0 commitment). The two state surfaces it concerns are owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

### Run admission + concurrent serving
Campaigns run in sequence behind the atomic `JobRegistry.reserve` slot (`capacity = settings.MACHINE_RUN_CAPACITY`, 1 today).
- **Open / gated — `capacity > 1`:** hard predecessors are **Lane A2 (BYO per-user keys)** + a **per-tenant `RateLimiter`** (today process-global) + backend throughput. Raising capacity before these cross-bills the shared key and throttles everyone.
- **Open — durable cross-process lock:** the in-process lock only guards one process; `--workers > 1` or web↔CLI mutual exclusion needs a disk CAS slot file (`O_EXCL` create + heartbeat + stale-reclaim). Same `capacity` knob; durable substrate.
- **Distinct axis — sample look-ahead *within* one run.** `capacity` is how many CAMPAIGNS the box admits; look-ahead is how many SAMPLES one candidate's walk holds in flight, so it does not cross-bill and is gated separately on `campaign.lookahead`. **Open:** the ceiling is a hand-set constant per connector rather than measured peak-RSS headroom (there is an OOM post-mortem in `runner/inner/spawn.py`), and inner and outer still share the process-global `RateLimiter` with no per-tenant slice. Boundary: [`../operations/access-model.md`](../operations/access-model.md) § Tier 1a.

### Lineage mask
The read side is [`../operations/mask-projection.md`](../operations/mask-projection.md); code SoT `application/mask/`. The **write side — fork-from-divergence — shipped**: `fork-cycle` carries `keep_rounds`, which swaps the offshoot trigger for `OPERATOR_REWIND` so rounds `0..N-1` are lifted, and `ConfigOverrides.scoring` carries the criterion into the fork's effective config. The alternative branch still materialises only as a real, measured fork the operator chose to run — never a stored forecast tail — which is why persisted mask identity is *still* not a thing: the criterion rides the seed the fork already writes.

Open here as **Lane C8**: the SAMPLE-SET half, and it is a different substrate rather than a second knob. A scoring mask forks on the existing rails because it re-selects a *candidate* from arms the run measured; a mask that changes which samples a round buys needs a replayable measurement ORDER seeded into the fork, which `CycleSeed` does not carry and `ForkSpec` does not root at. Until it does, a sample-subset mask previews and stops there.

### Rebase forfeit — pricing WHERE L2 rewinds to
UCB1 over normalized Rasch θ decides where a rebase fork re-expands from (`mask/backprop.py::select_rewind_round`) with **no cost term at all**, so an ancestor deep in the tree looks free. It is not, and the operator's own framing of the loss — "go back to the start and you lose all the data" — is wrong in the half that is easiest to believe: measurements are content-addressed by `(node_configs, sample_id)`, so everything already measured replays free at any cut. What a rebase actually forfeits is (1) **search position** — the θ climbed between the target and the frontier, which mutations from the older parent must re-earn; (2) **evidence leverage** — visits above the cut stop informing the new branch; (3) **future cache misses** — candidates minted from an older parent are new content hashes, and the replay fraction is predictable from observed reuse at comparable cuts (`LineageNode.cached_samples`), so it prices as an expected re-spend. Against all three sits the countervailing gain: a target whose adjacent child edges differ by ONE data-affecting knob (`application/knobs.py::classify_config_diff`) buys a controlled one-parameter contrast worth more than its raw θ.

Shape: a third pure fold beside `divergence.py` and `scenario.py`, over the same `SpineCycle` list UCB already loads (no round document opened), emitting a per-candidate-cut `ForfeitReading`. **Served diagnostic FIRST** — stamped on the tree's course nodes so the operator sees the price of each WHERE — and only then folded into the acquisition as `Q − λ·forfeit`, λ a settings constant beside `UCB_EXPLORATION_C`. It never reaches L2 as prose: WHETHER to fork is the LLM's call and WHERE stays UCB's arithmetic, so no panel enumerates ancestors.

### Plus-backlog (opportunistic, unscheduled)
Hard-Sample Sorter Phase 2/3 · Webapp Perf: SSE client cutover for the **dashboard** (the *chat* already consumes `events:subscribe` via `useCycleEvents`; the dashboard still 2 s-polls), SWR/TanStack, strip redundant memos under React Compiler (keep `l1RoundsKey` fingerprints) · MCP server mode · research extensions.

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
