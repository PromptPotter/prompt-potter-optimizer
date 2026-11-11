# PromptPotter Architecture

§0 is a contract: code that disagrees with it is fixed, and a request that fits no §0 bucket is a
flag. When §0 changes, the layers' `CLAUDE.md` files change in the same PR.

---

## §0 — PromptPotter on one page

### Purpose

Evolve a target prompt + pipeline params toward a fitness goal by running an optimizer — ours or
a peer's — against a scoring dataset, under one harness that measures every optimizer the same way.

#### Pipeline-agnostic

A backend is optimizable by publishing a `pipeline.yaml` of its tunables, so a new backend is a new
file and no PromptPotter code ([`developer/node-standard.md`](developer/node-standard.md)).

#### Two-layer searchpoints + self-optimization

`JobSearchPoint` is the frozen target spec being measured; `OptSearchPoint` is the **individual**
every optimizer proposes, selects and derives from. Lineage is a DAG; a node appends a variation and
rewrites none. `task_context` is the campaign's, on no individual. **An optimizer's working state is
not in the individual**: it rides the round as `optimizer_state`. Every optimizer runs on a
`pipeline.yaml` of a backend's shape, so accumulated individuals are the dataset for **optimizing
the optimizer** (L4).

#### The bench runs optimizers; it is not one

It runs the loop, measures, archives, meters spend, persists and serves the control plane; **every
optimizer it runs is a manifest**, ours (`potter`) beside CAPO, LEVI or GEPA. The bench decides
nothing an optimizer owns and never imports one.

### Bench and optimizer

#### Every optimizer is a manifest

A campaign names one optimizer and adjusts it with a `nodes.{name}.config` overlay. **An optimizer's
knobs are its nodes' `config`** — declared, visible, hashed into identity; the campaign's config
holds only what the bench owns. Implementations register in the one entry-point registry, so a third
party ships an optimizer with no edit here. **The bench walks `default` once per round, and
`initial_population` in a round entered with no population; every other pipeline is the
optimizer's own.**

#### A round is a neutral envelope

PROPOSE, MEASURE, SELECT, ADAPT (`domain/results.py::RoundOutcome`). **The headline is derived,
never an input**, and **no envelope field names a layer, a critique or an elimination rule** — those
are one optimizer's state, landing on the ledger stamped with it.

#### The bench score is not an optimizer's selection

**No optimizer node sees the bench set, and the bench grades the pick the optimizer DECLARED**
with one evaluator — never one made by comparing rounds read on different rows:
[`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) § The bench headline.

#### Three identities — what was measured, what ran, what graded it

None folds into another.

- **Measurement** — the node chain's config key plus the row's sample key. No optimizer or dataset
  name enters it, so an arm two optimizers propose replays free.
- **Treatment** — which optimizer ran; a resume diverges on the call digests each round stamps,
  never on a config edit.
- **Bench set** — what graded the headline. Two headlines are one quantity only where it agrees; a
  head-to-head reads every arm under ONE scorer.

#### The controlled comparison — one declaration every mechanism reads

A campaign minted as an **arm** of a declared head-to-head (`RunDeclaration.purpose` is `Compare`)
is **controlled**; every mechanism reading past the declaration asks that predicate alone
([`decisions.md`](decisions.md) § 0009). The head-to-head pins one bench set and one budget; under
it search is the prompt's own fields, memory is the arm's own line on a frozen ruler (the cache
still replays), an act on an arm takes it out of the comparison, and one served guard labels each
pair and refuses none.

### Central loop

One round is the manifest's `default` pipeline, scored through the **sole scoring gateway**
(`application/scoring/search_point_scorer.py`): [`concepts/the-loop.md`](concepts/the-loop.md).

#### Four single-place-to-extend mechanisms

**Scoring** through the scoring gateway, **persistence** through `CycleEventLog.append`,
**prompt-fill** through `injection_table()`, **an optimizer node's implementation** through the one
entry-point registry.

#### Origin, parent, and check-in

**Origin is C0** and the **parent at offset 0**; never "baseline":
[`concepts/the-loop.md`](concepts/the-loop.md) § Origin and check-in.

### Escalation — potter's controller

Potter's, not the bench's: [`concepts/the-loop.md`](concepts/the-loop.md) § Escalation.

### Errors heal upward, tolerantly

One failed measurement is innocent; repeats on one candidate are its fault; failure on every arm is an
outage that halts (`StopReason.BACKEND_UNREACHABLE`). `application/scoring/classification.py::DegradationCheck`
stops any optimizer's arm **`broken`** past `optimization.degradation_threshold` or on one fatal-classified
sighting (`degradation_fatal_fastpath`); only the eliminator says **`eliminated`** (`domain/arms.py::Cut.kind`).
**No retry of a (sample, candidate) pair**: a transport resend and the origin's one re-score after a transient
abort are not second verdicts. An L1 validation failure reaches L1's own next call, not L2.

### Dispatch hub

**New prompt information is one new injection renderer.** **Everything that reaches a model is
bounded where it is PRODUCED**; a composition site selects whole items, never cuts one —
[`developer/dispatch-hub.md`](developer/dispatch-hub.md) § Every item that reaches an LLM carries an upper limit.

### Four entities (outermost → innermost)

- **Workspace** — the tenant's datastore at `projects/{tenant}/`.
- **Dataset** — **its identity is its content, its name a label**.
- **Campaign** — one declared effort (dataset, origin, optimizer manifest) and its cycle tree; its
  declaration is the `RunDeclaration`, written by every door through one `declare`.
- **Cycle** — one node of that tree (root | fork | diag). A check-in is a draft, never a cycle. On
  disk: [`operations/persistence-and-state.md`](operations/persistence-and-state.md).

#### Two resolution seams

The **dataset-file** seam (`resolve_dataset`) answers *which bytes on disk*; the
**effective-config** seam (`application/pipeline_resolve.py`) answers *which values a campaign
runs*. **The effective-config seam consumes the dataset-file seam, never the reverse**, so
`nodes.*.config` is the seed a campaign starts from, never the answer for a running one.

#### A campaign has one root cycle — there is no Session tier

**A fork never switches the optimizer manifest**: a head-to-head is two campaigns. Measurement
scopes to dataset or workspace; configuration resolves per campaign —
[`../promptpotter/infrastructure/store/CLAUDE.md`](../promptpotter/infrastructure/store/CLAUDE.md)
§ The archive is not scoped by campaign.

### State + persistence — the five I/O kinds

Five I/O kinds, each with its own ingress. **Adding one amends §0 first.**

#### Persistence

Sole writer: the per-cycle `CycleEventLog.append` (a cycle-less command writes the workspace
ledger, same discipline) — [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md)
§ Persistence — one ingress, two projections.

#### Display

Read-only projections fed by their ledger. **Run state is derived, never a freshness guess.**
**Pause is the single operator interrupt** and leaves a cycle resumable; "done" is a human mark.

#### Control-local

Ctrl+C and a typed gate decision at the producing process — an INPUT, never a transport: each
dispatches the command every other door sends.

#### Control-remote

Every command, from HTTP or a terminal verb, is a `CommandRecord` on the account's command log,
filed by the sole `CommandDispatcher` ([`decisions.md`](decisions.md) § 0001). **A served GET is
not a sixth kind**: it mutates nothing and resolves in `application/`.

#### Identity

OIDC verification at the API trust boundary; tokens never pass it, and privileged identity or
deployment mutations ride an outbound operator-admin channel, never a Control-remote command —
[`decisions.md`](decisions.md) § 0002 and § 0004.

#### App visits — Persistence the app writes about its own perimeter

Not a sixth kind: who reached the app from outside without a session lands in-process, outside every
tenant, reaching no ledger and nothing the loop reads —
[`operations/access-model.md`](operations/access-model.md) § The perimeter. The website's records are
the site's own service, not the engine's.

### Everything material lives on disk, in human-readable form

The project file tree IS the operator's interface; nothing material is stdout-only or in-memory.
Read surfaces split by cadence (live, settled, internal `.runtime/`): repoint a consumer reading the
wrong cadence, never retire the other surface —
[`operations/persistence-and-state.md`](operations/persistence-and-state.md) § Layout.

### Tracing

The ledger is the trace, so every optimizer is traced and no member opens a span. **Fan-out only —
the optimizer never reads it.** No external sink ships; one returns as a projection on the ledger's roster.

### Measurement archive (the actual database)

**The archive is the database; the ledger is the event log over it — neither replaces the other.**
Rows are facts, never grades, keyed by no optimizer, position or dataset name:
[`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) § The archive.

### Layering and entry points

Hexagonal, failing loud at import ([`../promptpotter/application/CLAUDE.md`](../promptpotter/application/CLAUDE.md)
§ Layer rule); the entry points share one orchestration layer. No concept-first re-hierarchy into
vertical packages: a multi-directory fix is the footprint of changing the state spine. The core
install is the engine, every surface an extra ([`decisions.md`](decisions.md) § 0006). Every entry
point that starts a loop admits through one seam; a full box queues
([`operations/access-model.md`](operations/access-model.md) § What bounds resource use).

**The `###` headings above, Purpose aside, are the buckets** — a PR maps onto one by name.
Anything that fits none is drift (delete) or a missing bucket (amend §0 first, then the code).

---

## §0.5 — Load-bearing surface

Surface whose work is invisible to a sweep that counts readers. A PR touching one states its case.

- **PoBB** — potter's `eliminator`; **`DegradationCheck`** — the bench's per-candidate failure
  halt, for every optimizer.
- **Connectors** (`promptpotter/connectors/`) — the only place backend identity is named.
- **`infrastructure/producer_lock.py` and `jobs/interlock.py`** — the OS locks are the only box
  facts that outlive a process; a heartbeat has a window where the answer is wrong.
- **The roster's `source_digest`** — what an optimizer node is handed is L4 measurement identity
  ([`optimizers/potter/CLAUDE.md`](../promptpotter/application/optimizers/potter/CLAUDE.md)).
- **The core/extra split** — moving a package into core charges every embedder.
- **Resume and fork-on-divergence** (`ResumeCheckpointRecord`, `CheckpointKind`).
- **No campaign fact on a side file** — a campaign's result is a fold of its line's ledgers. <!-- probe: git grep -cE "result\.json" -- promptpotter/infrastructure/store/layout.py == 0 -->
- **The hard-sample sorter** and the round order it feeds
  ([`methods/verdict-resolution.md`](methods/verdict-resolution.md) § The round order).
- **`RoundResult.panel` means "proposed in THIS round"** — a retained individual read off every
  cell would be counted k+1 times.
- **One tenant δ ruler, one clock** — every θ is read on it, less the parent line's rows.
- **The discoverability injections**, **the `new` verb + `/potter-run` onboarding**, and
  **check-in's one `task_context` decomposition**.
- **Per-dataset configs are the source of truth** — a default goes in the dataset's file, never code.
- **`Evaluator` and its registry** — the only sanctioned "eval" vocabulary.

**Fitness is never one number — ask "under which formula?"** Formula-relative (the **active**
formula; a **mask** picked by a **lens**; a **replay**) and mode-relative (`measured` vs `all`);
the model is [`methods/verdict-resolution.md`](methods/verdict-resolution.md)'s, masks
[`operations/mask-projection.md`](operations/mask-projection.md)'s.

- **One writer, one resolver** — an arm that read no cell has `None`; `order_floor` alone turns
  absence into a number inside an ordering; `scoreboard_rank_key` **is not a selection** — the
  `selector` node is.
- **Every score is served, never recomputed in the consumer**, and a `score:F` lens grades the
  way a fresh run under `F` would.
- **A cycle's "best" has two bases** — the optimizer's declared pick and the bench headline — and
  which pick that is, against the origin, is ONE `RunStanding`.

**A budget is never one value — ask "at which position?"** One `Budget` stands at four: asked,
admitted, standing, spent (a fold of spend facts, never a record) — [`decisions.md`](decisions.md) § 0003.

- **Only the run's spend book halts a run**; an unreported call binds at the most it may cost,
  never counted spent.
- **An L4 inner cycle spends under its root's book** and charges its owner's account.
