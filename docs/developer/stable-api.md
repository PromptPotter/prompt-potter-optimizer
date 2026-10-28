# Stable API surface — what forks can rely on

> **Stable API v1**

What downstream forks build on without breaking on the next refactor. Anything not listed is **internal** — free to rename, restructure, or delete in any PR. Forks on internal symbols are on their own. Breaking changes here bump the major; pre-release the version is informational only. Non-promises spelled out in §8.

## 1. Connector protocol

The frozen dataclass `promptpotter/connectors/protocol.py::Connector`. **The dataclass is the roster and its field docstrings are the contract** — the signature, the default, and what each field costs to get wrong. Three fields are required (`name`, `wire_adapter`, `session_factory`); every other one defaults — `extract_experiment` set means the dataset's `panel` owns its rows (`owns_panel`) — and `connectors/__init__.py::_validate` raises on a combination that cannot run.

Four declarations whose omission yields WRONG NUMBERS rather than a missing feature: `required_observation_keys`, `identity_config` (what every cell is measured WITH when the wire payload does not say; without it banked rows replay against bytes nobody read), `sent_spend_bound` (an unbounded cell cannot run under a ceiling; an over-large one holds the walk to one cell in flight) and the answer shape — the other three owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md). `SessionProtocol` is `promptpotter/domain/connector.py`'s. `in_process_run` is handed the measured `Sample` (its `source_pin` is the row `extract_experiment` yielded) and an `InProcessWorkload` carrying `experiment` and `program` (§5b); **per-run state rides that workload** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode.

**Registering one, from your own package — no fork.** `promptpotter.connectors` is a published entry-point group:

```toml
[project.entry-points."promptpotter.connectors"]
anything = "my_package.connector:CONNECTOR"
```

The object named must be a `Connector`; **its `name` field is the registry key**, so the entry-point label is free and a package cannot claim a key its connector does not declare. No edits to `application/campaign_config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`application/runner/inner/connector.py`](../../promptpotter/application/runner/inner/connector.py).

**What a plugin is held to** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md); all three rules are enforced when the table completes (`connectors/__init__.py::_validate` and `shared/plugin_registry.py`, the loader every entry-point group shares), and each raise names its rule. What this page promises is only that they will not tighten within v1.

`connector_origins()` traces every registered name to `"built-in"` or the distribution that shipped it.

⚠️ **A connector is trusted code, not sandboxed** — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md). v1 promises entry points do **not** lower that bar, and no future version makes them a sandbox; the capability scoping of [`decisions.md`](../decisions.md) § 0005 governs API principals, not in-process code.

Adding one *to this repo* is one new file under `promptpotter/connectors/` defining `CONNECTOR`; built-ins are deliberately **not** entry points ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § A connector is trusted code).

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug; the `identity_config` layer is removed before the adapter is called, so none strips it · `extract_experiment` MUST return the panel's rows in panel order.

---

## 2. Scoring formula DSL

Configured per dataset via `{dataset}/dataset.yaml::campaign.scoring`:

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
- **Terms:** every per-sample evaluator in `_REGISTRY` (`application/scoring/evaluators.py`, the PACKAGE registry), banked into the row; and, in a `per_cell` formula, the per-cell terms `CELL_TERMS` names (`compiler.py`). `cell_terms_meta()` is their served projection — the criterion's vocabulary, with which terms a dial can weigh (`dial`) and which show by default (`primary`). A per-round evaluator is a reading of a round, never addressable. A campaign's own `judges` (§3) are addressable by their term keys too, and are deliberately not in that registry — a grader belongs to one campaign, not to the process. Names are stable and implementations may change — read the registry, not a list here.
- **Matchers:** `MATCHERS` (`application/scoring/formula/matchers.py`), each one's `score` splatted into the same namespace, so a formula calls one exactly like an evaluator. This is the LABEL arm — it reads answer prose and decides HIT/MISS (`label_match`, `gsm8k_match`, …), which is why a `per_sample` formula almost always names one. Read the map; it holds what a campaign can actually reach, and nothing is kept in it for a caller that does not exist.

Constants, name lookups, arithmetic operators (`+ - * / % **`) addressable. **Calls outside the registry are rejected at compile time** (enforced, not convention).

One stable signal every measurement carries: **`fitness`** — continuous, formula-driven, `[0,1]`, written only by `Scorer.grade`. It feeds the optimizer, SampleIndex and every cohort read; a surface needing the discrete word derives it (`domain/scoring.py::is_hit`).

---

## 3. Dataset config schema

The layout (`dataset.yaml` with `source:` / `pipeline:` / `campaign:` / `panel:`, `task.md`, `origin.yaml`, `README.md`) and the definition chain it is laid over are owned by [`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md). What v1 promises:

- **`pipeline`** is the wire shape of [`node-standard.md`](node-standard.md) plus an operator overlay; `available_models` is the check-in's model MENU and the fallback bound on `model` for a node declaring no `optimizer.param_allowed_values.model` — that per-node list is the PERMITTED set.
- **`campaign`** is `application/campaign_config.py::CampaignConfig` (`extra="forbid"`: an unknown key raises at boot). Its `optimization` knobs are a mechanism, not a key/default table: each is an `Annotated[T, Knob(scope, *estimands)]` field with a description — the bench's on `OptimizationConfig`, an optimizer's on its member's knob model — walked by `application/knobs.py`; read defaults off the fields and the manifest. An optimizer node's provider / model / temperature / `reasoning_effort` / `max_tokens` is its manifest's per-node config, moved by `optimization.nodes.{node}.config`.
- **`judges`** maps a scoring term to a registered LLM-as-judge (`{term: {name, stages: [{role, model, provider, temperature}]}}`), banked as a per-sample observation the formula reads by term KEY; **its models are inherited from nothing**. Third-party judges ride the `promptpotter.judges` entry-point group — [`judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md).
- **`origin.yaml`** fields must be ones the node declares (`application/datasets/prompts.py::declared_origin_prompts` refuses the rest); **`task.md`** front matter is the committed framing (`application/bench/task_context.py::commit_task_framing`).

---

## 4. DispatchHub injection keys

The contract is the mechanism, not the slot list: `dispatch/injections/registry.py::injection_table()`, per-slot detail in [`dispatch-hub.md`](dispatch-hub.md) § Reference.

## 4b. Roots — where the package reads and writes

Four roots, owned by `promptpotter/config/paths.py`. A fork may rely on the resolution
rules; the constants themselves are internal.

| Root | Resolves to | Contents |
|---|---|---|
| **Install content** | `promptpotter/assets/` inside the package | The optimizer's own `pipeline.yaml` + `resolved_schemas.json` + `sets/{name}.yaml`, and the exported dashboard. Ships in the wheel; ours, not the operator's. |
| **User data** | `$PROMPTPOTTER_HOME` → the checkout's `.promptpotter/` when running from a source tree → the OS app-data dir | Campaigns, sessions, measurements, jobs, identity. |
| **Configuration** | `$PROMPTPOTTER_ENV_FILE` → the checkout's `.env` when running from a source tree → the user-data root's `.env` | Keys and postures (`PROMPTPOTTER_AUTH`). A process variable outranks the file. The gate names an absent file, so no verdict reads the desk's configuration. |
| **Templates** | `promptpotter/assets/datasets/` (`config/paths.py::install_datasets_root`) | Template dataset **definitions**, read-only in checkout and wheel alike. Anything DERIVED from one lands in the user-data root, never beside the definition ([`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § Dataset content has two tiers). A tenant dataset of the same name shadows an installed one. |

**`PROMPTPOTTER_HOME` and `PROMPTPOTTER_ENV_FILE` are stable.** Set the first to relocate the
whole user-data tree and the second to read another configuration file; each is read once at
import, so it is an environment decision, not a runtime one.

**`$PROMPTPOTTER_HOME/optimizers/{name}/pipeline.yaml` and `$PROMPTPOTTER_HOME/checkin/pipeline.yaml`
are stable, and they are the install assets an operator may shadow.** Present, one replaces the
packaged manifest of that name (provider / model / temperature per node); absent, the packaged one
is read. The generated `resolved_schemas.json` beside each is deliberately not overridable, so the
seam is a file, never a directory.

**`$PROMPTPOTTER_HOME/block_packs/{name}/pack.json` is stable** (`config/paths.py::block_packs_root`):
a block pack the `block_pool` node's `packs` knob may name. None ships; a pack is installed by
placing or linking its directory there, so where its content is kept is the operator's.

The derived `assets/webapp/` tree is staged by
`scripts/build_release.py`, the supported way to build a wheel — a bare `uv build` produces
one that quietly serves no dashboard. There is no `REPO_ROOT`, and
nothing resolves to `site-packages/`: `pip` deletes there on upgrade.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the loop-mint verbs; lifecycle, run-control, diagnostic and maintenance verbs exist beside them. **The flag set is `presentation/cli/commands/verbs.py`** and what each does to the tree is [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)'s — a table here is one `--help` away from its source and has drifted from it before. What v1 promises is that the two verbs, and the flags that file declares for them, keep their meanings.

Two behaviours a fork may rely on, neither of them readable off `--help`:

- Every `new` mints a fresh `campaign_id`, but two `new` calls on an unchanged declaration SHARE their content-addressed root `cycle_id` and its origin score, then diverge from round 1 (`runner/campaign_ids.py::mint_campaign_id`). The prior campaign is preserved.
- A launch flag SETS an axis of the cycle's budget, raise or lower, over what the dataset declares, and stays on the cycle's standing budget for later resumes; the account admits the result whole or refuses the launch. `set-limits` moves it mid-flight.

The maintenance and diagnostic verbs are not part of v1.

## 5b. Embedded launch entry (Python)

The programmatic peer of §5's verbs, for a host driving one campaign inside its own event loop:
`application/embedded_run.py::open_session`, `::run_campaign`, `::submit_gate_decision`,
`::submit_arms`, `::compact_archive` (the `compact-archive` verb, dispatched, refused in the REST
422's words) and `::reindex_measurement_archive` — read their signatures there. What a host may
rely on and cannot read off a signature:

- **Two steps because every caller works between them.** The mint is the same `mint_framed_cycle`
  prologue `new` and the web mint run, and the origin is scored inside `run_optimization`, so the
  cycle is resumable, forkable and diagnosable by the §5 verbs — what this seam buys over a private
  loop. The session `run_campaign` answers is bound to the cycle the run ended on, a fork included.
- **`campaign_config` is what a MINT declares** — required on a fresh session, refused on one bound
  to a cycle (a resume runs its campaign's declaration).
- **A run measures the session's bank and nothing else**; `rows` hands the host's samples in as it.
  `program` rides the backend client as `InProcessWorkload.program` (§1).
- **`budget`** is `domain/spend.py::Budget`, the model the CLI flags and the `start-run` payload
  build, admitted through the one admission every launch takes; `mode` is `domain/run_mode.py::RunMode`.
- **`origin_gate` defaults to `strict` and a host has no TTY**, so `run_campaign` blocks at round 0
  until `submit_gate_decision` answers from another task once the cycle holds at its gate (one sent
  earlier is refused), or the knob is set off. Under `arm_gate` `review` / `author` every round
  blocks until `submit_arms` answers.
- The readout lands in the cycle's `readout.log` whatever the host passes; `readout_sink=print`
  shows it as it is written.

Nothing on this path imports a server, and the dependency list says so: `pip install
promptpotter` is the engine, `[api]` is what a host adds if it also wants to serve the API and
the dashboard.

`with_optimization(load_dataset_campaign_config(path), {…})`
(`application/datasets/authored.py`) is the
supported way to shape a dataset's `{dataset}/dataset.yaml::campaign` for one launch without editing the shared file:
a sparse `optimization` mapping merged depth-first **before** validation, so an unknown knob raises
here instead of being silently dropped — the same call a web mint, a check-in and the terminal make.

## 5c. The export artifact

Everything else this package writes answers *how the run went*; `cycles/{id}/export.json` answers
*what it found, and how good it is* — for a reader that will never open the campaign tree. Written
from the same call that stamps `index.json::final`, so both are projections of one `CycleResult`.

```python
from promptpotter.domain.export import parse_prompt_export
export = parse_prompt_export(Path("…/export.json").read_text())
fields = export.prompts["solve"]      # per prompt node, its fields by name, as scored
prompt = export.render("solve")       # that node's prompt as scored: those fields, then its shots
export.measurement.own.composite      # under export.measurement.formula, never a bare number
```

Four rules hold it, each a defect of DSPy's own `save()` inverted (`domain/export.py` argues them):
**fields by name** (their `load_state` zips positionally with `strict=False`, so a signature that
gained a field reloads scrambled and raises nothing) · **provenance inside the file** — the fitness
under its named formula as the winner's `own` level with its n, the paired lift + CI over the
parent (`vs_reference`) and over the origin on the origin panel (`vs_origin`), the held-out lift as `bench.vs_origin`, θ, the rows' hash, the
optimizer manifest, which is the half we compute and they cannot · **an `artifact_version` a reader
refuses on**, since we owe no back-compat · **JSON and scalars, never pickle**. `tuned_params`
carries the node config the winner ran under, minus each node's rendered prompt — that is
`prompt_fields` again, and one artifact does not state a fact twice.

`lineage` is how the winner was reached: one entity per candidate, the origin stating its loci
whole and every other one only the `changes` over its parent, joined by PROV-O's `wasDerivedFrom`,
`wasGeneratedBy` (its round, the members that proposed it); every round measured on the file's one
`dataset_hash`. The file's
`@context` names those keys, so it is JSON-LD with no library. Write it with `export.to_json()`;
the `export` verb and route serve it, and its `package` tier wraps it as an Agent Skill.

That node config names the model the winner was won on, and a reader must treat it as binding: an
optimized prompt does not carry across models, so a model swap is a new run, never a copy of the
winner ([`../research/external-constraints.md`](../research/external-constraints.md) § Direction — where optimization pays, and where it does not).

Absent when no round ever closed: an artifact whose point is a fitness with provenance may not
carry an unmeasured one.

## 6. Ledger event types

Typed records on the per-cycle ledger (`.runtime/ledger.jsonl` — the
workspace-scoped sibling at `.workspace/events.jsonl` carries workspace
lifecycle, not cycle records). The record family — `PhaseRecord`,
the arm-walk records (`SampleScoredRecord`, `ArmRecord`, …), `ResumeCheckpointRecord`, `TokenUsageRecord`,
`LLMCallStartRecord`/`LLMCallRecord` — is the discriminated union in
`domain/run_records.py`; each record's fields are its dataclass, read
them there.

Forks within a family share one event stream via `CycleEventLog.inherit_from(parent, offset)`. The forked cycle's ledger FILE holds only its own appends; `iter()` walks the parent's records up to `offset` in front of them, and the parent carries a `ResumeCheckpointRecord` of kind `FORK_CUT`. The cut address is `index.json::forked_at_offset`, so that walk is reproducible off disk — but it yields a VIRTUAL position (parent + own) that is not the `sequence` a tail reports, which is the file's own line. Address a record as `(path, offset)`, never by an integer alone.

### The cut

A family is a partial order over **cuts**, with two operations: **iterate** by time (the ray, forks interleaved with their parents) and **reduce** by causality (a **fold** — the inherited prefix, then the cycle's own records). A cut is `(cycle, offset)` (`domain/cycle_paths.py::Cut`); `offset` is the 0-based line in that cycle's **own** ledger — the one space `ProjectionEnvelope.sequence`, `StepFacts.offset` and every artifact's `at_offset` share — and the inherited prefix rides `iter(own_limit=…)`, outside it.

- **A cut names a `CycleHop`, not a `CyclePath`** — descent is spent before anything folds; `CyclePath` is the WIRE address.
- **Every artifact stamps the cut it is of**, so the ledger is the truth and each file a cache: `?at=<offset>` on the dashboard route re-folds any past moment, and `read_model.py::Moment` replays every ledger of the family to one instant.

Subscribers read via `Projection.on_record(record)` and MUST NOT write any campaign artifact beyond their declared allowlist (fails loud; see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## 7. Per-cycle artifact paths

**What each file holds, and who writes it** — owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § File reference. What v1 promises is narrower and is only stated here: inside `campaigns/{campaign_id}/cycles/{cycle_id}/`, the contract for any tool reading per-cycle results is **`rounds/round_NNNN.json` + `index.json` + `log.md`**, and `export.json` (§5c) for the winner alone. Everything under `.runtime/` may change shape between minor versions, ledger records included — §6 promises the record family, not the file layout around it.

Sibling cycles (forks, diag) live flat under `cycles/` alongside the root, each carrying its own per-cycle artifacts including its own `dashboard.json`, which a fork seeds from its parent at the cut.

## 8. What is NOT stable

- **Internal module structure, private (`_`-prefixed) names, and runtime dataclass shapes** beyond §1–§7. Package `__init__` files re-export nothing, and `__all__` is declared only where a tool reads it — this document is the public surface.
- **In-memory caches** and their invalidation.
- **Prompt templates** at `promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **The optimizer node types** (`application/optimizers/nodes.py`) and the `NodeContext` a member is handed — its roster of readings moves with the bench.
- **Test helpers** and **the `webapp/` layout**.
- **The REST API and the events stream**, specified though they are (`docs/specs/openapi.generated.json`, `events-asyncapi.generated.json`). Identity is a browser SESSION COOKIE (`presentation/api/middleware/oidc.py`); no route takes an API key, and the one inbound bearer token (`presentation/api/deps.py::require_site_records_token`, `SITE_RECORDS_TOKEN`) opens only `/site-records`, outside every tenant. So a third party reaches the campaign surface only with `PROMPTPOTTER_AUTH=off` — a same-origin browser surface plus a local no-auth mode, not an integration surface.
