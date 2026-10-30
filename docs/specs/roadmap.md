# Roadmap

Forward work in execution order, each item with the check that closes it. Why a boundary sits where
it does is [`../decisions.md`](../decisions.md)'s; debt is [`code-debt-cleanup.md`](code-debt-cleanup.md)'s.
Any new endpoint is multi-tenant by default.

## Sequence

**Waves, auto-dev runs them top to bottom** (each arc its own branch, closed on the operator's word).
Every wave pairs $0 code arcs (up to three agents at once) with paid runs supervised beside them, so
neither waits on the other. A wave's paid runs need only what an earlier wave landed.

1. **Trust the instrument.** Code: crossover main/donor; per-account snippet packs; the check-in
   address leftovers; then the inputs that change a run but leave no event, and the three probed
   invariants ([`code-debt-cleanup.md`](code-debt-cleanup.md) § URGENT — shapes that hide the backbone
   from a reshape). Paid: the CAPO head-to-head
   on `hotpotqa-closedbook`, closing M13 (§ The bench (M13)); L4 validation (~$1).
2. **Recombination at $0** (C9): the member backbone (code-debt § The member backbone). Paid: the
   mode head-to-head (§ Variation and pools, item 1).
3. **The optimizer climbs** (§ Optimizer tuning — the standing board). Paid: the board's path, steps
   (1)–(4).
4. **The preprint's evidence (M14).** Code: the N-arm mint verb; `bbeh` re-ingested with a wider bench
   partition; third-party reachability (§ Order, item 7). Paid, priced in dollars and wall clock first:
   the comparability run, then the budget dimension (§ Order, items 5 and 6); the ablation rows.
5. **The hosted beta (Lane A):** A1 first-run exhibit, built on a wave-3 campaign that climbed; A2 host
   coupon + BYO keys; A3 anonymous preview.
6. **Capability (Lane C), by dependency:** C2 composite fitness → C4 cross-user panel; C2b judged
   scoring; C3 L4 closure; SIFT S1; the PUCT selector (§ Selector members — parent choice as one acquisition); C5, C6, C8, C10; C7 after v1.

**Rides whenever it unblocks:** the webapp vanilla pass (code-debt § NEXT ARC), once the run-card
session's files land; the operator's picked renames.

Lanes by dependency. Front priority: Lane A and the bench lane (M13), concurrently; C9 ahead of
the rest of Lane C.

### Lane A — the hosted beta, usable by a free-tier account end to end

- **A1** First-run exhibit — a finished campaign a new account reads before supplying anything (§ First-run exhibit).
- **A2** Host coupon + BYO per-user API keys (§ Host coupon + BYO keys).
- **A3** Anonymous preview tier — the public site's chat before sign-in (§ Anonymous preview tier).

### Lane C — differentiator and capability

- **C1** Chat-first front door — [`chat-foundation.md`](chat-foundation.md).
- **C2** Composite fitness P2–P4 (§ Members the presets need).
- **C2b** Judged + turn-structured scoring: the per-step ruler (§ Judged and turn-structured scoring).
- **C3** L4 closure — [`l4-outer-loop.md`](l4-outer-loop.md) § Open.
- **C4** Cross-user measurement panel, after P3.
- **C5** MCP server mode (§ Agent-tool parity) · the editable `dataset.yaml` `pipeline` write side · the connector as a unit (§ The connector as a unit).
- **C6** Public-service hardening: Docker, metrics, a rate on unpaid writes (reads are metered), billing; a bound on the NUMBER of accounts.
- **C7** Non-prompt targets · agent harnesses (§ Evolving agent harnesses) · multimodal; all but harnesses after v1.
- **C8** Mask abstraction — the sample-set half (§ Lineage mask).
- **C9** Variation and pools (§ Variation and pools).
- **C10** Operator-authored prompt-field order (§ Prompt-field order).

**M14 (publication)** needs: ablation rows L1 / L1+L2 / full · scan · cross-run archive · critique ·
zero-signal-filter; competitor and L4 numbers after C3. Admission bar and roster: [`../research/benchmarks.md`](../research/benchmarks.md).

## The bench (M13)

**M13:** every optimizer, peers included, is a manifest the bench runs, inheriting measurement,
archive, spend ledger, persistence, the five entry points and the control plane; the headline is the
held-out bench score. It closes when ONE peer (CAPO first) runs end to end in a head-to-head whose
only difference is the optimizer. **M14 (the preprint):** the stopping rule and the comparability
guard are executable here and every peer ran under them ([`../../paper/`](../../paper/README.md)).

The contract is [`../architecture.md`](../architecture.md) § Bench and optimizer; node types are
[`../developer/node-standard.md`](../developer/node-standard.md) § Optimizer node types. Two rules a
member must keep: **it is handed its node's `NodeContext`, never `Cycle`** (a store handle or live
client in its input moves the core boundary and the trusted-code story); **its own sends are
admitted before they leave** (`infrastructure/llm/spend_book.py::admitted`).

- **The compare pane does not render the head-to-head headline** (`GET /evidence::head_to_head` serves it).

### Order

5. **The comparability run** (PAID): BBEH, one target model, one grader, one bench set, arms
   `optimizer ∈ {potter, capo, levi, gepa}`, calls held equal, 3 seeds; paired per-cell differences,
   clustered SEs, Holm/BH across the matrix. The controlled reading (one declared head-to-head) and
   the uncontrolled one (each peer run its own way, the notebooks) are never substituted. Owed: `bbeh`
   re-ingested with a bench partition wider than 100 rows; an N-arm mint verb over
   `application/jobs/mint.py::_join_head_to_head`.
   Price it in dollars and wall clock before asking.
6. **The budget dimension** (PAID, after 5): the same arms at two or three budget points — the
   budget-matched Pareto comparison reviewers ask for first.
7. **Third-party reachability**: a scratch-venv example package with one entry point and one
   proposing node. *Closes when* `optimizer: example` runs a round whose arms carry
   `source="example:propose"` with zero edits inside this tree.

### Members the presets need

- **Composite fitness (C2).** P2 per-candidate rollup + scatter panel (after P3) · P3
  `compile_post_aggregate_fitness(formula)` + `campaign.scoring_post_aggregate` · P4 multi-objective
  racing: a `pareto` selector with an eliminator reading the front.
- **SIFT's members** (§ SIFT reproduction): the self-proposer (the parent agent on its own source
  and logs, one patch out); the archive-wide parent selector `P(i) ∝ exp(−α·r_BT − β·r_acc − η·log(1+v))`,
  whose inherited-accuracy rule may steer a choice, never become a measurement; a candidate-level
  pairwise judge + regularized Bradley-Terry; the `fixed_gate` eliminator.

### Selector members — parent choice as one acquisition

Potter's selector (`application/scoring/selection.py::elect_round_winner`, greedy) is UCB1 rebase
(`application/mask/backprop.py::select_rewind_round`) with the exploration term at zero. Direction:
**one PUCT acquisition `Q + c·P·√ΣN/(1+N)`**, P a predicted lift in θ units on `L1Variant`
(never a normalized preference). `fork_proposal`'s contract does not change. No learned value model
until a surrogate's held-out error is measured below the lifts the loop chases; Q stays measured θ.

1. **Bank the predicted lift as an inert field** and compare it with realized θ lift — built first,
   shipped with its display (one mark per arm, read by state; an arm the eliminator cut shows its
   prediction as resolved without measurement).
2. **Serve what the acquisition cannot otherwise report:** visit concentration, value vs exploration
   term, prior entropy across arms, calibration trend, and ruler provenance on a backpropagated Q
   (`NodeStats.value_sum` mixes scales under `prior_pinned`).
3. **The rebase forfeit as its cost term** — a pure fold beside `divergence.py` emitting a per-cut
   `ForfeitReading`, served first, then `Q − λ·forfeit`; lands as ONE function with the acquisition.

Open: what stands in for θ on unmeasured children; forest vs round normalization; whether a cut arm
is a legal parent; names (`selection`, `prior`, `value_sum` → `theta_sum`).

## SIFT reproduction

SIFT-exact is a `sift` preset (a frozen sampler, `fixed_gate`, no controller, one variant per round),
and the paper is the ablation ladder from it: one potter member swapped in per arm at one budget.
Models: the dataset's own; S3's agent `qwen/qwen3.7-flash`; the candidate judge `openai/gpt-oss-120b`.
Code-artifact candidates (a content-hashed file set loaded per episode) are the design-bearing gap.

- **S1** — the judge, parent selector and `fixed_gate` on a single-call dataset (`justlogic/d234`),
  opening with the judge's ranking against realized θ (bar ρ≈0.68). *Closes when* SIFT-on-prompts vs
  SIFT-on-prompts + PoBB is served by `evidence`.
- **S2** — the self-proposer. *Closes when* an `optimizer: sift` round proposes through it.
- **S3** — code artifacts + Polyglot, only if S1 makes it worth it; the sandbox is decided then.

## Optimizer tuning — the standing board

The goal is the price of a unit of optimizer progress, per dollar and per minute. How to work it is
the `potter-self` skill's; numbers are [`../research/benchmarks.md`](../research/benchmarks.md)'s. The
operator's bar: at least +10 points on a held-out bench in about five rounds, then the same lift in
less wall clock, on an official dataset with a natural origin below 0.4. A one-key task is a smoke test.

**The path:** (1) replicate the nearest climbing setting and read what the proposer saw of the
misses; (2) screen library blocks without the optimizer — authored arms, origin plus ONE block;
(3) the optimizer-model screen — re-run the halted `qwen3.7-flash @ none` arm; (4) combine the winners.

**Defects** are [`code-debt-cleanup.md`](code-debt-cleanup.md) § Open — optimizer tuning.


## Variation and pools

The contract is [`variation-and-pools.md`](variation-and-pools.md); why is
[`../decisions.md`](../decisions.md) § 0007. Part of M13; each M14 ablation row quotes
`rounds_to_separable`. It must not become a fifth sealed optimizer, a second overlay beside
`optimization.nodes`, a pool written by a file edit, or a credit model steering before a reading
shows it informative.

1. **The mode head-to-head** (PAID), the host the only difference, matched on USD and cells.
2. **L4 reach** — the mix as an outer-arm lever, on the inner cell's overlay and identity.
3. **A pool fed while a campaign runs** — refused by a controlled arm, applied at the next round
   boundary, replayed on resume.
4. **Paper compositions** — EvoPrompt GA/DE, PhaseEvo, GEPA's merge, each stating its departures.

Stages 2, 3 and 4 share no seam.

## Probe rounds

A probe round spends its budget on ONE narrow question: more candidates on a narrower question.
`L2ContextOutput` carries no `action` field for it. Build it as: the target picked from measurement
(a peaked axis, a recurring failure cluster), `n_variants` widened for that round, samples chosen by
relevance to the hypothesis — never by a set that is empty on a healthy run. Re-running warned
queries is degradation triage, not a search action.

## Origin-resolution check-in

Invariants: it reuses the `checkin/2` node; `reasoning_floor`/`ceiling` stay off the operator
surface; model and provider are optimizer-locked.
- **Run mode** — the same node raises `pause-cycle` / `change-run-limits` / `fork-cycle` over
  `application/datasets/origin_resolve.py`'s `RaisedCommand`. Lands with L4.
- **The whole setup proposed from the operator's text** — every node's model and effort, rounds and
  cap — priced against the served menu and `application/jobs/quota.py::launch_usd_bound`; the host's
  bound outranks the operator's words; an off-menu proposal is dropped at `_coerce`.

## Connectors

- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types, L1/critique
  injection-echo validators, a repeat-detection circuit breaker.

### The connector as a unit

Operator direction, not yet a design. **The operator picks a connector, not a backend URL**: each
registered connector shows its status, its pipelines and their modes (Harbor's skill delivery is
advanced or primitive).
- **One declaration schema**: `Connector` (`connectors/protocol.py`) served by `GET /connectors` as a
  generated type, and the webapp draws every connector from that alone; nothing branches on a name.
- **The chat hero is the connector's picture**, connector ⊃ pipeline ⊃ node; where you click picks the level.
  Advanced mode draws the skill as a tool attached to the agent, served from `Connector.prompt_delivery`.
- **The pipeline choice rides the existing channel** (`DraftPatch.pipeline_steps` → … → TermNorm's
  `steps`); `Connector.default_pipeline` widens into a named set with one default.
- **A mode is a node-config key with declared options**, never a search axis, always in identity.
- **Check-in adds no `EditDraftPatch` field**; owed: validation against the declarations, and
  `draft_from_dataset` accepting a verifier-graded dataset. A running campaign never switches in place.

Order: (2) `Connector` declarations + `GET /connectors`, status from `Connector.preflight`; (3) the
TermNorm toggle moves into the connector panel; (4) the dashboard inspector reads the same object;
(5) the C5 write side rides the same overlays, and `change-pipeline-param` is re-scoped or deleted.

## Agent-tool parity

An operating agent calls PromptPotter as one tool: the MCP server exposes mint, run, supervise and
read. **Held on the inbound credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
§ 8. The first caller is HuggingChat: a user-added server carries only a name and URL, so ours
authenticates without headers; results are terse named fields, never a document; `run` returns an id
at once and a status tool reads it. The tool set scopes along [`../decisions.md`](../decisions.md)
§ 0005's capabilities; `purge-cold` stays out.

**Sandbox:** NVIDIA OpenShell is the sandbox seam, at the containerized cell. **Coding-agent
optimization is deferred** until we have an expert or the field converges.

## What PromptPotter emits

**We write a file and provide a reader; we never load, host, route or hot-swap.** DSPy is the
reference read before designing any ecosystem expansion. The artifact and its reader contract are
[`../developer/stable-api.md`](../developer/stable-api.md) § 5c.

- **`to_dspy`** on the `[dspy]` extra beside `presentation/teleprompter.py`: applies the winner to a
  live program rather than emitting DSPy state.
- **Langfuse write-back** to Prompt Management (the MLflow half exists — [`../operations/observability.md`](../operations/observability.md) § MLflow).
- **A DSPy listing PR** pointing users at `PromptPotterOpt` beside `dspy.GEPA`.
- MLflow as a second viewer: [`mlflow-interop.md`](mlflow-interop.md).

## Judged and turn-structured scoring

What is live is [`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md)'s. A
rubric must vary across arms on the same cell before a campaign funds it. Open:
- **Per-step difficulty**: δ and `a` per step under a testlet model (a person×testlet effect; the cell
  stays the atom, since a turn is not an item), gated on identifiability at ~20–34 cells a round, a fixed
  semantic step schema and a new `RulerScope`; it needs each step's term banked as its own `pipeline_data`
  key, never collapsed to a scalar.
- **A declared step as a NODE**, so `campaign_config.exclude_nodes` is the step selector (generalizes
  `connectors/harbor.py`'s `AGENT_NODE`); not running later steps (`_unscoreable_step` raises on a truncated one).

## Evolving agent harnesses

The artifact becomes an agent harness, through `connectors/harbor.py`. Do not re-open "whose
harness": Harbor sits below every harness. The target is a **skilled agent** — many skills, each
maintained and measured separately; measured lift is the only admission ticket.
- **Tier 1 validator** — a `validators/l1_strict.py` rule rejecting a skill body over 500 lines
  before it runs; today only the L2 prompt names the bound.
- **The no-skill arm beside the search** inside one cycle.
- **Skill routing** before the library reaches a few dozen skills.

## Small open items

- **Schema axes** — contrast `description` prose and `response_format` on `justlogic/d234` via
  `evidence --grid`; promote at `proxy_lift_corr ≥ 0.6`, a negative result reverts the axis.
- **Fitness comparability** — the cross-round headline surfaces and the lineage `/N` badge.
- **Exit gate** — `rounds_to_separable ≤ 5` on `llm_only` AND TermNorm under one `l1_generate_hash`;
  `behavior_pass_rate = 1.0` seeded; `proxy_lift_corr ≥ 0.6` over ≥4 paired branches. The clock is
  separability, never an absolute accuracy.
- **Run admission** — full speed for two busy users needs more provider quota (A2; an `api_key`
  dimension on `get_llm_client`'s cache key); look-ahead's ceiling is a hand-set constant per
  connector, not measured RSS headroom; a horizon priced in expected waste would launch deeper.
- **Cheap top-up** — a second `PanelTrigger`, blocked on the rule that prices a missing row against
  the round's marginal draw cell.
- **CPU-hours** metered per phase beside `SpendRollup`, for every connector.
- **Origin-relative weighting** — each archive observation weighted by its producing candidate's
  similarity and recency; `Observation` carries a timestamp and lineage hint once archive-wide drift or
  a per-node capability curve is chosen.
- **Delegation, open** ([`../decisions.md`](../decisions.md) § 0005): who edits grants; how a
  channel is identified unspoofably; whether the user's own assistant is attenuable; depth beyond
  one level; whether a delegate is its own tenant.

## Lineage mask

The read side and the fork write side are [`../operations/mask-projection.md`](../operations/mask-projection.md)'s.
**Open (C8): the sample-set half.** A mask that changes which samples a round buys needs a replayable
measurement order seeded into the fork, which `CycleSeed` does not carry; until then a sample-subset
mask previews and stops.

## First-run exhibit

The demo: write down ten films you like, get better recommendations than the starting prompt, and
the prompt. Bars: most people do it; platform-independent; no private data; a right answer
(held-out titles, scored by `list_rr`). Before a round, compute the chance floor of the draw — the
first cut's origin read below it. **MovieLens cannot ship** (no commercial use or redistribution):
collect our own rows, cut under a name nothing has run before. Served from an install-tier campaign
root behind a `demo_mode_enabled` reader.

## Host coupon + BYO keys

The host runs a user on its own keys up to a per-user coupon; past it the user uploads their own key
and continues on their own money. The coupon protects the host wallet, `jobs/quota.py` the machine.
- **The coupon remainder is derived from the host-key ledger, never a counter** (`grant.json`); keys
  sit in `TenantApiKeyStore` (`api_keys.json`), both on `read_json_optional` / `write_json`.
- **`resolve_api_key(identity, provider, stores)` wraps `get_llm_client` as the one choke point**:
  the user's own key for the provider wins with no coupon check; else a live coupon uses the host
  key; else 422 `host_allowance_exhausted`, distinct from 422 `no_api_key`. Per provider, unless
  `settings.coupon_void_on_byo` retires the coupon.
- **The coupon replaces the free-tier path** (`application/jobs/quota.py::admit_launch` + the `User`
  lifetime ceilings) in the same change, keeping both units (USD and tokens) and the per-run
  reservation; the `SpendBook` caps live at the remainder, stopping on a new `StopReason.HOST_ALLOWANCE`.
- **`TokenUsageRecord.key_source: host|user`** — the one exception to identity-from-path, since it
  records which wallet paid; `/auth/activity` `group_by=api_key` then groups by whose key.
- `/auth/api-keys` and `/auth/coupon` ride the auth router, not `/commands/*`.

## Anonymous preview tier

Do not build before the chat backend exists.
- **Its own issuer sentinel, `is_anonymous` asked before the operator arm** —
  `quota.py::spends_the_hosts_own_key` reads a missing issuer as the box operator.
- **Empty capability set**: it converses and reads; every action is a sign-in prompt.
- **One shared tenant** (a tenant per visitor is an unauthenticated directory-creation surface).
- **A global daily pool is the ceiling**; a per-visitor cap is UX.
- **No trustworthy origin signal ⇒ refuse the request.**

## Prompt-field order

Today a node's field order is its declaration. Proposal: the check-in carries an order per prompt
node beside its fields (`DraftCampaign.origin_prompts`), the editor drags it, `edit-draft-campaign`
persists it, a fork inherits it. **Open (operator): an optimizer axis or an operator setting?** Held
is the smaller change. Reorder re-keys every cell, so order is a searchpoint property (never
`localStorage`), and a descendant reading under a changed order renders `?`. The drag surface: a new
`components/ui/` primitive on Pointer Events, keyboard parity (`↑`/`↓` with an `aria-live`
announcement), commit on drop, the handle the touch target.

## Run card

Collapsed, the run card is the compact card (round axis, trend glyph, heatmap, subject, hardest
samples); `more` swaps in before/after against the origin, the evolution chart with the lineage, and
the two-row sample heatmap. Rendered from the served hierarchy (`domain/hierarchy.py`), a fork drawn as
an event at its spawn point. Owed: the round that changed each key, served ("changed in C7.3").
Rejected: the round rail of hollow/filled bars and the ring readout.
