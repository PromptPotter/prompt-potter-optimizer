# connectors/ — backend-specific hook bundles

Each connector packages everything PromptPotter needs to talk to one
backend kind. A connector is one file under this package exporting a
``Connector(...)`` binding (`protocol.py`), registered via the dict in
`__init__.py`. Operating one is local too: model/provider switches go in
`datasets/{name}/pipeline.yaml::nodes.{name}.config`, never in the backend's repo.

**Adding one is local to `connectors/<name>.py` + a dataset directory, and the way that
claim fails is what to watch for.** Naming two files it must not edit
(`application/campaign_config.py`, `infrastructure/backend.py`) is true and beside the point:
`harbor` needed edits to five core files, none of them those two. Every one
was core INFERRING something a connector should DECLARE. So the rule to hold this to is not a
file list; it is that **whatever the next connector has to reach into core to fix, the fix is a
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

> **`import dspy` is function-local, and must stay that way.** `__init__.py` imports every
> built-in eagerly, so a module-level import would break `import promptpotter.connectors` for
> every install that did not ask for the `[dspy]` extra — which is all of them by default. The
> caller-facing half (`presentation/teleprompter.py`) imports it at module level instead, because
> its only importer is the caller and a missing extra should stop them there, by name.

> **`llm_only` is a NODE name, never a connector.** Every single-node benchmark
> declares an `llm_only` node inside a `termnorm` pipeline and routes over HTTP to the
> server like any other. A connector of that name once existed (the no-server "Feature
> A" case) and was **deleted** — it had zero dataset adopters, and its in-process answer
> extraction merely duplicated what TermNorm's `_step_llm_only` already does over the
> wire. Do not re-add it: the single-node case is served by the TermNorm connector
> accepting an `llm_only` pipeline.

## What the second connector taught the boundary

Adding `promptpotter` exercised the abstraction and the protocol held unmodified. Three
things the next connector should heed. **Wire payload shape is connector-specific** — each
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
touch one side, fix both. Debugging war-stories →
[`../../docs/operations/backend-integration.md`](../../docs/operations/backend-integration.md)
§ Debugging the highway.

## Execution mode — declared, never name-branched

A connector declares **how its backend runs** via `Connector.execution`
(`ConnectorExecution`): `remote_http` (default — posts to a live `/matches`)
or `in_process` (runs in this process, no HTTP). `BackendClient.run_query`
**dispatches on this declared mode, never on the connector name** — so a new
backend's transport is a capability it declares, not a branch in the core loop.

**The `in_process` arm is wired (SHIPPED).** `run_query` calls the
connector-supplied `Connector.in_process_run(query, payload) -> {"data": {…}}` —
the same shape the scorer parses from an HTTP `/matches` body. The registry guard
(`__init__.py`) enforces the pairing: an `in_process` connector MUST supply
`in_process_run`, a `remote_http` one MUST NOT. Three connectors ride the seam today, and
`harbor` is the one that shows the mode is not a synonym for "cheap and local": its cell holds a
container, spends real money and takes minutes, so it declares `measured_unit="cell"` exactly as
the recursion does. **`in_process` is a statement about TRANSPORT — there is no HTTP — and about
nothing else.**

- **`promptpotter` (Feature B, SHIPPED)** — `in_process_run` is a thin delegate to
  `application/runner/inner/spawn.py::run_inner_cycle` (running a whole inner
  campaign is heavy orchestration — it belongs in `application/runner`, not the
  connector). That runner calls `run_optimization` in its **own `asyncio.Task`**
  (the three per-task ContextVars — `_CYCLE_LEDGER` + `_CURRENT_ROUND`
  (`infrastructure/llm/telemetry.py`) and `_ABORT_CHECK`
  (`infrastructure/llm/rate_limit.py` — two files, not one) — isolate per task,
  not per call; the child gets a COPY, which is
  how `_ABORT_CHECK` carries the outer's pause into the inner run) under **sandboxed stores in a
  flat per-cycle registry `<workspace>/.inner/<key>/`**
  (`init_services(store=…)`; no active-pointer collision, and it holds no machine slot). It is
  named by (owned by) the spawning cycle but kept **flat, not physically nested** —
  physical nesting (`…/.runtime/inner/…/.runtime/inner/…`) blows past Windows'
  260-char `MAX_PATH` at depth 1; a flat registry stays shallow at every depth, so
  the **re-entrant** invariant holds (task spawns at every level → L5+ nests). The
  spawning cycle publishes its context via `publish_inner_spawn_context` (runner
  seam, every cycle) so this context-free hook can find where to sandbox + which
  inner benchmark to run. **Owner and asker are two facts, and a fork splits them:**
  once the cycle id is final `retarget_inner_spawn` moves only the *asker* an inner run
  stamps as `spawned_by.outer_cycle_id`, while the sandbox owner never follows a fork —
  a repaired cell CONTINUING the campaign the parent banked is the whole point, and one
  field meaning both filed every measurement a fork paid for under the cycle it
  superseded. The outer L1's optimizer prompt mutations apply to the inner
  `assets/optimizer/pipeline.yaml` prompts through a per-run override ContextVar
  (`set_optimizer_prompt_overrides`, set inside the inner task). One process, no
  networking. The localhost-endpoint option is retained only as the future
  hosted/multi-tenant worker mode: a new `execution` value, dispatched on
  uniformly, with no core-loop edit.
- **`harbor` (SHIPPED)** — `in_process_run` builds a `TrialConfig` and awaits Harbor's own
  `Trial.create(...).run()`; the container, the verifier and the reward file are all theirs, so
  this connector shapes payloads and reads a number rather than orchestrating anything. Three
  things it decides that the next episodic backend will face too. **The candidate prompt ships as
  an Agent Skill** — written to a temp `<dir>/<name>/SKILL.md` and passed through
  `AgentConfig.skills`, because that is the injection channel Harbor already has and the artifact
  class the skill-evolution literature evolves; the frontmatter `description` is FIXED and never a
  search axis, since the agent sees only that eagerly and a candidate free to write its own could
  win by making itself uninviting. **The panel is published from `extract_experiment`**, which
  init already calls with the parsed `harbor_tasks.yaml` — a second channel carrying the same file
  would be a redundant path, and `in_process_run` has no argument to carry pins in. **Trial
  scratch goes to the system temp dir, not the workspace**: Harbor nests
  `<trials_dir>/<trial>/<role>/…` and a workspace path is already deep, which is the same
  `MAX_PATH` wall that forced `.inner` flat. Nothing durable lives there — reward, digest and
  token counts are projected into the measurement archive, which is where a fact belongs.

## The answer shape — declared in `extract_experiment`, never inferred

**What one cell's ANSWER is, a connector declares by whether the queries it yields carry a
`ground_truth`.** Two shapes, and every core reader that needs to know asks the LABEL:

| shape | `extract_experiment` yields | who decides the score | the formula reads |
|---|---|---|---|
| **ranked-label** (`termnorm`, `dspy`) | `ground_truth: "<label>"` | a node emitting a ranking; `predicted` is compared to the label | `exact_match(predicted, ground_truth)` |
| **verifier-graded** (`harbor`, `promptpotter`) | `ground_truth: None` | something else, with a NUMBER — the task's own verifier, L4's outer proxies | a `required_observation_keys` entry: `max(0.0, min(1.0, env_reward))` |

`domain/scoring.py::is_verifier_graded` (one label) and `all_verifier_graded` (a round, a bank,
a dataset) are the ONE place that is asked. **Never `predicted == NO_RESULT`** — the natural
proxy, and it does not work: that sentinel is set by `terminal_ranking` returning nothing, which
a *dataset* decides. Harbor's `agent` node declares no `node_role` so it fires;
`promptpotter-self`'s `l1_critique` declares `ranker` so it never does. Two labelless backends,
opposite answers, from a proxy for something neither is about.

**There is deliberately no second declaration** — never a `Connector` flag beside it: an author
who writes `extract_experiment` correctly and forgets the flag gets back the entire class of
misdiagnosis the flag would exist to prevent. Same reasoning `__init__.py::_validate` applies to
an `auth_token` on an in-process connector — dead config that reads as protection.

**`Connector.answer_key` is not that flag, and the difference is the whole point.** The shape above
answers *is this cell graded against a label* — one fact, one home, on what `extract_experiment`
yields. `answer_key` answers *where the answer TEXT lives*, a different question the table never
asked: a verifier-graded cell still ANSWERED something. `measure_sample` had exactly one source for
`predicted` — the terminal ranking — so a backend emitting none got the `NO_RESULT` sentinel, right
for a verdict that is purely a number and wrong the moment anything reads the answer as text. It
was live: every `harbor` cell handed `answer_grounding` the literal string `NO_RESULT`, which the
rubric graded and banked a category for. The two declarations cannot disagree, because neither can
answer the other's question. **Declaring it is also what closes the ranking hack for good** — the
first bullet below exists because there was nowhere else to put an answer, and now there is.

Four things that follow, each one a defect this cost before it was a rule:

- **Do not invent a ranking to look ranked-label shaped.** Harbor emitted a one-element
  `final_ranking` holding a summary line; nothing read it and three readers had to un-believe it.
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

- **Project a published turn format; never author one.** Harbor's agents already write ATIF
  (`harbor/models/trajectories/step.py`), whose own field description calls `step_id` *"ordinal
  index of the turn"* — `TurnRecord` is a narrowing of it, dropping the training surface (token
  ids, logprobs, per-turn metrics) that no prompt, ruler or formula reads. Parse it as plain JSON:
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
  something that is not reasoning. A terminal pane is the record where commands are the work, and
  one `echo` of the answer where the evidence is inlined in the instruction — and L1 reads such a
  panel literally, proposing repairs to a step the task does not have. Drop the `user` turns when
  you build it — those are the task WE handed the agent, and on such a panel they are the whole
  haystack, quoted back at the optimizer as if the agent had produced it.

**A per-step aggregate can flatter, and Harbor's does.** `_aggregate_step_rewards` drops a step
with no verifier result from the denominator, so a cell whose first step scored 1.0 and whose
second CRASHED reports a perfect 1.0 while an honest wrong answer reports 0.5. `harbor.py::
_unscoreable_step` raises instead. Whatever the next episodic backend rolls up, ask what its
roll-up does with a step that produced nothing — the answer is usually silence.

## The measured unit — declared, never sniffed

A connector declares what ONE measured row IS via `Connector.measured_unit` (`MeasuredUnit`):
`sample` by default, `cell` on `promptpotter` (one outer row is a whole inner campaign) and on
`harbor` (one row is a whole agent episode in a container). What the two share is not their
transport but their SHAPE — a row that takes minutes, spends on its own account and can fail
halfway — which is what the word marks. It
rides the same declared-capability channel as `execution` — `Connector` → `build_backend_client` →
`BackendClient.measured_unit` → the dispatch bundle, the terminal readout and
`dashboard.json::measured_unit` — so **no prompt panel, CLI line or browser surface holds a literal
`"cell"` / `"sample"` beside a count**. Pluralising and counting are `unit_plural` / `unit_count` on
the producer, never an f-string at the render site.

**A renderer may not infer it.** `_r_inner_narratives` read `mean_round_delta` off the rows to work
out which world it was in — a statistical field deciding vocabulary is the shape this field deletes.

Two places keep their own word on purpose: the **evidence** surface calls every row a cell because a
selection there spans campaigns, datasets and backends, so no single connector's noun applies; and
`ruler_n` / `DeltaRuler` count **ruler cells**, a δ-scale membership that is the same on every
backend ([`../../docs/methods/verdict-resolution.md`](../../docs/methods/verdict-resolution.md)).

**And `cell` implies NOTHING about the run's CONTROL LOOP — a flag reasoning "a cell is expensive,
therefore…" is the one to refuse.** A connector declares what a row costs (`max_cells_in_flight`,
the ceiling it may be run at); how long an operator's look-ahead arming lasts is the round's to
spend, and no connector can see the round it is inside. The shape to watch for is a second flag
that ships beside `measured_unit` and is set by RESEMBLING the recursion rather than by any fact
about the run — which is how a declaration reaches every backend whose cells merely look alike.

## Registering a connector

**Declare a built-in as a data row in the `_BUILTIN` dict in `__init__.py` — never a
`register()` call, and never an append to `CONNECTORS`.** `CONNECTORS` is not that dict:
it is what `_load()` returns after merging `_BUILTIN` with the `promptpotter.connectors`
entry points and running `_validate` over both. Appending to it post-import registers a
connector that was never validated, which is the one thing the module exists to prevent.
A connector shipped from **another** package declares the entry point instead and touches
nothing here ([`stable-api.md`](../../docs/developer/stable-api.md) §1).

## A connector is trusted code, not sandboxed — and that is stated, not implied

Loading one imports its module into this process, where it sees the provider API keys, the
tenant tree and the identity store, exactly as a module we ship does. Entry points do not
weaken that boundary (anything that can install a distribution into this environment can
already run code here), but they do make the trust *explicit*: installing a connector package
is trusting its publisher completely, and this repo's capability scoping (ADR-0005) governs
API principals, not in-process code. **`CONNECTOR_ORIGINS` is the audit surface** — it names
the distribution behind every registered key, including the ones that are ours.

Two rules follow, both enforced in `_load` / `_validate`. **A plugin may not shadow a
built-in:** `CONNECTORS["promptpotter"]` is read by name by the L4 inner runner
(`application/runner/inner/tasks.py`), so which object answers that key is not a third
party's call. **A broken plugin is fatal, never skipped:** skipping would trade a loud error
naming the package for `connector 'x' not registered` at mint time, with nothing pointing at
the cause.

**Discovery is two paths; validation is one. Deliberately.** Declaring our own two as entry
points would be the tidier "single path", and it is wrong here: it makes
`import promptpotter.connectors` depend on this distribution's installed metadata, so a plain
source-tree run would find zero backends. The property worth protecting — a half-wired
connector fails at import, never mid-campaign — lives in the validator, not in the channel it
arrived through.

## The credential rides the connector

**`Connector.auth_token() -> str | None` is the ONLY route by which a bearer token reaches
the wire, and `build_backend_client(connector, base_url)` (`infrastructure/backend.py`) is
the ONLY place a `BackendClient` is constructed** — it reads the token off the connector it
was handed. Never name a credential at a construction site: four sites once passed
`settings.TERMNORM_TOKEN` to whatever connector had been resolved, so a second `remote_http`
backend would have had TermNorm's secret POSTed to its host. An `in_process` connector has
no wire, so declaring a token on one fails the registry guard at import.

## Conventions

- Wire adapters are pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` returns `(queries, index_terms)` — the index_terms
  list may be empty for connectors with no retrieval index.
- **A declared `experiment_file` OWNS its dataset's panel, and `dataset_access.py::dataset_panel_rows` is
  its ONE reader — init and every roster read (`/preview`, `/measurement-series`) resolve the bank
  through it.** Ordered before the row ladder, never a fallback: rows cached under the same name
  used to win, publishing no panel and leaving `_PANEL` unset while the run reported a healthy
  sample count. A resolver that knows only MATERIALIZED banks answers a connector-owned one
  EMPTY, which is not a fact about the dataset — hence one reader rather than a rule per surface.
  Panel ORDER is the `sample_id` (`samples_from_dicts` numbers positionally).
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
  WARNs on drift; no-op when either field is `None`. Pattern motive:
  the pre-flight gate's debug-state bullet, reaching across a repo
  boundary — cross-repo dependency
  drift becomes visible at session start, not weeks later in spend
  accounting. The same shape works for any future connector.
