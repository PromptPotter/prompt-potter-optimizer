# Roadmap

Forward work in execution order, each item with the check that closes it. Why a boundary sits where
it does is [`../decisions.md`](../decisions.md)'s; debt is [`code-debt-cleanup.md`](code-debt-cleanup.md)'s.
Any new endpoint is multi-tenant by default.

## Sequence

### The cleanup chains (wave 4)

The process is [`../developer/refactor-arcs.md`](../developer/refactor-arcs.md)'s. A landing's chains
start off the landing below it, main moves on the operator's word, and the end is the ledger at its
header, then the operator asked about a release.

1. **batch/21** — the docs trim and the ledger: w0, t-dev, t-docs, t-claude, t-skills.
2. **batch/22** — tracing goes; every record kind is read by a roster fold; the landing at every
   process entry: stream, stream-b, land, reads, weba, visits.
3. **batch/23** — one launch answer, one change feed, one narration: startb, feed, narr.
4. **batch/24** — one stop door, the connector declares itself, the doors: stopb, conn, doors, tv.
5. **batch/25** — run init as one module, one pass verb, served arm rows: init, passb, webb. The
   decision window opens.
6. **batch/26** — every re-key: money, rk-opt and stream-rk on its tip (`RunCallbacks` and the
   `declare_*` doors), rk-run, addr; then rk-dec; then rn-conn. The window closes.
7. **batch/27** — records and layout: rec, la, lw; md if the operator says so.
8. **batch/28** — lb, then faults on its tip.
9. **batch/29** — lite (LiteLLM out of core), xconn, mkey, then mcp on mkey's tip.
10. **batch/30** — ws, forks, the block picker's census.
11. **batch/31** — tune, small, pick, box (the operator runs it).
12. **batch/32** — ts-arch, ts-score, ts-spend; then ren; then rel and `gate.py --release`.

**Block picker:** the pool pick already rides the draft. What is left: earned blocks offered at
check-in, their lift and n kept, the origin field's snippet ids served, the provenance chip (wave 4, pick).

**Rules.** No paid run between batch/25's start and batch/26's push. From batch/22 the landing drops
unloadable run data itself; a survivor reshape ships its migration in its landing; the live box asks.
A short window defers from the end (pending tracks, ren, faults, the ts private-reach link, lb, la, lw),
never w0, the trim, stream, startb, stopb, land, init, money, rk-opt, rk-run or rel.

**Forks left to the operator** (taste and policy; a chain leaves each as it stands): whether `log.md`
and `review.md` go (md) · whether the visits analytics move to the site repo · what "calls held equal"
holds equal and the crowning bar's statistic (rk-dec) · the sub-cent money rule (forks) · which env
file the box keeps (box) · a bench's total · whether an upload's bytes are kept before the parse · a
prompt field's order · the host assistant's open questions (§ Host assistant).

### After the release

1. **deep**, the first feature. 2. **Recombination at $0** (C9), then the mode head-to-head (PAID).
3. **The optimizer climbs** (§ Optimizer tuning — the standing board). 4. **The preprint's evidence**:
the bench track first; the N-arm mint verb; `bbeh` with a wider bench partition; anytime-valid
confidence sequences as the default stopping rule, PoBB selectable
([`../research/external-constraints.md`](../research/external-constraints.md) § Ranked, item 2); then,
priced first, the comparability run, the budget dimension and the ablation rows. 5. **The hosted beta**:
A1, then anon. 6. **Lane C** by dependency. 7. **Embedding** (§ Embedding (M15)), R1 first.

#### deep — a managed deep-agents node

A node kind beside `llm_only` running a planning and tool loop in process through `InProcessRun` (the
seam `connectors/dbllmbench.py` uses), every send through the one sender and book, its prompt fields
optimizable like `llm_only`'s; its dependency asked before any install. *Closes:* a definition naming it
runs offline under `offline_world`.

#### bench — five head-to-head rows

The three INVESTIGATEs (nobody has shown them real) read banked ledgers on the desk and the box first;
each ends as a rule built or a closed note.
- **INVESTIGATE — does an errored cell bias a head-to-head?** `gpt-oss-20b:nitro` can end
  `finish_reason=error` then pass one schema repair; unknown whether the row banks the answer or a miss.
  *Re-test:* per arm, the hit rate of error rows against the arm's other rows.
- **INVESTIGATE — CAPO's survivors are ranked on another reading than the reference ranks them on.**
  `capo/members.py::_rank_survivors` keeps μ best-first by mean objective on the cells all survivors
  measured; promptolution's CAPO sorts survivors on raw score over every cell each was evaluated on,
  cached across rounds, so an incumbent's deeper blocks from earlier races count. After a one-block race
  the two can crown differently. Open: which reading the bench compares. *Re-test:* on a CAPO run with
  offspring < μ, rank one round's survivors both ways.
- **An optimizer's round price is its name's, not its treatment's**
  (`application/head_to_head_price.py::optimizer_round_price`): the three `escalation_ladder` arms read
  one potter price, though an `l1` round sends fewer optimizer calls than a `full` one. Action: key it on
  the treatment once enough campaigns of each have run. *Re-test:* the ablation's optimizer line names
  one price for three ladders.
- **INVESTIGATE — a CAPO arm is admitted on its round CEILING** (`preflight.py::check_cap_funds_round`
  over `round_cells_ceiling`: 4,230 cells on 310 search rows), which its race rarely spends, so a budget
  point must fund the worst case to mint CAPO at all. *Re-test:* a finished CAPO round's cells against
  its ceiling.
- **A declared bench is not the held-out count**: `{bench: 100}` held out 162 rows on the box's
  `hotpotqa-closedbook`. No shipped recipe adds a `held_out` tail and the loader dedups rows, so the 62
  are in the box's tenant definition; the one code path holding out past `bench` is
  `domain/bench.py::partition_bank`, which L4 inner cells (`bench: 0`) and `justlogic/d234-held` are
  built on. Open: read the definition (`/potter-box`), then decide whether `bench` becomes the total; it
  moves a dataset cut, so it lands with a re-key. *Re-test:* a fresh `{bench: 100}` campaign on that
  definition holds out 100.

#### anon — the anonymous upload

A visitor uploads and previews a dataset before sign-in, and nothing paid runs before it; an
unauthenticated write door, so [`../operations/access-model.md`](../operations/access-model.md) first and
a security review. Its own issuer sentinel, `is_anonymous` asked before the operator arm
(`default_claim.py::is_host` reads a missing issuer as the box operator) · no capability but upload and
preview · one shared quarantine tenant, the upload held under an unguessable claim token that sign-in
claims · a global daily pool and size and row caps · no trustworthy origin signal ⇒ refused.

### Lanes

- **Lane A, the hosted beta:** A1 the first-run exhibit · A3 the public site's chat before sign-in, on
  anon's tenant · A4 the arrangeable workspace.
- **Lane C, capability:** C1 the host assistant (§ Host assistant) · C2
  composite fitness P2–P4, then C4 the cross-user panel · C2b judged scoring · C3 L4 closure
  (§ L4 closure) · C6 metrics and billing · C7 agent harnesses · C8 the mask's sample-set half · C9 variation
  and pools · C10 prompt-field order.
- **The preprint** needs the ablation rows L1 / L1+L2 / full (`paper/plans/ablation.yaml`); competitor
  and L4 numbers after C3 ([`../research/benchmarks.md`](../research/benchmarks.md)).

## The bench

Every optimizer, peers included, is a manifest the bench runs, the held-out bench score its headline
([`../architecture.md`](../architecture.md) § Bench and optimizer; the paper's side is [`../../paper/`](../../paper/README.md)).

### Order

1. **The comparability run** (PAID): BBEH, one target model, one grader, one bench set, arms
   `optimizer ∈ {potter, capo, levi, gepa}`, calls held equal, 3 seeds; paired per-cell differences,
   clustered SEs, Holm/BH across the matrix (`paper/plans/comparability.yaml`, priced first).
2. **The budget dimension** (PAID, after 1): the same arms at two or three budget points
   (`paper/plans/budget-dimension.yaml`).

### Members the presets need

- **Composite fitness (C2):** P2 per-candidate rollup (after P3) · P3
  `compile_post_aggregate_fitness(formula)` · P4 a `pareto` selector with an eliminator reading the front.
- **SIFT's members:** the self-proposer; the parent selector `P(i) ∝ exp(−α·r_BT − β·r_acc − η·log(1+v))`;
  a pairwise judge with regularized Bradley-Terry; the `fixed_gate` eliminator.
- **One PUCT acquisition `Q + c·P·√ΣN/(1+N)`** in place of the greedy pick
  (`application/scoring/selection.py::elect_round_winner`) and UCB1 rebase: P a predicted lift banked
  first as an inert field beside realized θ lift, Q measured θ, the rebase forfeit its cost term; no learned value model until a surrogate's held-out error is below
  the lifts chased. Open: θ for unmeasured children; forest vs round normalization; whether a cut arm is
  a legal parent.

## SIFT reproduction

A `sift` preset (a frozen sampler, `fixed_gate`, no controller, one variant per round); the paper is the
ablation ladder from it. **S1** the judge, parent selector and `fixed_gate` on `justlogic/d234`, closing
when SIFT-on-prompts vs SIFT-on-prompts + PoBB is served by `evidence` · **S2** the self-proposer · **S3**
code artifacts + Polyglot, only if S1 makes it worth it.

## TypeQL bench

[`typedb/db-llm-bench`](https://github.com/typedb/db-llm-bench) has a model write TypeQL against the
Reactome database; the query is executed and its result compared with a known answer. Built: the
`dbllmbench` connector and the `reactome-typeql-42` tenant definition's `README.md`. **The maturity
gate**, before any spend on the reported model (then the operator picks it): upstream's `verify`
reproduces every reference value through the cells' image; one origin cell banks all three retry
readings, its turns, tokens and cost; the origin's spread on the development model is pinned at neither
0 nor 1; the reading holds under `noise-floor`. **Owed after it:** the campaign on the search split; the
comparison over all 42 questions at zero, two and four retries by tier, beside their published rows and
never pooled; their `analysis/` scripts run unchanged on our output; the drop-in skill file. Their rows
are Claude Sonnet 5 and DeepSeek V4 Pro: another model is another benchmark. **Upstream offers:**
`connectors/resources/dbllmbench-repetitions.patch`, a cache block for the stable head, `--tls-disabled`
for `load.sh`. **Parked:** the tool-using agent over TypeDB's MCP server, its own dataset.

## Optimizer tuning — the standing board

The goal is the price of a unit of optimizer progress, per dollar and per minute; how to work it is the
`potter-self` skill's. The operator's bar: at least +10 points on a held-out bench in about five rounds,
then the same lift in less wall clock, on an official dataset with a natural origin below 0.4. **The
path:** replicate the nearest climbing setting; screen library blocks without the optimizer; screen the
optimizer model per method and per step ([`../../paper/notes/model-per-method.md`](../../paper/notes/model-per-method.md));
combine the winners.

## L4 closure

What is true of L4 is [`../concepts/l4.md`](../concepts/l4.md)'s. Owed:

1. **A bounded, cheap default config** — `new promptpotter-self` must complete at a cost an evaluator tolerates.
2. **`proxy_lift_corr ≥ 0.6` over ≥4 paired branches** — gated on the panel resolving one arm from another.
3. **The OUTER election is unmeasured**: round 0 holds one arm, so every claim about outer *behaviour* is untested.
4. **The arms differ less than their own intervals.** More cells, or candidates that differ more — the cheaper lever.
5. **Cross-sample terms, unbuilt:** area-under-lift-vs-budget; `mean lift − λ·std` through the post-aggregate
   formula, never the election rank key; PoBB-decisive promotion over inner arms
   (`application/scoring/selection.py::prior_read`).
6. **Self-adopt mode (unbuilt, off by default)** — SIFT's ([`../research/landscape.md`](../research/landscape.md))
   loop with our statistics in its gates: on a **decisive** outer election the winner's overrides become
   the outer campaign's `optimization.nodes` overlay and the outer cycle forks onto it; the instrument is
   never adopted. Preconditions: a held-out panel of other task families scored only by the adoption gate,
   worst-case non-inferiority per family, anchor to C0 with rollback by fork, a leakage lint on the
   adopted set. It inherits #3 and #4.
7. **Environment admission inside the run** — § Survey round.

## Variation and pools

Every optimizer's features as switchable members ([`../decisions.md`](../decisions.md) § 0007;
`domain/pool.py::Pool`). Never a fifth sealed optimizer, a second overlay beside `optimization.nodes`, a pool written
by a file edit, or a credit model steering before a reading shows it informative. A **pool** is a named
set of one entry kind (`individual` · `block` · `shot`) that nodes draw from under an **access**
(`off` · `open` · `closed`); a **variation node** turns parents and pool entries into arms. Owed, in
order: (1) `mix` on the opening node, a knob, then the mode head-to-head (PAID), matched on USD and
cells, with a fresh individual as donor separating recombination from macro-mutation; (2) L4 reach,
`mix` and access as levers (`application/optimizers/nodes.py::LlmNode.outer_levers`), the instrument
never; (3) a pool fed while a campaign runs, its writers declared, a write a record at a round boundary,
a controlled arm taking none; (4) parent and survivor rules past `tournament`, ranked on θ alone, and the
paper compositions (EvoPrompt, PhaseEvo, GEPA's merge). Never a shipped block library.

## Survey round

**Nothing is built.** The optimizer's first act is a survey round: several starts, written by
`l1_generate`, measured cheaply against the given origin (C0 stays it), the task placed between floor and
ceiling, the descent from the best; under `promptpotter-self` the dataset is an axis. It is a round of
the run, never transfer or a side screen ([`../research/external-constraints.md`](../research/external-constraints.md)
§ Survey round). One round kind, three ways in: at run init (`at_init`), elected by L2/L3 through a
`survey_proposal` gated by `survey_capability`, and an operator command; off in every manifest but the
`promptpotter-self` overlay; it varies starts, never the instrument, and never admits or rejects a
held-out seat (that is the bench). A served band verdict
(`below_floor` · `collapsed` · `saturated` · `flat` · `in_band`, a typed enum in `domain/`) gates and
never ranks; `share` bounds its spend on USD and wall clock. Build order: the verdict reproducing the
kept seed-screen sidecars; `INITIAL_POPULATION` renamed `survey`, offline legs unchanged; `at_init` and
the origin gate on the verdict; `survey_proposal`; L4 admission; the `at_init` ablation (PAID). The
`seed-screen` verb goes when it lands.

## Probe rounds

A probe round spends its budget on ONE narrow question: the target picked from measurement (a peaked
axis, a recurring failure cluster), `n_variants` widened for that round, samples chosen by relevance to
the hypothesis. `L2ContextOutput` carries no `action` field for it yet.

## Turns — the unsupervised limit

**A turn is what the system may spend on its own between two human Plays** (operator, 2026-11-02). Play
buys one turn from the allowance, admitted whole or refused (§ 0003); a spent turn PAUSES the run; the
campaign ceiling is off by default; the turn count is a ledger record served with the next turn's cost;
a Play the next turn cannot use is refused before a job launches. **Closes when** a run paused at its
turn's end resumes on one Play and a Play the allowance cannot cover is refused naming it.

## The right winner — redundant mechanisms

PoBB's per-cell bar locks in falsely about four times as often as it states
([`../../paper/notes/stopping-rule-false-stops.md`](../../paper/notes/stopping-rule-false-stops.md)). Owed,
each moving what the loop decides ([`../developer/offline-run.md`](../developer/offline-run.md)):
- **Lock in only past an anytime-valid bound**, a mixture likelihood ratio on θ at α/K per arm, reading
  the verdict direction as the election does and the parent pair on cells the parent's grades did not
  choose.
- **The standing pick's evidence pools across rounds**, and **a near-miss carries forward** into the
  next round's panel.

**Closes when** an offline replay elects no pick its later reads as parent contradict, and the pilot's
equal-arms simulation shows false locks at the stated level. **Open:**
- **Tie-breaking at budget cap.** Top 2–3 at similar `P(best)`: ship pick-by-point-estimate; design a
  cap-extension policy after seeing how often it fires.

## Host assistant

The account's one conversation, the check-in its first job, one surface for every host
([`../decisions.md`](../decisions.md) § 0012): the origin gate stays the contract and the assistant acts
as the user, through `application/host_surface.py::HOST_TOOLS`. Owed, in order: the host endpoint, a turn's steps recorded on
the account's workspace ledger; the webapp's host, a server-side loop through
`bench/llm_call.py::llm_call` under the session's identity, the composer on every screen, every tool call
one `step`, a write fired only by the user's click on one proposal shape; the check-in as the "reach the
origin gate" prompt, deleting `CheckinOutput`, `RaisedCommand` and `shell/families.tsx::familiesNamed`. **Closes when** a new account's CSV starts its campaign from the
webapp's host, and the same steps from Claude Code reach the same draft. **Open (operator):** where
threads persist (a SURVIVOR); which writes skip the click; an in-house loop or an agent SDK; its model.

## Origin-resolution check-in

It reuses the `checkin/2` node. **Run mode:** the node raises `pause-cycle` / `change-run-limits` /
`fork-cycle` (lands with L4). **The whole setup proposed from the operator's text**, priced against the
served menu and `application/jobs/quota.py::launch_bound`, the host's bound outranking his words.

## Embedding (M15)

PromptPotter is the optimizer a program calls instead of holding one. **A program, model kind or
framework arrives as a declaration, a program answering its own cells, or an entry-point plugin; no
R-item adds core code naming one** ([`../research/external-constraints.md`](../research/external-constraints.md) § M15).
**The headline setup is Langflow** for someone who has never opened a shell: PromptPotter tunes a flow
through its per-run tweaks (`LANGFLOW_TWEAKS_POLICY=declared`), one starter in `examples/` with an agent
skill walking the user's assistant through it. *Closes:* such a person finishes a campaign on their flow.

- **R1 The machine principal**: wave 4's mkey builds the account token (batch/29). Owed after it: a
  federated mint (OAuth client credentials, MCP's OAuth or a device code). *Closes:* a CI job mints a
  head-to-head with no cookie.
- **R2 Pulled cells**: `domain/connector.py::ConnectorExecution` gains `pulled`; a program leases an
  admitted cell and answers on R1's credential, so the box dials nothing a tenant names
  ([`../operations/access-model.md`](../operations/access-model.md) § loop ↔ everything). Open: whether
  `in_process` is `pulled` over a local transport.
- **R3** one versioned wire · **R4** host-declared providers (`infrastructure/llm/registry.py::_OPENAI_COMPAT_SPECS`)
  · **R5** typed parameter domains · **R6** output refs, judged where the output is · **R7** one priced
  count · **R8** the surge rehearsal, ending on a refusal the box serves · **R9** one OCI image · **R10**
  the box without its operator ([`../decisions.md`](../decisions.md) § 0004) · **R11** scale-out by
  tenant, never a shared database · **R12** the conformance kit · **R13** every request routed to one of
  five answers, where ecosystems land, a framework only as a standard · **R14** CODEOWNERS by layer.

## Connectors

- **Prompt-injection Phase 2:** `TrustedText`/`UntrustedText` renderer types, injection-echo validators,
  a repeat-detection circuit breaker.
- **The connector as a unit:** the operator picks a connector, not a backend URL. Owed: `Connector`
  (`connectors/protocol.py`) served by `GET /connectors` as a generated type, the webapp drawing every
  connector from it alone; the chat hero is connector ⊃ pipeline ⊃ node; a mode is a node-config key with
  declared options, never a search axis.

## Agent-tool parity

The MCP server exposes mint, run, supervise and read, **held on the inbound credential** (mkey; the HTTP door is mcp 2). The first
caller is HuggingChat (a name and a URL, no headers); `run` returns an id at once and a status tool reads
it; the tool set scopes along [`../decisions.md`](../decisions.md) § 0005's capabilities.

## What PromptPotter emits

**We write a file and provide a reader; we never load, host, route or hot-swap**
([`../developer/stable-api.md`](../developer/stable-api.md) § 5c). Owed: `to_dspy` on the `[dspy]` extra;
Langfuse write-back to Prompt Management; a DSPy listing PR; MLflow as a second viewer (below).

### MLflow interop

MLflow is the operator's server, never our dependency beyond the client; nothing here hosts, routes or
swaps. batch/22 deleted the mirror with `infrastructure/tracing/`: the ledger is the trace
([`../operations/observability.md`](../operations/observability.md)), so each step below builds on a
sink's return as a ledger projection. Verify against a real `mlflow server`, never the offline run. In order:
(1) a campaign opts in, one config field off by default at all five entry points; (2) the winning model
in `register_prompt`'s `model_config`; (3) a campaign that starts from a registry prompt, fetched once at
mint and pinned in the seed (`CycleSeed.origin_prompts`, a new `OriginSource` member), whose winner
registers as version N+1 of that prompt, `origin_override` one typed field on `MintCampaignPayload` and
`EditDraftPatch` (open: whether a restored origin carries its node's `shots`); (4) a `promptpotter.delivery` tag saying how the connector delivers the text; (5)
cells as traces and grades as feedback, folded off the records the bench writes; (6) PromptPotter as an
`mlflow.genai.optimize_prompts` optimizer, an extra beside `presentation/teleprompter.py`; (7) MLflow's
meta-prompt optimizer on the bench, low priority; (8) a push/pull store only for a host that cannot mount.

## Judged and turn-structured scoring

What is live is [`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md)'s.
Open: per-step difficulty under a testlet model, each step's term banked as its own `pipeline_data` key;
a declared step as a NODE, so `campaign_config.exclude_nodes` selects steps.

## Evolving agent harnesses

The artifact becomes a skilled agent's harness through `connectors/harbor.py`, each skill measured
separately. Owed: an L1 rule rejecting an over-long skill body; the no-skill arm beside the search;
skill routing before the library grows.

## Small open items

- **Schema axes** on `justlogic/d234` via `evidence --grid`, promoted at `proxy_lift_corr ≥ 0.6`.
- **Exit gate**: `rounds_to_separable ≤ 5` on `llm_only` AND TermNorm under one `l1_generate_hash`.
- **Run admission**: look-ahead's ceiling is a constant per connector, not measured headroom.
- **Cheap top-up**: a second `PanelTrigger`, blocked on pricing a missing row.
- **Delegation, open** ([`../decisions.md`](../decisions.md) § 0005): who edits grants; whether a delegate
  is its own tenant.
- **Lineage mask (C8)**, beyond [`../operations/mask-projection.md`](../operations/mask-projection.md): a
  mask that changes which samples a round buys needs a replayable measurement order seeded into the
  fork; until then a sample-subset mask previews and stops.
- **First-run exhibit (A1):** ten films you like in, better recommendations and the prompt out: no
  private data, a right answer (held-out titles, `list_rr`), our own rows (MovieLens cannot ship), served
  from an install-tier campaign behind a `demo_mode_enabled` reader.
- **Prompt-field order (C10):** today a node's declaration. Proposal: the check-in carries an order per
  prompt node (`DraftFile.origin_prompts`), a fork inherits it, a reorder re-keys every cell. **Open
  (operator):** an optimizer axis or an operator setting.
- **Arrangeable workspace (A4):** panels resize, collapse, pop out and dock back; on a phone they stack.
  Owed: one view, one panel (`components/dashboard/layout/panes.tsx`); the arrangement as data on the
  address (`lib/workspace.tsx`); one panel-chrome primitive; the tapped candidate's before/after.
  **Closes when** an arrangement survives a reload on a phone and a desktop.
- **Run card:** the chat's sample walk alone. Owed: the round that changed each key ("changed in C7.3").
