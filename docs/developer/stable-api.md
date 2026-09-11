# Stable API surface — what forks can rely on

> **Stable API v1**

What downstream forks build on without breaking on the next refactor. Anything not listed is **internal** — free to rename, restructure, or delete in any PR. Forks on internal symbols are on their own. Breaking changes here bump the major; pre-release the version is informational only. Non-promises spelled out in §8.

## 1. Connector protocol

Frozen dataclass at `promptpotter/connectors/protocol.py::Connector`:

```python
@dataclass(frozen=True)
class Connector:
    name: str                                                       # lowercase id; matches pipeline.yaml::backend_type
    wire_adapter: Callable[[str, dict | None], dict]                # outbound HTTP body shaper
    session_factory: Callable[[], SessionProtocol]                  # fresh session per BackendClient
    extract_experiment: Callable[[dict], tuple[list[dict], list[str]]]  # → (queries, index_terms)
    execution: ConnectorExecution = "remote_http"                   # "remote_http" | "in_process" (no HTTP; TRANSPORT only)
    in_process_run: InProcessRun | None = None                      # async (workload, query, payload) -> {"data": …}; required iff in_process
    required_observation_keys: tuple[str, ...] = ()                 # keys the payload ALWAYS carries; init RAISES if the dataset declares no mapping
    experiment_file: str = ""                                       # on-disk experiment doc read from the dataset dir in place of a sample table
    resolve_experiment: ExperimentResolver | None = None            # parsed experiment_file -> the document every read sees (a named roster pinned)
    identity_config: Callable[[Path, Mapping | None], dict] | None = None  # (dataset dir, resolved experiment) -> MEASUREMENT IDENTITY, not the wire
    measured_unit: MeasuredUnit = "sample"                          # what ONE row is CALLED — "sample" | "cell"
    expected_revision: str | None = None                            # backend rev this PP rev expects (paired w/ version_check)
    version_check: VersionCheck | None = None                       # async (http, base_url) -> str | None; init WARNs on drift
    preflight: PreflightFn | None = None                            # async (backend_url) -> None reachability probe; None opts out
    auth_token: AuthTokenFn | None = None                           # () -> str | None bearer for THIS backend; unset when in_process
```

Plus the first-tenant draft seeds (`default_pipeline`, `default_node_config`, `default_optimization`, `default_exclude_nodes`, `node_types`) and `max_cells_in_flight`, which shape the ingest UI and the scoring walk rather than the measurement. **The dataclass is the roster** — read the field notes there, which say what each one costs to get wrong.

Three of the fields above are on this page because omitting them produced WRONG NUMBERS rather than a missing feature, silently:

- **`required_observation_keys`** — an undeclared key is dropped at `sample_measurement.py::measure_sample` and never reaches `pipeline_data`, so the formula grades a measurement it never received. `wiring.py::_verify_required_observation_keys` raises at init instead.
- **`identity_config`** — what the cell was measured ON, when that is not in the wire payload (a Harbor task's git pins, the inner optimizer's effective revision). Without it, banked rows are silently replayed against bytes nobody read.
- **The answer shape** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The answer shape — a query yielding `ground_truth: None` declares it, and `extract_experiment` is the only place a connector may.

`SessionProtocol` (`promptpotter/domain/connector.py`): `async set_terms(http, base_url, terms)` (backend handshake; noop ok) · `async recover(http, base_url)` (re-establish after transport error).

`InProcessWorkload` (`protocol.py`) is the run's own state, handed to every `in_process_run` call: `experiment` (the resolved `experiment_file` the samples came from, `None` without one) · `program` (what an embedded host passed to `open_session`, §5b; `None` otherwise). Per-run state rides it — never a ContextVar or a module cache.

**Registering one, from your own package — no fork.** `promptpotter.connectors` is a published entry-point group:

```toml
[project.entry-points."promptpotter.connectors"]
anything = "my_package.connector:CONNECTOR"
```

The object named must be a `Connector`; **its `name` field is the registry key**, so the entry-point label is free and a package cannot claim a key its connector does not declare. No edits to `application/campaign_config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

**What a plugin is held to** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md); all three rules are enforced in `connectors/__init__.py` when the table completes, and each raise names its rule. What this page promises is only that they will not tighten within v1.

`connector_origins()` maps every registered name to `"built-in"` or `"<distribution>: <module>:<attr>"` (the entry point's *value*, not its label — the label is free, the value is what was imported), so a name that greps to nothing in this tree can still be traced to its package. Audit what is loaded with:

```bash
python -c "from promptpotter.connectors import connector_origins as o; print(*o().items(), sep='\n')"
```

⚠️ **A connector is trusted code, not sandboxed** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md). What v1 promises here is narrower and worth saying out loud: entry points do **not** lower that bar, and no future version will make them a sandbox. The capability scoping in [ADR-0005](../adr/0005-delegated-principals-and-capability-scoping.md) governs API principals, not in-process code.

Adding one *to this repo* is one new file under `promptpotter/connectors/` defining `CONNECTOR`. Built-ins are deliberately **not** declared as entry points: reading them from install metadata would make a source-tree run with no metadata find zero backends.

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug · `extract_experiment` MUST return `(queries, index_terms)` (the latter may be empty).

---

## 2. Scoring formula DSL

Configured per dataset via `campaign.yaml::scoring`:

```jsonc
{
  "scoring": {
    "per_sample": "acc",                    // required: was this cell RIGHT
    "per_cell": "acc * 500 / max(1, latency)", // optional: what it was WORTH — θ is fit on this
    "scorer_id": "acc_v1"                   // optional: explicit id
  }
}
```

**Addressable namespace** (`application/scoring/formula/compiler.py`):

- **Builtins:** the `_SAFE_BUILTINS` map in that module — arithmetic and `math` only. Nothing else from `__builtins__`.
- **Evaluators:** any registered name, per-sample or per-round. `all_evaluators()` (`application/scoring/evaluators.py`) is the PACKAGE registry, `evaluators_meta()` its served projection; a campaign's own `judge` (§3) is addressable by its judge name too, and is deliberately not in that registry — a grader belongs to one campaign, not to the process. Names are stable and implementations may change — read the registry, not a list here.
- **Matchers:** `SCORING_FUNCTIONS` (`application/scoring/formula/matchers.py`), splatted into the same namespace, so a formula calls one exactly like an evaluator. This is the LABEL arm — it reads answer prose and decides HIT/MISS (`exact_match`, `gsm8k_match`, …), which is why a `per_sample` formula almost always names one. Read the map; it holds what a campaign can actually reach, and nothing is kept in it for a caller that does not exist.

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
- `nodes` — node graph. Per-node: `runtime` (`backend`/`frontend`/`in_process`) · `node_role` (`candidate_source`/`ranker`/`enricher`/`cache`/`""` — the WIRE key; it maps to `PipelineNode.node_type`, which is the model field, not the key you publish) · `optimizer.param_keys` (list — the SEARCH AXES this node opens to the optimizer; `provider`/`route_order` are stripped whatever it says, `model` is opened by listing it, and a campaign narrows the rest) · `optimizer.observation_mappings` (wire-name → optimizer-name) · `optimizer.langfuse_type` · `config` (per-dataset overlay merged onto the wire payload).
- `pipelines` — named pipeline variants.
- `available_models` — the model MENU: what the check-in offers, and the fallback bound on `model` for a node declaring no `optimizer.param_allowed_values.model`. That per-node list is the PERMITTED set — what the optimizer may pick where the axis is open, and what a human fork may steer to un-tainted. A check-in dataset gets the menu from `Connector.available_models`.
- `resolved_prompts` — prompt-template map keyed by version. (`resolved_schemas` is a
  sibling file, not a key: for `_optimizer` it is generated into
  `resolved_schemas.json` by `scripts/build_optimizer_schemas.py`.)

### `campaign.yaml`

Campaign knobs + scoring + optimizer LLM. Validated by `application/campaign_config.py::CampaignConfig` with `extra="forbid"` — unknown keys raise at boot. See `CampaignConfig` for the full field list.

**Top-level keys.** `dataset_name`, `scoring`, `judge`, `sp_budget_round`, `exclude_nodes` (drop pipeline nodes by name), `pipeline_overlay` (per-node config overlay), `optimization`. (The optimizer LLM is install-global — `promptpotter/assets/optimizer/pipeline.yaml` — not a campaign key.)

`judge` names a registered LLM-as-judge and the models to run it on — `{name, stages: [{role, model, provider, temperature}]}` — for datasets whose answer no matcher can grade. Its verdict is banked as a per-sample observation the `scoring` formula reads by NAME (never a call: a judge is a measurement, not a formula term). **Its models are inherited from nothing** — not a node's permitted set, not node config, not the optimizer's. A third party ships a judge through the `promptpotter.judges` entry-point group, validated like §1's connectors; contract: [`../../promptpotter/judges/CLAUDE.md`](../../promptpotter/judges/CLAUDE.md).

**`optimization` knobs:** the stable contract is the mechanism, not a
frozen key/default table (same rule as §4). Every knob is a
self-describing field on `OptimizationConfig` in
`application/campaign_config.py` — `Annotated[T, Knob(scope, *estimands)]` plus a
`Field(description=…)` — and `application/knobs.py::KNOBS` is the walked
taxonomy. Only `degradation_threshold` is required; everything else
defaults. Read defaults off the fields, never
off a doc.

**Optimizer LLM:** install-global, **not** in `campaign.yaml`. Provider, model, temperature, `reasoning_effort`, and `max_tokens` are per-node config in `promptpotter/assets/optimizer/pipeline.yaml` (`nodes.{l1_generate|l1_critique|l2_context|l3_plan|checkin}.config`), resolved inside `llm_call` like any other node tunable. One file configures the optimizer for every campaign.

Constants moved out of `campaign.yaml` (they live next to their consumer): L1 candidate-generation temperature (the `creativity` arg in `l1/generate.py`, driven by `l1_overrides.creativity`, defaulting to the `l1_generate` node temperature), L2/L3 transition temperatures (the `l2_context`/`l3_plan` node temperatures), runaway-loop ceiling (`runner/loop.py::HARD_CAP`), stale-data recovery ladder (`scoring/sample_measurement.py`). PoBB lock-in went the other way and stayed campaign config — `pobb_lock_in` / `pobb_lock_in_n_min` / `mechanisms.elimination.leader_lock_in`.

The yield-drought escalation rule (`l2_axis_yield_drought`) is permanent — no opt-in flag. L2 and L3 are always-on architecture.

### Other files

- **`prompts/{node}.yaml`** — 8-field `PromptTemplate` per node. Schema: `domain/opt_search_point.py::PromptTemplate`. Loaded by `application/datasets/prompts.py::load_node_prompt`.
- **`task_description.md`** — free-form markdown; decomposed at `init` into the `task_context` dict on `OptSearchPoint`.
- **`dataset.md`** — operator guide; free-form, not parsed.
- **`task_context.yaml`** — the committed task framing; written once by the `checkin` decomposition (`application/optimization/task_context.py::decompose_prompt_fields`) or by web ingest at commit, and read free on every later run through `infrastructure/store/dataset_access.py::dataset_task_context_path` on the tenant-first ladder.

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
| **Benchmarks** | the checkout's `datasets/` → `promptpotter/assets/benchmarks/` | Sample dataset **definitions**, read-only on both shapes. Anything DERIVED from a definition on the operator's machine lands in the user-data root instead, under a flat keyed file per kind — the HuggingFace rows at `benchmark-rows/{name}.json`, the first-sight LLM decomposition of `task_description.md` at `task-context/{name}.yaml`. Never beside the definition, which under a wheel is inside `site-packages`. A tenant dataset of the same name shadows an installed one. |

**`PROMPTPOTTER_HOME` is stable.** Set it to relocate the whole user-data tree; it is
read once at import, so it is an environment decision, not a runtime one.

**`$PROMPTPOTTER_HOME/optimizer/pipeline.yaml` is stable, and it is the one install asset
an operator may shadow.** Present, it replaces the packaged optimizer manifest (provider /
model / temperature per optimizer node); absent, the packaged one is read. Its two
neighbours are deliberately not overridable — `resolved_schemas.json` is generated from the
Pydantic models, `sets/*.yaml` is the L4 instrument — so the seam is one file, not the
directory.

Both derived asset trees (`assets/webapp/`, `assets/benchmarks/`) are staged by
`scripts/build_release.py`, the supported way to build a wheel — a bare `uv build` produces
one that quietly serves no dashboard and resolves no dataset. There is no `REPO_ROOT`: the
parent walk that once stood for all three roots resolved to `site-packages/` when installed,
which is both where `pip` deletes on upgrade and where the HuggingFace `datasets` library lives.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the loop-mint verbs; lifecycle, run-control, diagnostic and maintenance verbs exist beside them. **The flag set is `presentation/cli/parsers.py`** and what each does to the tree is [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)'s — a table here is one `--help` away from its source and has drifted from it before. What v1 promises is that the two verbs, and the flags that file declares for them, keep their meanings.

Two behaviours a fork may rely on, neither of them readable off `--help`:

- Every `new` mints a fresh root cycle; on content-hash collision with an existing root the `cycle_id` gains a `_r2` / `_r3` discriminator so the new run lands in its own directory tree. The prior campaign is preserved.
- A launch flag may only lower a budget. `set-budget` is the verb that raises one.

The maintenance and diagnostic verbs are not part of v1.

## 5b. Embedded launch entry (Python)

The programmatic peer of §5's verbs — `application/embedded_run.py`, for a host program driving
one campaign inside its own event loop:

```python
session = await open_session(dataset_name, *, backend_url=…, backend_id=…, on_status=None,
                             identity=None, stores=None, program=None)
observers, dataset, origin = await mint_and_score_origin(
    session, train_data, campaign_config, *, pipeline_params=None, display=None, on_status=None)
result = await run_campaign(observers, dataset, origin, campaign_config, *, session,
                            langfuse_session_id=None, spend_budget_usd=None, token_budget=None,
                            mode=None)
```

Three steps rather than one because every caller does its own work between them. It mints through
the same `prepare_fresh_cycle` prologue `new` and the web mint run, so the cycle it produces is
resumable, forkable and diagnosable by the §5 verbs — that is what this seam buys over a private
loop. `identity` / `stores` pass through to `init_services`; without them a host writes into the
anonymous `projects/default/` tenant. `program` rides the backend client as
`InProcessWorkload.program` (§1) — the host's own code, for an in-process backend with no service.
**`origin_gate` defaults to `strict` and a host has no TTY**, so `run_campaign` blocks at round 0
until something answers — call
`submit_gate_decision(cycle_dir, "rescore"|"proceed"|"abort")` from another task, or set the knob
off. It is `application/`, so it renders nothing: pass `LiveDisplay.for_campaign(session,
campaign_config)` for the run readout, and `presentation/views/completion.py::report_completion`
for the closing box.

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
template = export.template()          # PromptTemplate — fields by name, few-shot intact
export.measurement.composite_fitness  # under export.measurement.formula, never a bare number
```

Four rules hold it, each a defect of DSPy's own `save()` inverted (`domain/export.py` argues them):
**fields by name** (their `load_state` zips positionally with `strict=False`, so a signature that
gained a field reloads scrambled and raises nothing) · **provenance inside the file** — the fitness
under its named formula, n, lift + CI, θ, the rows' hash, the optimizer manifest, which is the half
we compute and they cannot · **an `artifact_version` a reader refuses on**, since we owe no
back-compat · **JSON and scalars, never pickle**. `tuned_params` carries the node config the winner
ran under, minus each node's rendered prompt — that is `prompt_fields` again, and one artifact does
not state a fact twice.

Absent when no round ever closed: an artifact whose point is a fitness with provenance may not
carry an unmeasured one.

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

Subscribers read via `DerivedView.on_record(record)` and MUST NOT write any campaign artifact beyond their declared allowlist (fails loud; see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## 7. Per-cycle artifact paths

**What each file holds, and who writes it** — owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § File reference. What v1 promises is narrower and is only stated here: inside `campaigns/{campaign_id}/cycles/{cycle_id}/`, the contract for any tool reading per-cycle results is **`rounds/round_NNNN.json` + `index.json` + `log.md`**, and `export.json` (§5c) for the winner alone. Everything under `.runtime/` may change shape between minor versions, ledger records included — §6 promises the record family, not the file layout around it.

Sibling cycles (forks, diag) live flat under `cycles/` alongside the root, each carrying its own per-cycle artifacts including its own `dashboard.json`, which a fork seeds from its parent at the cut.

## 8. What is NOT stable

- **Internal module structure** beyond §1–§7. The dispatch hub split into `hub/{bundle, injections, facade}` is internal — only the public symbols (`DispatchHub`, `injections`, `build_bundle`, `validate_template`) are stable.
- **Private types** (`_Injection`, `_TEMPLATE_EXTRAS`, etc., plus any `_`-prefixed name). Package `__init__` files are namespace markers that re-export nothing — §1–§7 is the whole public surface, not whatever a package surfaces.
- **`__all__`** — this document is the public surface; `__all__` is a reader's hint and nothing more. It is mechanically inert here (`implicit_reexport = true`, no `import *` anywhere), so neither runtime nor mypy consults it, and a name listed there is not thereby promised. Prune an entry nothing imports rather than reading it as a contract.
- **Runtime dataclass shapes** not in §1–§7 (`CycleSlice`, `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc.).
- **In-memory caches** and their invalidation strategies (optimizer LRU caches, the dispatch hub's pipeline-param-catalogue cache, etc.).
- **Prompt templates** at `promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **Test helpers** (`tests/factories.py`, `tests/conftest.py`).
- **The `webapp/` layout.** The webapp + control plane ship and serve users; internal component layout stays free to move.
- **The REST API and the events stream**, specified though they are (`docs/specs/api-openapi.yaml`, `events-asyncapi.yaml`). They carry **no inbound credential**: `presentation/api/middleware/oidc.py` derives identity from a browser SESSION COOKIE and nothing else — no bearer token, no API key anywhere on the inbound path — so a third party reaches them only by running the server with `PROMPTPOTTER_AUTH=off`, i.e. with no auth at all. That makes this a same-origin browser surface plus a local no-auth mode, not an integration surface, and saying so is the honest state: per-endpoint guarantees would promise something it cannot yet keep. (The one bearer token the repo holds runs PP→TermNorm — outbound, the other direction.)
