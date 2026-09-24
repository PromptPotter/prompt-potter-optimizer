# connectors/ — backend-specific hook bundles

Each connector packages everything PromptPotter needs to talk to one
backend kind. A connector is one file under this package exporting a
``Connector(...)`` binding (`protocol.py`) as `CONNECTOR`. Operating one is local too: model/provider switches go in
`datasets/{name}/pipeline.yaml::nodes.{name}.config`, picked from the menu
`Connector.available_models` seeds into that file — never in the backend's repo.

**Adding one is local to `connectors/<name>.py` + a dataset directory, and the way that
claim fails is what to watch for:** core INFERRING something a connector should DECLARE. The
rule is not a file list; it is that **whatever the next connector has to reach into core to fix, the fix is a
declaration on `Connector` or a fact derived from what `extract_experiment` already returns —
never a branch at the site where the symptom showed up.**

**Connector authoring is a product surface.** If adding one is hard, integrators ask the
operator instead of doing it themselves. The bar on any defect found while adding one is not
"this connector works" but *whatever it tripped over, connector #3 does not.*

## Registered connectors

| Name | File | Wire shape | Session | Use |
|---|---|---|---|---|
| `termnorm` | `termnorm.py` | `{query, steps, node_config}` posted to `/matches` | `POST /sessions` handshake with terms array | TermNorm production backend |
| `promptpotter` | `promptpotter.py` | `{query, optimizer_prompt_overrides}` → in-process inner cycle (`in_process_run` → `runner/inner/spawn.py`) | Noop (no remote service) | Optimizer-of-the-optimizer (L4) |
| `dspy` | `dspy_module.py` | `{query, prompt, params}` → the caller's `dspy.Module` | Noop (no remote service) | PromptPotter as a DSPy `Teleprompter` (`presentation/teleprompter.py`) |
| `harbor` | `harbor.py` | `{query, prompt, model_name, agent_kwargs}` → one containerized Harbor trial (`in_process_run` → `Trial.create(...).run()`) | Noop (no remote service) | Tuning an agent that works in a sandbox, graded by the task's own verifier |

> **`import dspy` is function-local, and must stay that way.** Building the table imports every
> built-in, so a module-level import would break every run for every install that did not ask
> for the `[dspy]` extra — which is all of them by default. The
> caller-facing half (`presentation/teleprompter.py`) imports it at module level instead, because
> its only importer is the caller and a missing extra should stop them there, by name.

> **`llm_only` is a NODE name, never a connector.** Every single-node benchmark
> declares an `llm_only` node inside a `termnorm` pipeline and routes over HTTP to the
> server like any other. Do not add an `llm_only` connector: its answer extraction would
> duplicate what TermNorm's `_step_llm_only` already does over the wire.

## What the second connector taught the boundary

Three things the next connector should heed. **Wire payload shape is connector-specific** — each
decides its own outer key (`termnorm` flattens `pipeline_params` into `node_config`,
`promptpotter` nests under `optimizer_prompt_overrides`) and the protocol just carries the
dict through. **The session contract works for in-process backends via a noop**
(`PromptPotterSession` no-ops `set_terms`/`recover`), at the cost of the HTTP shape leaking
into the rest of `BackendClient`. And **`extract_experiment` is the impedance-match seam**:
both connectors yield `(queries, index_terms)` from very different bodies, so **a new
connector shapes its `experiment_data` to fit the loader, never the reverse**.

## TermNorm is not a third party

**A structural bug whose cause sits in TermNorm's code gets fixed in TermNorm — never
papered over on this side.** It lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel`
(backend under `backend-api/`), the same project as PromptPotter, split into a separate
repo for security reasons only; folding it back in is the goal. That makes it the
exception to "backends are read-only" — and to nothing else: per-dataset config still
rides the overlay, backend *behaviour* still earns a TermNorm root-fix, and which one
you have is decided by which side actually holds the cause. **Cross-repo edits are
authorized:** edit the local repo directly (runfish5 authors it); if unavailable,
coordinate with **runfish5 on GitHub**. The PP↔TermNorm highway is a shape contract —
touch one side, fix both. Debugging →
[`../../docs/operations/backend-integration.md`](../../docs/operations/backend-integration.md)
§ Debugging the highway.

## Execution mode — declared, never name-branched

A connector declares **how its backend runs** via `Connector.execution`
(`ConnectorExecution`): `remote_http` (default — posts to a live `/matches`)
or `in_process` (runs in this process, no HTTP). `BackendClient.run_query`
**dispatches on this declared mode, never on the connector name** — so a new
backend's transport is a capability it declares, not a branch in the core loop.

**The `in_process` arm.** `run_query` calls the
connector-supplied `Connector.in_process_run(workload, query, payload) -> {"data": {…}}` —
the same shape the scorer parses from an HTTP `/matches` body. The registry guard
(`__init__.py`) enforces the pairing: an `in_process` connector MUST supply
`in_process_run`, a `remote_http` one MUST NOT. The mode is not a synonym for "cheap and
local" — a `harbor` cell holds a container, spends real money and takes minutes. **`in_process` is a statement about TRANSPORT — there is no HTTP — and about
nothing else.**

**Per-run state is the `workload` argument, never a ContextVar or a module cache.**
`InProcessWorkload` (`protocol.py`) is built once by `init_services` and held by the run's own
`BackendClient`: the `experiment_file` its samples came from, already through
`Connector.resolve_experiment`, and the `program` an embedded host passes to `open_session`. The
run's samples, its workload and its identity fingerprint all read that one resolved document.
Sibling campaigns are sibling tasks each building their own client, so neither sees the other's.
What an inner cell spawns UNDER is not the workload: it names a cycle init has not minted yet,
and moves every round.

- **`promptpotter`** — `in_process_run` is a thin delegate to
  `application/runner/inner/spawn.py::run_inner_cycle`, because running a whole inner campaign is
  heavy orchestration and belongs in `application/runner`. Five facts about the arrangement:
  - **Its own `asyncio.Task`.** The three per-task ContextVars — `_CYCLE_LEDGER` + `_CURRENT_ROUND`
    (`infrastructure/llm/telemetry.py`) and `_ABORT_CHECK` (`infrastructure/llm/rate_limit.py`) —
    isolate per task rather than per call, and the child gets a COPY, which is how `_ABORT_CHECK`
    carries the outer's pause into the inner run.
  - **Sandboxed stores in a FLAT per-cycle registry** `<workspace>/.inner/<key>/` — no
    active-pointer collision, and it holds no machine slot. Flat, never physically nested
    (`infrastructure/store/layout.py` says why), so the **re-entrant** invariant holds and L5+ nests.
  - **The spawning cycle publishes its context** via `publish_inner_spawn_context` at the runner
    seam, so the hook can find where to sandbox and which inner benchmark to run.
  - **Owner and asker are two facts, and a fork splits them.** `retarget_inner_spawn` moves only
    the *asker* (`spawned_by.outer_cycle_id`); the sandbox owner never follows a fork, because a
    repaired cell CONTINUING the campaign the parent banked is the whole point. One field meaning
    both files a fork's measurements under the cycle it superseded.
  - **The outer L1's prompt mutations reach the inner optimizer** through a per-run override
    ContextVar (`set_optimizer_prompt_overrides`), set inside the inner task. One process, no
    networking; a localhost-endpoint worker mode would be a new `execution` value with no core-loop
    edit.
- **`harbor`** — `in_process_run` builds a `TrialConfig` and awaits Harbor's own
  `Trial.create(...).run()`; the container, the verifier and the reward file are all theirs, so
  this connector shapes payloads and reads a number rather than orchestrating anything. What it
  decides, which the next episodic backend will face too: **the candidate prompt ships as an Agent
  Skill** (`AgentConfig.skills`) whose frontmatter `description` is FIXED and never a search axis
  (`harbor.py::_SKILL_NAME` says why). **`nodes.agent.config.skill_delivery: system_prompt` is the
  second channel** — the skill body at the head of terminus-2's prompt template — fixed per
  campaign, never listed in `param_keys`, absent means the Agent Skill; it enters identity as the
  node config it is. **The panel is the workload's `experiment`**, its published roster pinned by
  `resolve_experiment`, and `extract_experiment` publishes nothing. **Trial scratch goes to the
  system temp dir, never the workspace**, and nothing durable lives there — reward, digest and token
  counts belong in the measurement archive. **The one thing a cell leaves on the Docker host is its
  task image** (`hb__<content hash>`, one per distinct task environment, never one per cell); a hard
  kill's leftover containers and scratch are swept by the next run (`harbor.py::_reap_dead_producers`).
  **Never swept: the task images and the package cache**, which are what a resume is cheap on,
  **nor any container that does not name our compose overlay**, because this Docker host has other
  tenants. **A trial that measured the machine is never a cell**: `_infrastructure_failure` retries
  it and then raises `CellInfrastructureError`, which halts the walk — at once and as
  `CellSendRefusedError` when the provider account is out of credit. **Nor is one its model provider
  throttled**: `CellThrottledError` hands it to the run's backpressure (`BackendClient.run_query`),
  since the agent's own calls reach no client of ours. The rule, and the package cache that keeps
  downloads out of a cell, are
  [`../../docs/operations/package-cache.md`](../../docs/operations/package-cache.md).

## The answer shape — declared in `extract_experiment`, never inferred

**What one cell's ANSWER is, a connector declares by whether the queries it yields carry a
`ground_truth`.** Two shapes, and every core reader that needs to know asks the LABEL:

| shape | `extract_experiment` yields | who decides the score | the formula reads |
|---|---|---|---|
| **ranked-label** (`termnorm`, `dspy`) | `ground_truth: "<label>"` | a node emitting a ranking; `predicted` is compared to the label | `exact_match(predicted, ground_truth)` |
| **verifier-graded** (`harbor`, `promptpotter`) | `ground_truth: None` | something else, with a NUMBER — the task's own verifier, L4's outer proxies | a `required_observation_keys` entry: `max(0.0, min(1.0, env_reward))` |

`domain/scoring.py::is_verifier_graded` (one label) and `all_verifier_graded` (a round, a bank,
a dataset) are the ONE place that is asked. **Never `predicted == NO_RESULT`** — that sentinel is
set by `terminal_ranking` returning nothing, which a *dataset's* `node_role` decides, so two
labelless backends give opposite answers.

**There is deliberately no second declaration** — never a `Connector` flag beside it: an author
who writes `extract_experiment` correctly and forgets the flag gets back the misdiagnosis the flag
would exist to prevent.

**`Connector.answer_key` is not that flag, and the difference is the whole point.** The shape above
answers *is this cell graded against a label* — one fact, one home, on what `extract_experiment`
yields. `answer_key` answers *where the answer TEXT lives*, a different question the table never
asked: a verifier-graded cell still ANSWERED something. Without it, `predicted` falls back to the
terminal ranking, so a backend emitting none hands every text reader (a judge rubric) the literal
`NO_RESULT`. The two declarations cannot disagree, because neither can
answer the other's question. Declaring it is also where an answer goes instead of a ranking.

Four things that follow:

- **Do not invent a ranking to look ranked-label shaped** — every ranking reader then has to
  un-believe it.
- **Do not reach the same place by declaring the node a `RANKER`.** That switches on
  `candidate_recall`, which walks a ranking for a ground truth the backend does not have and
  banks the resulting `0.0` into `rounds/round_NNNN.json` and `index.jsonl::scores`.
- **A label-comparing formula is refused at compile** (`formula/compiler.py`), because
  `exact_match` strips both sides and scores an empty answer against an empty label as a PERFECT
  `1.0` — and that is the launcher's own default formula shape.
- **Emit absence, not zero.** Rank buckets, top-k and recall are all comparisons against a label;
  with none, every one reads `not_found` / `0.0` and reports a round that solved eight of ten as
  having solved none.

## A multi-turn cell — the turns are the backend's, the steps are the task's

A backend whose cell is a CONVERSATION emits `pipeline_data::turns`
(`domain/scoring.py::TurnRecord`), and everything about that channel is settled there. Three rules
belong here, because they are what a connector author gets wrong:

- **Project a published turn format; never author one.** `TurnRecord` narrows Harbor's ATIF
  (`harbor/models/trajectories/step.py`), dropping the training surface (token ids, logprobs,
  per-turn metrics) that no prompt, ruler or formula reads. Parse it as plain JSON:
  the file is upstream's PRIVATE trial layout, so a field they add must degrade the record, not
  raise inside a cell already paid for.
- **A turn carries the STEP it served, and never becomes one.** A step is a NAMED segment the task
  declares (Harbor's `[[steps]]`, whose name we author to match the schema term, so there is no
  third word for it); the turn's ordinal is not an axis, because an episode takes however many
  turns it takes. Per-step rewards ride the row as TERMS beside the cell's aggregate — Harbor's own
  `multi_step_reward_strategy` folds them into one number and that fold is the cell's score.
  Treating either as an item claims kN observations where there are N.
- **Absent is not empty.** No `turns` key means "this backend has no turn concept"; `[]` would mean
  "it had none". Only one of those is ever true, and a reader has to be able to tell them apart.
- **What the optimizer is shown is what the agent DECIDED — the environment is where a decision is
  carried out, which is a different question.** `reasoning_trace` renders to L1 under the header
  `MODEL REASONING`, so a digest built from the environment's log alone puts that header over
  something that is not reasoning, and L1 reads it literally. Drop the `user` turns when you build
  it — those are the task WE handed the agent, not something it produced.

## Injection is not consumption — one backend, and the absence elsewhere is DECLARED

**On every connector but one, the candidate prompt is IN the request, so "did the model receive it"
is not a question.** termnorm, dspy and promptpotter all put the rendered prompt on the wire.
Harbor, by default, does not: `harbor.py::_write_skill` drops it into the container as an Agent
Skill, and `terminus-2` eagerly shows the model only the frontmatter — name, description, location.
**The candidate's prompt is the BODY, and it reaches the model only if the model opens the file.** An episode that never does ran as no-skill, so every arm of that
round was the same episode, the δ ruler is flat by construction, and the round reports a tie it
never measured. `harbor.py::_skill_opened` measures it and `SKILL_KEY` is a required observation.
Under `skill_delivery: system_prompt` the same key reports whether the first request CARRIED the
body (`_skill_in_first_request`), so a healthy campaign in that mode reads 1.0 rather than warning.

**There is deliberately no core `turn_scalars` member for this, and that hole is not an oversight to
fix.** A term whose value is decided by which backend you are on is not a core projection: on the
other three it would be the constant `1.0`. The rule generalizes rather than the key — **ask of any
new connector whether what it injects is what the model consumes**, and if the two can diverge, that
gap is a measured observation and not a diagnostic.

**A per-step aggregate can flatter, and Harbor's does** — `_aggregate_step_rewards` drops a step
with no verifier result from the denominator, so a crashed step scores better than a wrong one.
`harbor.py::_unscoreable_step` raises instead. Whatever the next episodic backend rolls up, ask what its
roll-up does with a step that produced nothing — the answer is usually silence.

## The measured unit — declared, never sniffed

A connector declares what ONE measured row IS via `Connector.measured_unit` (`MeasuredUnit`):
`sample` by default, `cell` on `promptpotter` (one outer row is a whole inner campaign) and on
`harbor` (one row is a whole agent episode in a container). What the two share is not their
transport but their SHAPE — a row that takes minutes, spends on its own account and can fail
halfway — which is what the word marks. It rides the same declared-capability channel as
`execution` (through `BackendClient.measured_unit` to `dashboard.json::measured_unit`), so **no prompt panel, CLI line or browser surface holds a literal
`"cell"` / `"sample"` beside a count**. Pluralising and counting are `unit_plural` / `unit_count` on
the producer, never an f-string at the render site.

**A renderer may not infer it** — a statistical field (`mean_round_delta`) deciding vocabulary is the
shape this field deletes.

Two places keep their own word on purpose: the **evidence** surface calls every row a cell because a
selection there spans campaigns, datasets and backends, so no single connector's noun applies; and
`ruler_n` / `DeltaRuler` count **ruler cells**, a δ-scale membership that is the same on every
backend ([`../../docs/methods/verdict-resolution.md`](../../docs/methods/verdict-resolution.md)).

**And `cell` implies NOTHING about the run's CONTROL LOOP — a flag reasoning "a cell is expensive,
therefore…" is the one to refuse.** A connector declares what a row costs (`max_cells_in_flight`,
the ceiling it may be run at; `cells_hold_the_machine`, whether that ceiling is the MACHINE's —
every run on it drawing one pool — because a cell holds a container here; `cell_envelope_s`, the
wall clock ONE of them may spend); how long an operator's look-ahead arming lasts is the round's
and the operator's to decide — no connector can see the round it is inside. Refuse a second flag
beside `measured_unit` set by RESEMBLING the recursion rather than by any fact about the run.

**`cell_envelope_s` is the test passing, and the template for anything that wants to join it:** it
is a fact about one cell of THIS backend, resolved from the same pair the request is built from,
and it decides nothing — the round it sits in is neither consulted nor changed.
`application/scoring/cell_envelope.py` enforces it. **A ceiling on measured COUNTS would fail the
same test** — tokens and dollars jitter run to run, so the same cell would be cut on one run and not
the next.

## Registering a connector

**A built-in is a module under this package defining `CONNECTOR` — never a `register()` call, and
every module here but `protocol` is one.** `registered()` walks them, merges the
`promptpotter.connectors` entry points and runs `_validate` over both, once per process.
**The connector table completes at a declared step, never at import** — owned by
[`../application/CLAUDE.md`](../application/CLAUDE.md) § Subpackages; nothing here may read the
table at module scope, and a check that must fail before a run spends is the connector's
`completion_check`, which that step runs. A connector shipped from **another** package declares the entry point
instead and touches nothing here ([`stable-api.md`](../../docs/developer/stable-api.md) §1).

## A connector is trusted code, not sandboxed — and that is stated, not implied

Loading one imports its module into this process, where it sees the provider API keys, the
tenant tree and the identity store, exactly as a module we ship does. Entry points do not
weaken that boundary (anything that can install a distribution into this environment can
already run code here), but they do make the trust *explicit*: installing a connector package
is trusting its publisher completely, and this repo's capability scoping (ADR-0005) governs
API principals, not in-process code. **`connector_origins()` is the audit surface** — it names
the distribution behind every registered key, including the ones that are ours.

Two rules follow, both enforced in `_load` / `_validate`. **A plugin may not shadow a
built-in:** `get("promptpotter")` is read by name by the L4 inner runner
(`application/runner/inner/tasks.py`), so which object answers that key is not a third
party's call. **A broken plugin is fatal, never skipped:** skipping would trade a loud error
naming the package for `connector 'x' not registered` at mint time, with nothing pointing at
the cause.

**Discovery is two paths; validation is one. Deliberately.** Declaring the built-ins as entry
points would make the table depend on this distribution's installed metadata, so a plain
source-tree run would find zero backends. The property worth protecting — a half-wired connector fails before a run
spends, never mid-campaign — lives in the validator, not in the channel it arrived through.

## The credential rides the connector

**`Connector.auth_token() -> str | None` is the ONLY route by which a bearer token reaches
the wire, and `build_backend_client(connector, base_url)` (`infrastructure/backend.py`) is
the ONLY place a `BackendClient` is constructed** — it reads the token off the connector it
was handed. Never name a credential at a construction site: a `settings.TERMNORM_TOKEN` passed
there reaches whatever `remote_http` connector was resolved, POSTing TermNorm's secret to its host. An `in_process` connector has
no wire, so declaring a token on one fails the registry guard.

## Conventions

- Wire adapters are pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` returns `(queries, index_terms)` — the index_terms
  list may be empty for connectors with no retrieval index.
- **A declared `experiment_file` OWNS its dataset's panel, and
  `dataset_access.py::dataset_experiment` is its ONE reader** — `init_services`, and every read
  outside a run: `GET /datasets`, `/origins`, `/cells` and the campaign pipeline. L4's `runner/inner/` is the exception: it re-reads its typed
  `inner_tasks.yaml` per cell. Ordered before the row ladder, never a
  fallback: rows cached under the same name describe a different instrument, and a resolver that
  knows only MATERIALIZED banks answers a connector-owned one EMPTY, which is not a fact about the
  dataset. Panel ORDER is the `sample_id` (`samples_from_dicts` numbers positionally).
- **`query` is whatever addresses one unit of work, and on an episodic backend that is an ID.**
  A judge falling back to it then grades against an identifier, so a task carrying a real question
  declares it and it rides `Sample.question` (`domain/sample.py`) — the only channel that reaches
  a judge, which is handed the measured row and never the `Sample`.
- **Declare every key the payload always carries** in
  `Connector.required_observation_keys`. An undeclared key is dropped at
  `sample_measurement.py::measure_sample` and never reaches `pipeline_data`, so the
  scoring formula grades a measurement it never received and nothing raises;
  `wiring.py::_verify_required_observation_keys` RAISES at init instead. Unlike
  revision pinning below this is a wrong number, not drift — so it fails the run.
  Empty (default) = the backend guarantees no key.
- **Revision pinning is opt-in.** A connector can set
  `Connector.expected_revision` (the backend SHA/version this rev was
  developed against) and a `Connector.version_check(http, base_url) -> str | None`
  hook reading the backend's self-reported revision. Init
  (`application/initialization/wiring.py::_verify_connector_revision`)
  WARNs on drift; no-op when either field is `None` — cross-repo drift surfaces at session
  start, not later in spend accounting.
