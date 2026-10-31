# PromptPotter Architecture

The page every PR measures against. §0 is a contract: code that disagrees with it is fixed, and
a request that fits no §0 bucket is a flag — propose a shape that fits, or push back. Detail lives
in each layer's `CLAUDE.md`; when §0 changes, those files change in the same PR.

---

## §0 — PromptPotter on one page

### Purpose

Evolve a target prompt + pipeline params toward a fitness goal by running an optimizer — ours or
a peer's — against a scoring dataset, under one harness that measures every optimizer the same
way. Three commitments shape every bucket below.

#### Pipeline-agnostic

Any backend publishing a `pipeline.yaml` of its tunables is optimizable; node names, parameter
shapes and prompt slots all come from that self-description, so a new backend is a new file and
no PromptPotter code. The contract: [`developer/node-standard.md`](developer/node-standard.md).

#### Two-layer searchpoints + self-optimization

`JobSearchPoint` is the frozen target spec being measured; `OptSearchPoint` is the **individual**
every optimizer proposes, selects and derives from — **a value per locus over the roster its
pipeline declares** (`domain/opt_search_point.py::Locus`). Lineage is a DAG (`parent_ids`: one
parent for a mutation, several for a crossover), and each node that wrote an individual appends a
variation and rewrites none. The operator's framing (`task_context`) is the campaign's, frozen for
the run, and rides no individual. **An optimizer's own working state is not in the individual**:
it rides the round as `optimizer_state`, typed parts each registered by its owner
(`domain/optimizer_state.py::StatePart`), and a roster leaving a part open is refused at load.
Every optimizer runs on a `pipeline.yaml` of the same shape as a backend's, so accumulated
individuals are the dataset for **optimizing the optimizer** (L4).

#### The bench runs optimizers; it is not one

**PromptPotter is a bench** — the harness that runs the loop, measures, archives, meters spend,
persists and serves the control plane — **and every optimizer it runs is a manifest**, ours
included: `potter` (L1/L2/L3), and a peer such as CAPO, LEVI or GEPA beside it. The bench decides
nothing an optimizer owns and never imports one.

### Bench and optimizer

#### Every optimizer is a manifest

A campaign names one optimizer (`optimizer: <name>`, a manifest under `assets/optimizers/`) and
adjusts it with a `nodes.{name}.config` overlay. **An optimizer's knobs are its nodes' `config`** —
declared, visible and hashed into identity; the campaign's own config holds only what the bench
owns. Node types and their I/O are [`developer/node-standard.md`](developer/node-standard.md)
§ Optimizer node types'. Implementations register under their node name through the one
entry-point registry connectors and judges use, so a third party ships an optimizer with no edit
in this tree, and a manifest is typed on read (`application/optimizer_manifest.py::PipelineManifest`)
so a roster the bench cannot run is refused before a mint. **The bench walks `default` once per
round, and `initial_population` in a round entered with no population; every other pipeline is
the optimizer's own, and the bench never chooses.** The individuals a run carries are one
`Cycle.population`, every optimizer's.

#### A round is a neutral envelope

PROPOSE, MEASURE, SELECT, ADAPT; its document is `domain/results.py::RoundOutcome` — arms by
`ArmKey`, every row as its archive address, the `Election`, and the `optimizer_state` it ended
on. **The headline is derived, never an input**, and **no envelope field names a layer, a critique
or an elimination rule** — those are one optimizer's state. A node's decision lands on the
ledger stamped with the node that took it.

#### The bench score is not an optimizer's selection

**No optimizer node sees the bench set, and the bench grades the pick the optimizer DECLARED**
with one evaluator — never one it makes by comparing rounds read on different rows. Splits,
columns, the campaign-owned passes and `bench_trigger`:
[`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) § The bench headline.

#### Three identities — what was measured, what ran, what graded it

Three values, three functions; none folds into another.

- **Measurement** — `infrastructure/store/measurement_archive.py::config_key` over the node chain,
  plus the row's `sample_key`. No optimizer and no dataset name enter it, so an arm two
  optimizers propose replays free and a row is the same row under any label.
- **Treatment** — which optimizer ran: `domain/treatment.py::Treatment`, minted onto
  `campaign.json`; a resume diverges on the call digests each round stamps, never on a config edit.
- **Bench set** — what graded the headline: `domain/campaign.py::bench_set_of`. Two headlines are
  one quantity only where it agrees, and a head-to-head reads every arm under ONE scorer.

#### The controlled comparison — one declaration every mechanism reads

An ordinary campaign uses everything that helps; one minted as an **arm** of a declared
head-to-head (`RunDeclaration.purpose` is `Compare`) is **controlled**, and every mechanism that
reads past the declaration asks that predicate alone — owned by [`decisions.md`](decisions.md)
§ 0009. The head-to-head (`domain/campaign.py::HeadToHeadRecord`) pins one `SharedPart`, so every
arm is graded on one bench set under one budget. What controlled switches, one site each: the
search is the prompt's own fields (`application/jobs/mint.py::_prompt_axes_only`); memory is the
arm's own line (`infrastructure/store/archive_queries.py::ArmOf`) and the frozen ruler
(`promptpotter/application/bench/difficulty.py::judging_ruler`), while the cache still replays; the bench pass
is the launch's own (`application/jobs/mint.py::declare`); every act on an arm is admitted and takes
its campaign out of the comparison as its event (`domain/run_records.py::LeftComparisonRecord`); the
ceiling meters search beside bench (`domain/spend.py::CeilingMeter`). Each pair is labelled by one
served guard (`application/evidence/head_to_head.py::pair_guard`), which refuses no pair.

### Central loop

One round is the manifest's `default` pipeline; potter's is generate → score → critique, scored
through the **sole scoring gateway** (`application/scoring/search_point_scorer.py`). Potter's
round, its two efficiency mechanisms and its stop: [`concepts/the-loop.md`](concepts/the-loop.md).

#### Four single-place-to-extend mechanisms

**Scoring** through the scoring gateway, **persistence** through `CycleEventLog.append`,
**prompt-fill** through the `injection_table()` registry, **an optimizer node's implementation**
through the one entry-point registry.

#### Origin, parent, and check-in

**Origin is C0** — an `OptSearchPoint` like every candidate, or the point a fork branches from —
and the **parent at offset 0**; everywhere else say parent, never "baseline". Check-in is the
bench's resolver + two gates (readiness, then round-0 scoreability) that complete it:
[`concepts/the-loop.md`](concepts/the-loop.md) § Origin and check-in.

### Escalation — potter's controller

Potter's, not the bench's: lazy (L2 on an L1 stall, L3 on an L2 stall), higher layers constrain
lower ones, no layer rewrites `task_context`, and deterministic rules route, never stop —
[`concepts/the-loop.md`](concepts/the-loop.md) § Escalation.

### Errors heal upward, tolerantly

One failed measurement is innocent; repeats on one candidate are its fault (the bench's
`DegradationCheck`, `broken` apart from `eliminated`); failure on every arm is an outage that
halts. No retry of a (sample, candidate) pair —
[`developer/self-healing-internals.md`](developer/self-healing-internals.md) § Errors heal upward.

### Dispatch hub

Every optimizer `llm` node fills its prompt by one path (`DispatchHub.fill`) from one registry
(`application/optimizers/potter/dispatch/injections/registry.py::injection_table`): **a new piece
of prompt information is one new injection renderer**, no sidecar. **Everything that reaches a
model is bounded where it is PRODUCED**, and a composition site may select whole items, never cut
one — [`developer/dispatch-hub.md`](developer/dispatch-hub.md) § Every item that reaches an LLM
carries an upper limit.

### Four entities (outermost → innermost)

- **Workspace** — the tenant's queryable datastore at `projects/{tenant}/`: every bank, every
  campaign, the content-addressed caches (`config/paths.py::SHARED_CACHE_DIRS`).
- **Dataset** — **its identity is its content, its name a label** (`shared/hashing.py::dataset_hash`);
  tenant definitions and read-only install templates share one read path and one shape.
- **Campaign** — a first-class entity: one declared effort (dataset, origin, optimizer manifest)
  and its cycle tree, minted fresh per mint; its declaration is the `RunDeclaration` on
  `campaign.json`, written through `application/jobs/mint.py::declare` by every door.
- **Cycle** — one node of that tree (root | fork | diag), id'd by target content hash. A check-in
  is a draft, never a cycle. On-disk shape: [`operations/persistence-and-state.md`](operations/persistence-and-state.md).

#### Two resolution seams

The **dataset-file** seam (`resolve_dataset`) answers *which bytes on disk*; the
**effective-config** seam (`application/pipeline_resolve.py`) answers *which values a campaign
runs*. **The effective-config seam consumes the dataset-file seam, never the reverse**, so
`nodes.*.config` is the seed a campaign starts from, never the answer for a running one.

#### A campaign has one root cycle — there is no Session tier

`Session` is in-process wiring, `active_session.json` a pointer, `mint: "root"` a label — none a
tier. **A fork never switches the optimizer manifest**: a head-to-head between optimizers is two
campaigns. Measurement queries scope to dataset or workspace (`domain/cells.py::HeatmapScope`);
configuration resolves per campaign — [`../promptpotter/infrastructure/store/CLAUDE.md`](../promptpotter/infrastructure/store/CLAUDE.md)
§ The archive is not scoped by campaign.

### State + persistence — the five I/O kinds

Five I/O kinds, each with its own ingress. **The five are fixed: adding one amends §0 first.**

#### Persistence

Sole writer: the per-cycle `CycleEventLog.append`; a cycle-less command writes the workspace
ledger beside it, same discipline; a fork is `inherit_from(parent, offset)`. Searchpoints are
immutable, so their hash is a trustworthy identity. Owned by
[`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md)
§ Persistence — one ingress, two projections.

#### Display

Ledger subscribers, read-only, each writing only the artifact it declares; every entry point binds
the same set. **Run state is derived (`derive_run_state`), never a freshness guess.** **Pause is
the single operator interrupt** and leaves a cycle resumable; "done" is a human mark.

#### Control-local

Ctrl+C and a typed gate decision at the producing process — an INPUT, never a transport: each
dispatches the same command every other door sends. There is no flag file.

#### Control-remote

Every command, from HTTP or a terminal verb, is a `CommandRecord` on the cycle's ledger written by
the sole `CommandDispatcher`, naming who sent it and acked by who took it — owned by
[`decisions.md`](decisions.md) § 0001. **A served GET is not a sixth kind**: it mutates nothing,
but declares its response model first and resolves in `application/` —
[`developer/adding-a-surface.md`](developer/adding-a-surface.md) § A served read.

#### Identity

OIDC verification at the API trust boundary; tokens never pass it, and privileged identity or
deployment mutations ride an outbound operator-admin channel, never a Control-remote command —
[`decisions.md`](decisions.md) § 0002 and § 0004.

#### Site records — Persistence with an ingress of its own

Not a sixth kind: `/site-records/{table}` opens to one bearer token and appends outside every
tenant, reaching no ledger and nothing the loop reads (`application/site_records.py`) —
[`operations/access-model.md`](operations/access-model.md) § The perimeter.

### Everything material lives on disk, in human-readable form

The project file tree IS the operator's interface, so an assistant reads state from it; nothing
material is stdout-only or in-memory. It is a read-out: input flows through the Control kinds only.
Read surfaces split by cadence — live `dashboard.json`, settled `index.json` / round files — over
the internal `.runtime/` (ledger, projection caches, OS lock files): repoint a consumer reading the
wrong cadence, never retire the other surface. Layout:
[`operations/persistence-and-state.md`](operations/persistence-and-state.md) § Layout.

### Tracing, Langfuse-shaped, lightweight by default

A trace is a fold of the ledger (`infrastructure/tracing/bridge.py::TracingProjection`), so every
optimizer is traced and no member opens a span. **Fan-out only — the optimizer never reads it.**
Sinks are off by default: [`../promptpotter/infrastructure/tracing/CLAUDE.md`](../promptpotter/infrastructure/tracing/CLAUDE.md).

### Measurement archive (the actual database)

**The archive is the project's database; the ledger is the event log over it — neither replaces
the other.** Rows are facts, never grades, and no key names an optimizer, a position or a dataset
name: [`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) § The archive.

### Layering and entry points

#### Hexagonal layering

A structural invariant that fails loud at import — [`../promptpotter/application/CLAUDE.md`](../promptpotter/application/CLAUDE.md)
§ Layer rule. The entry points share one orchestration layer and one set of data types.

#### One rejected re-cut

No concept-first re-hierarchy into per-concept vertical packages: a multi-directory fix is the
footprint of changing the central state spine, not a defect.

#### The loop is embeddable

The core install is the engine; every surface above it is an extra — [`decisions.md`](decisions.md) § 0006.

#### Run admission — one seam in, one queue

Every entry point that starts a loop admits through `jobs/launcher/admission.py`; a full box
queues `Job` records, which are not a run phase. Bounds and drain order:
[`operations/access-model.md`](operations/access-model.md) § What bounds resource use.

**The `###` headings above, Purpose aside, are the buckets** — a PR maps onto one by name.
Anything that fits none is drift (delete) or a missing bucket (amend §0 first, then the code).

---

## §0.5 — Load-bearing surface

Surface whose work is invisible to a sweep that counts readers, beside every invariant §0 states.
A PR touching one states its case.

- **PoBB** — potter's `eliminator` and the reference member of its node type; **`DegradationCheck`**
  — the bench's per-candidate failure halt, for every optimizer.
- **Connectors** (`promptpotter/connectors/`) — the only place backend identity is named.
- **`infrastructure/producer_lock.py` and `jobs/interlock.py`** — the OS locks are the only box
  facts that outlive a process; a heartbeat is the version with a window where the answer is wrong.
- **Langfuse-shape events and `TracingProjection`** — the Tracing bucket's foundation.
- **The roster's `source_digest`** (`optimizers/nodes.py`) — what an optimizer node is handed is L4 measurement identity,
  so its renderers, bundling and composition are hashed (AST-normalized).
- **The core/extra split** — moving a package into core charges every embedder; re-measure
  reachability ([`decisions.md`](decisions.md) § 0006).
- **Resume and fork-on-divergence** (`ResumeCheckpointRecord`, `CheckpointKind`).
- **No campaign fact on a side file** — a campaign's result is a fold of its line's ledgers. <!-- probe: git grep -cE "result\.json" -- promptpotter/infrastructure/store/layout.py == 0 -->
- **The hard-sample sorter** (`application/intelligence/hard_sample_sorter.py`) and the round
  order it feeds ([`methods/verdict-resolution.md`](methods/verdict-resolution.md) § The round order).
- **`RoundResult.panel` means "proposed in THIS round"** — `application/intelligence/rasch.py::build_observations`
  flattens it undeduplicated, so a retained individual read off every cell is counted k+1 times.
- **One tenant δ ruler, one clock** (`infrastructure/store/ruler_store.py::RulerStore`) — every θ is
  read on it, less the parent line's rows (`promptpotter/application/bench/difficulty.py::judging_ruler`); every
  reading carries its `RulerStamp`.
- **The discoverability injections** (`application/optimizers/potter/dispatch/injections/catalogues.py`).
- **The `new` verb + `/potter-run` onboarding**, and **check-in's one `task_context` decomposition**.
- **Per-dataset configs are the source of truth** — a default goes in the dataset's file, never
  PromptPotter code.
- **`Evaluator` and its registry** — the only sanctioned "eval" vocabulary.

**Fitness is never one number — ask "under which formula?"** Formula-relative (the **active**
formula; a **mask** picked by a **lens**; a **replay**) and mode-relative (`measured` vs `all`);
the model is [`methods/verdict-resolution.md`](methods/verdict-resolution.md)'s, masks
[`operations/mask-projection.md`](operations/mask-projection.md)'s, the per-cell composite θ is fit on
[`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) § Composite — recorded, not gating's,
and the bench headline § The bench score is not an optimizer's selection's.

- **One writer, one resolver** — `compute_composite_fitness` writes it; an arm that read no cell
  has `None`; `order_floor` alone turns absence into a number inside an ordering;
  `scoreboard_rank_key` **is not a selection** — the `selector` node is.
- **Every score is served, never recomputed in the consumer**, and a `score:F` lens grades the
  way a fresh run under `F` would (`application/mask/load.py`).
- **A cycle's "best" has two bases** — the optimizer's declared pick and the bench headline — and
  which pick that is, against the origin, is ONE `RunStanding` banked on `RoundClosedRecord`.

**A budget is never one value — ask "at which position?"** One `Budget` (`domain/spend.py`) stands
at four: asked, admitted, standing (`set-limits`), spent (a fold of spend facts, never a record) —
admission and the spend book are [`decisions.md`](decisions.md) § 0003's.

- **Only the run's spend book halts a run**; an unreported call binds at the most it may cost,
  never counted spent.
- **An L4 inner cycle spends under its root's book**, mirrored onto the outer ledger, and charges
  its owner's account (`infrastructure/store/stores.py::build_stores`, `charges`).
