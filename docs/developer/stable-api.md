# Stable API surface — what forks can rely on

> **Stable API v1**

What downstream forks build on without breaking on the next refactor. Anything not listed is **internal** — free to rename, restructure, or delete in any PR. Forks on internal symbols are on their own. Breaking changes here bump the major; pre-release the version is informational only. Non-promises spelled out in §8.

## 1. Connector protocol

The frozen dataclass `promptpotter/connectors/protocol.py::Connector`. **The dataclass is the roster and its field docstrings are the contract** — the signature, the default, and what each field costs to get wrong. Four fields are required (`name`, `wire_adapter`, `session_factory`, `extract_experiment`); every other one defaults, and `connectors/__init__.py::_validate` raises on a combination that cannot run.

Four declarations are named on this page because omitting one produces WRONG NUMBERS rather than a missing feature, silently:

- **`required_observation_keys`** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Conventions — a plugin declares every key its payload always carries, and init raises on one the dataset does not map.
- **`identity_config`** — what every cell is measured WITH, when that is not in the wire payload; without it, banked rows are silently replayed against bytes nobody read. What ONE cell is measured on rides that cell's row as `source_pin` instead (the field's docstring).
- **`sent_spend_bound`** — without one the cell is unbounded, and an unbounded cell cannot run under a ceiling at all. Declare the run it declares, never every retry it might need at once: the ceiling admits what the reservation does not cover, so an over-large bound buys nothing and silently holds the walk to one cell in flight.
- **The answer shape** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The answer shape — a query yielding `ground_truth: None` declares it, and `extract_experiment` is the only place a connector may.

`SessionProtocol` (`promptpotter/domain/connector.py`): `async set_terms(http, base_url, terms)` (backend handshake; noop ok) · `async recover(http, base_url)` (re-establish after transport error).

`InProcessWorkload` (`protocol.py`) is handed to every `in_process_run` call: `experiment` (the resolved `experiment_file` the samples came from, `None` without one) · `program` (what an embedded host passed to `open_session`, §5b; `None` otherwise). **Per-run state rides it** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode — a plugin keeps none in a ContextVar or a module cache.

**Registering one, from your own package — no fork.** `promptpotter.connectors` is a published entry-point group:

```toml
[project.entry-points."promptpotter.connectors"]
anything = "my_package.connector:CONNECTOR"
```

The object named must be a `Connector`; **its `name` field is the registry key**, so the entry-point label is free and a package cannot claim a key its connector does not declare. No edits to `application/campaign_config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

**What a plugin is held to** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md); all three rules are enforced when the table completes (`connectors/__init__.py::_validate` and `shared/plugin_registry.py`, the loader every entry-point group shares), and each raise names its rule. What this page promises is only that they will not tighten within v1.

`connector_origins()` traces every registered name to `"built-in"` or the distribution that shipped it. Audit what is loaded with:

```bash
python -c "from promptpotter.connectors import connector_origins as o; print(*o().items(), sep='\n')"
```

⚠️ **A connector is trusted code, not sandboxed** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md). What v1 promises here is narrower and worth saying out loud: entry points do **not** lower that bar, and no future version will make them a sandbox. The capability scoping in [ADR-0005](../adr/0005-delegated-principals-and-capability-scoping.md) governs API principals, not in-process code.

Adding one *to this repo* is one new file under `promptpotter/connectors/` defining `CONNECTOR`; built-ins are deliberately **not** entry points ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § A connector is trusted code).

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug · `extract_experiment` MUST return `(queries, index_terms)` (the latter may be empty).

---

## 2. Scoring formula DSL

Configured per dataset via `campaign.yaml::scoring`:

```jsonc
{
  "scoring": {
    "per_sample": "acc",                    // required: was this cell RIGHT
    "per_cell": "acc * 500 / max(1, latency)" // optional: what it was WORTH — θ is fit on this
  }
}
```

**The scorer id is derived, never declared** — `compiler.py::auto_scorer_id` over everything that grades a cell. A run stamps the one it graded under at `index.json::final.scorer_id`; `campaign.json` holds only a delta over the dataset file, so an id re-derived from it can name a scorer that never ran.

**Addressable namespace** (`application/scoring/formula/compiler.py`):

- **Builtins:** the `SAFE_BUILTINS` map in that module — arithmetic and `math` only. Nothing else from `__builtins__`.
- **Terms:** every per-sample evaluator in `all_evaluators()` (`application/scoring/evaluators.py`, the PACKAGE registry), banked into the row; and, in a `per_cell` formula, the per-cell terms `CELL_TERMS` names (`compiler.py`). `cell_terms_meta()` is their served projection — the scoring-mask editor's vocabulary, since a `score:` lens IS a `per_cell` formula. A per-round evaluator is a reading of a round, never addressable. A campaign's own `judges` (§3) are addressable by their term keys too, and are deliberately not in that registry — a grader belongs to one campaign, not to the process. Names are stable and implementations may change — read the registry, not a list here.
- **Matchers:** `SCORING_FUNCTIONS` (`application/scoring/formula/matchers.py`), splatted into the same namespace, so a formula calls one exactly like an evaluator. This is the LABEL arm — it reads answer prose and decides HIT/MISS (`label_match`, `gsm8k_match`, …), which is why a `per_sample` formula almost always names one. Read the map; it holds what a campaign can actually reach, and nothing is kept in it for a caller that does not exist.

Constants, name lookups, arithmetic operators (`+ - * / % **`) addressable. **Calls outside the registry are rejected at compile time** (enforced, not convention).

One stable signal every measurement carries: **`fitness`** — continuous, formula-driven, `[0,1]`, written only by `rescore_results`. It feeds the optimizer, SampleIndex and every cohort read. There is no companion `hit` boolean: it was only ever `fitness >= 1.0`, so it carried nothing the number beside it did not, and on a graded formula it was constantly false. Where a surface needs the discrete word, it derives one (`domain/scoring.py::is_hit`).

---

## 3. Dataset config schema

Each dataset lives at `datasets/{name}/` with:

### `pipeline.yaml`

Connector-described pipeline (the shape `GET /pipeline` exposes, plus an operator overlay). Required top-level keys:

- `name`, `version` — pipeline identity.
- `backend_type` — connector name; must match a registered connector.
- `backend_name` — display name for operator surfaces.
- `nodes` — node graph. Per-node: `runtime` (`backend`/`frontend`/`in_process`) · `node_role` (`candidate_source`/`ranker`/`enricher`/`cache`/`""` — the WIRE key; it maps to `PipelineNode.node_type`, which is the model field, not the key you publish) · `optimizer.param_keys` (list — the SEARCH AXES this node opens to the optimizer; `provider`/`route_order` are stripped whatever it says, `model` is opened by listing it, and a campaign narrows the rest) · `optimizer.observation_mappings` (wire-name → optimizer-name) · `config` (per-dataset overlay merged onto the wire payload).
- `pipelines` — named pipeline variants.
- `available_models` — the model MENU: what the check-in offers, and the fallback bound on `model` for a node declaring no `optimizer.param_allowed_values.model`. That per-node list is the PERMITTED set — what the optimizer may pick where the axis is open, and what a human fork may steer to un-tainted. A check-in dataset gets the menu from `Connector.available_models`.
- `resolved_prompts` — prompt-template map keyed by version. (`resolved_schemas` is a
  sibling file, not a key: for `_optimizer` it is generated into
  `resolved_schemas.json` by `scripts/build_optimizer_schemas.py`.)

### `campaign.yaml`

Campaign knobs + scoring + optimizer LLM. Validated by `application/campaign_config.py::CampaignConfig` with `extra="forbid"` — unknown keys raise at boot. See `CampaignConfig` for the full field list.

**Top-level keys.** `dataset_name`, `scoring`, `judges`, `sp_budget_origin`, `exclude_nodes` (drop pipeline nodes by name), `pipeline_overlay` (per-node config overlay), `optimization`. (The optimizer is `optimization.optimizer`, a manifest under `promptpotter/assets/optimizers/`; its nodes' knobs and models ride `optimization.nodes`.)

`judges` maps a scoring term to a registered LLM-as-judge and the models to run it on — `{term: {name, stages: [{role, model, provider, temperature}]}}` — for datasets whose answer no matcher can grade. Each verdict is banked as a per-sample observation the `scoring` formula reads by its term KEY (never a call: a judge is a measurement, not a formula term). **Its models are inherited from nothing** — not a node's permitted set, not node config, not the optimizer's. A third party ships a judge through the `promptpotter.judges` entry-point group, validated like §1's connectors; contract: [`../../promptpotter/judges/CLAUDE.md`](../../promptpotter/judges/CLAUDE.md).

**`optimization` knobs:** the stable contract is the mechanism, not a
frozen key/default table (same rule as §4). Every knob is a
self-describing field — the bench's on `OptimizationConfig` in
`application/campaign_config.py`, an optimizer's on its node member's knob model (potter's:
`application/optimizers/potter/knobs.py`), each `Annotated[T, Knob(scope, *estimands)]` plus a
`Field(description=…)` — and `application/knobs.py` walks both. Only `degradation_threshold` is
required; the bench's other knobs default in code, an optimizer's in its manifest. Read defaults
off the fields and the manifest, never off a doc.

**Optimizer LLM:** provider, model, temperature, `reasoning_effort`, and `max_tokens` are per-node config in the selected manifest (`promptpotter/assets/optimizers/{name}/pipeline.yaml::nodes.{node}.config`), resolved inside `llm_call` like any other node tunable; a campaign moves one through `optimization.nodes.{node}.config`. The check-in node is the bench's own, in `promptpotter/assets/checkin/pipeline.yaml`.

Constants moved out of `campaign.yaml` (they live next to their consumer): L1 candidate-generation temperature (the `creativity` arg in `l1/generate.py`, driven by `l1_overrides.creativity`, defaulting to the `l1_generate` node temperature), L2/L3 transition temperatures (the `l2_context`/`l3_plan` node temperatures), runaway-loop ceiling, in arms raced (`runner/loop.py::HARD_CAP_ARMS`), stale-data recovery ladder (`scoring/sample_measurement.py`). PoBB lock-in went the other way and stayed configurable — potter's `pobb` node `lock_in` / `lock_in_n_min` / `leader_lock_in`.

The yield-drought escalation rule (`l2_axis_yield_drought`) is permanent — no opt-in flag. Which LAYERS potter may reach is its `escalation` node's `escalation_ladder` (`full` / `l1_l2` / `l1`), the ablation switch; the individual rules are not separately toggleable.

### Other files

- **`prompts/{node}.yaml`** — 8-field `PromptTemplate` per node. Schema: `domain/opt_search_point.py::PromptTemplate`. Loaded by `application/datasets/prompts.py::load_node_prompt`.
- **`task_description.md`** — free-form markdown; decomposed into the campaign's `task_context` framing (`Cycle.framing`) by the first mint that finds none committed, or by a check-in (`new <name> --task-file`, the web check-in).
- **`dataset.md`** — operator guide; free-form, not parsed.
- **`task_context.yaml`** — the committed task framing; written once by the `checkin` decomposition (`application/bench/task_context.py::commit_task_framing`) or by web ingest at commit, and read free on every later run through `infrastructure/store/dataset_access.py::dataset_task_context_path` on the tenant-first ladder.

---

## 4. DispatchHub injection keys

`{{slot}}` names available in any optimizer prompt. Assembled into `dispatch/injections/registry.py::injection_table()` from the `@signal("<slot>", …)` decorator on each renderer (`injections/{panels,layer_state,catalogues,wounds}.py`). Adding a slot is one decorated renderer — key and body co-located. Using a slot not in the registry is a load-time `KeyError` via `validate_template`.

**The stable contract is the mechanism, not the slot list** — the set evolves, so this page doesn't freeze a table that drifts. The live set is the registry itself; the doc-level reference with per-slot detail is [`dispatch-hub.md`](dispatch-hub.md) § Reference.

**Per-template extras** (caller-supplied via `compile_prompt(**hub_dict, **extras)`): `l1_generate` → `{n_variants}` · `l1_critique`/`l2_context`/`l3_plan` → `{}` · `checkin` → `{consultation_instruction}`.

## 4b. Roots — where the package reads and writes

Three roots, owned by `promptpotter/config/paths.py`. A fork may rely on the resolution
rules; the constants themselves are internal.

| Root | Resolves to | Contents |
|---|---|---|
| **Install content** | `promptpotter/assets/` inside the package | The optimizer's own `pipeline.yaml` + `resolved_schemas.json` + `sets/{name}.yaml`, and the exported dashboard. Ships in the wheel; ours, not the operator's. |
| **User data** | `$PROMPTPOTTER_HOME` → the checkout's `.promptpotter/` when running from a source tree → the OS app-data dir | Campaigns, sessions, measurements, jobs, identity. |
| **Benchmarks** | the checkout's `datasets/` → `promptpotter/assets/benchmarks/` | Sample dataset **definitions**, read-only on both shapes. Anything DERIVED from one lands in the user-data root, never beside the definition ([`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § Dataset content has two tiers). A tenant dataset of the same name shadows an installed one. |

**`PROMPTPOTTER_HOME` is stable.** Set it to relocate the whole user-data tree; it is
read once at import, so it is an environment decision, not a runtime one.

**`$PROMPTPOTTER_HOME/optimizers/{name}/pipeline.yaml` and `$PROMPTPOTTER_HOME/checkin/pipeline.yaml`
are stable, and they are the install assets an operator may shadow.** Present, one replaces the
packaged manifest of that name (provider / model / temperature per node); absent, the packaged one
is read. The generated `resolved_schemas.json` beside each is deliberately not overridable, so the
seam is a file, never a directory.

Both derived asset trees (`assets/webapp/`, `assets/benchmarks/`) are staged by
`scripts/build_release.py`, the supported way to build a wheel — a bare `uv build` produces
one that quietly serves no dashboard and resolves no dataset. There is no `REPO_ROOT`, and
nothing resolves to `site-packages/`: `pip` deletes there on upgrade.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the loop-mint verbs; lifecycle, run-control, diagnostic and maintenance verbs exist beside them. **The flag set is `presentation/cli/parsers.py`** and what each does to the tree is [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)'s — a table here is one `--help` away from its source and has drifted from it before. What v1 promises is that the two verbs, and the flags that file declares for them, keep their meanings.

Two behaviours a fork may rely on, neither of them readable off `--help`:

- Every `new` mints a fresh `campaign_id`, but two `new` calls on an unchanged declaration SHARE their content-addressed root `cycle_id` and its origin score, then diverge from round 1 (`runner/campaign_ids.py::mint_campaign_id`). The prior campaign is preserved.
- A launch flag SETS the cycle's budget, raise or lower, over what the dataset declares, and stays as the cycle's standing ceiling for later resumes; the account admits the result whole or refuses the launch. `set-limits` moves it mid-flight.

The maintenance and diagnostic verbs are not part of v1.

## 5b. Embedded launch entry (Python)

The programmatic peer of §5's verbs — `application/embedded_run.py`, for a host program driving
one campaign inside its own event loop:

```python
session = await open_session(dataset_name, *, backend_url=…, backend_id=…, on_status=None,
                             identity=None, stores=None, program=None)
result = await run_campaign(session, train_data, campaign_config, *, readout_sink=None,
                            langfuse_session_id=None, limits, mode)
```

`limits` is a `promptpotter.domain.launch_limits.LaunchLimits(halt_at_accuracy=…,
spend_budget_usd=…, token_budget=…)`, the model the CLI flags and the `start-run` payload build; a
budget it declares sets the run's over the campaign's own (no admission — the host program holds no
slot), and `LaunchLimits()` declares none. `mode`
is `runner/entry.py::RunMode`, and `RunMode()` is a plain run.

Two steps rather than one because every caller does its own work between them. It mints through
the same `prepare_fresh_cycle` prologue `new` and the web mint run, and scores the origin inside
`run_optimization` like every other entry point, so the cycle it produces is resumable, forkable
and diagnosable by the §5 verbs and a stop during origin scoring closes it — that is what this seam
buys over a private loop. The origin's accuracy is `result.origin_accuracy`. `identity` /
`stores` pass through to `init_services`; without them a host writes into the anonymous
`projects/default/` tenant. `program` rides the backend client as
`InProcessWorkload.program` (§1) — the host's own code, for an in-process backend with no service.
**`origin_gate` defaults to `strict` and a host has no TTY**, so `run_campaign` blocks at round 0
until something answers — call
`submit_gate_decision(cycle_dir, "rescore"|"proceed"|"abort")` from another task, or set the knob
off. The run readout lands in the cycle's `readout.log` whatever the host passes; `readout_sink=print`
shows it as it is written, and `presentation/terminal/completion.py::report_completion` prints
the closing box.

Nothing on this path imports a server, and the dependency list says so: `pip install
promptpotter` is the engine, `[api]` is what a host adds if it also wants to serve the API and
the dashboard.

`load_dataset_campaign_config(path, overrides=…)` (`application/datasets/authored.py`) is the
supported way to shape a dataset's `campaign.yaml` for one launch without editing the shared file:
a nested mapping merged depth-first **before** validation, so an unknown knob raises here instead
of being silently dropped.

## 5c. The export artifact

Everything else this package writes answers *how the run went*; `cycles/{id}/export.json` answers
*what it found, and how good it is* — for a reader that will never open the campaign tree. Written
from the same call that stamps `index.json::final`, so both are projections of one `CycleResult`.

```python
from promptpotter.domain.export import parse_prompt_export
export = parse_prompt_export(Path("…/export.json").read_text())
template = export.template()          # PromptTemplate — fields by name
prompt = export.render()              # the prompt as scored: those fields, then its shots
export.measurement.composite_fitness  # under export.measurement.formula, never a bare number
```

Four rules hold it, each a defect of DSPy's own `save()` inverted (`domain/export.py` argues them):
**fields by name** (their `load_state` zips positionally with `strict=False`, so a signature that
gained a field reloads scrambled and raises nothing) · **provenance inside the file** — the fitness
under its named formula, n, the lift + CI over the parent (in accuracy, beside the parent's
accuracy as its bar — the lift over the origin is `bench.lift`, one entry per bench column), θ, the rows' hash, the
optimizer manifest, which is the half we compute and they cannot · **an `artifact_version` a reader
refuses on**, since we owe no back-compat · **JSON and scalars, never pickle**. `tuned_params`
carries the node config the winner ran under, minus each node's rendered prompt — that is
`prompt_fields` again, and one artifact does not state a fact twice.

That node config names the model the winner was won on, and a reader must treat it as binding: an
optimized prompt does not carry across models. [Why Prompt Optimization Works, and Why It Sometimes
Doesn't](https://arxiv.org/abs/2605.26655) found edits that help one benchmark often fail on another
across four model families, and [Prompting Inversion](https://arxiv.org/abs/2510.22251) reports a
scaffold that helps GPT-4o and hurts GPT-5. A model swap is a new run, never a copy of the winner.

Absent when no round ever closed: an artifact whose point is a fitness with provenance may not
carry an unmeasured one.

**Gap:** the provenance carries no optimization spend or tokens, which amortized lifetime cost
needs ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost
reporting) — [Databricks](https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization)
counts optimization cost plus serving cost over 100k requests, and a reader of this file cannot yet.

## 6. Ledger event types

Typed records on the per-cycle ledger (`.runtime/ledger.jsonl` — the
workspace-scoped sibling at `.workspace/events.jsonl` carries workspace
lifecycle, not cycle records). The record family — `PhaseRecord`,
`SnapshotRecord`, `ResumeCheckpointRecord`, `TokenUsageRecord`,
`LLMCallStartRecord`/`LLMCallRecord` — is the discriminated union in
`domain/run_records.py`; each record's fields are its dataclass, read
them there.

Forks within a family share one event stream via `CycleEventLog.inherit_from(parent, offset)`. The forked cycle's ledger FILE holds only its own appends; `iter()` walks the parent's records up to `offset` in front of them, and the parent carries a `ResumeCheckpointRecord` of kind `FORK_CUT`. The cut address is `index.json::forked_at_offset`, so that walk is reproducible off disk — but it yields a VIRTUAL position (parent + own) that is not the `sequence` a tail reports, which is the file's own line. Address a record as `(path, offset)`, never by an integer alone.

### The cut

A family is a partial order over **cuts**, and there are exactly two operations over them: **iterate** by time (the ray — one chronology, forks interleaved with their parents) and **reduce** to one by causality (a **fold** — the inherited prefix, then the cycle's own records).

A cut is `(cycle, offset)`, resolved as `domain/cycle_paths.py::Cut`. `offset` is the physical 0-based line index in that cycle's **own** ledger: the one space `ProjectionEnvelope.sequence`, `RayItem.offset`, `dashboard.json::at_offset` and `round_NNNN.json::at_offset` share, so a ray item addresses a fold directly. An inherited prefix is walked in front of that space and is not in it — which is why a cut rides `iter(own_limit=…)` and never a comparison inside the loop.

Three consequences that a surface must not re-decide:

- **A cut names a `CycleHop`, not a `CyclePath`.** Descent is spent before anything folds (`resolve_cycle_path` returns a sandbox-rooted store plus the leaf), so a path would be a lie at depth ≥ 1. `CyclePath` is the WIRE address and stays `RayItem`'s.
- **`cycle` and `hop` ride together** because they must agree; every construction site derives one from the other through `cycle_dir_for`.
- **Every artifact stamps the cut it is of**, so the ledger is the truth and each file is a cache: `?at=<offset>` on the dashboard route re-folds any past moment off disk, and `index.json::forked_at_offset` is a cut on the *parent*.

Subscribers read via `Projection.on_record(record)` and MUST NOT write any campaign artifact beyond their declared allowlist (fails loud; see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## 7. Per-cycle artifact paths

**What each file holds, and who writes it** — owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § File reference. What v1 promises is narrower and is only stated here: inside `campaigns/{campaign_id}/cycles/{cycle_id}/`, the contract for any tool reading per-cycle results is **`rounds/round_NNNN.json` + `index.json` + `log.md`**, and `export.json` (§5c) for the winner alone. Everything under `.runtime/` may change shape between minor versions, ledger records included — §6 promises the record family, not the file layout around it.

Sibling cycles (forks, diag) live flat under `cycles/` alongside the root, each carrying its own per-cycle artifacts including its own `dashboard.json`, which a fork seeds from its parent at the cut.

## 8. What is NOT stable

- **Internal module structure** beyond §1–§7. The dispatch hub split into `dispatch/{bundle, compose, injections, facade}` is internal — only the public symbols (`DispatchHub`, `injections`, `build_bundle`, `validate_template`) are stable.
- **Private types** (`_Injection`, `_TEMPLATE_EXTRAS`, etc., plus any `_`-prefixed name). Package `__init__` files are namespace markers that re-export nothing — §1–§7 is the whole public surface, not whatever a package surfaces.
- **`__all__`** — this document is the public surface; `__all__` is a reader's hint and nothing more. It is mechanically inert here (`implicit_reexport = true`, no `import *` anywhere), so neither runtime nor mypy consults it, and a name listed there is not thereby promised. Prune an entry nothing imports rather than reading it as a contract.
- **Runtime dataclass shapes** not in §1–§7 (`CycleSlice`, `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc.).
- **In-memory caches** and their invalidation strategies (optimizer LRU caches, the dispatch hub's pipeline-param-catalogue cache, etc.).
- **Prompt templates** at `promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **The optimizer node types** (`application/optimizers/nodes.py`, registered under `promptpotter.optimizer_nodes`). They hand a member the live `Cycle`, so a member built on them builds on internal state.
- **Test helpers** (`tests/factories.py`, `tests/conftest.py`).
- **The `webapp/` layout.** The webapp + control plane ship and serve users; internal component layout stays free to move.
- **The REST API and the events stream**, specified though they are (`docs/specs/api-openapi.yaml`, `events-asyncapi.yaml`). They carry **no inbound credential**: `presentation/api/middleware/oidc.py` derives identity from a browser SESSION COOKIE and nothing else — no bearer token, no API key anywhere on the inbound path — so a third party reaches them only by running the server with `PROMPTPOTTER_AUTH=off`, i.e. with no auth at all. That makes this a same-origin browser surface plus a local no-auth mode, not an integration surface, and saying so is the honest state: per-endpoint guarantees would promise something it cannot yet keep. (The one bearer token the repo holds runs PP→TermNorm — outbound, the other direction.)
