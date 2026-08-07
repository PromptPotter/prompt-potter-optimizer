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
    execution: ConnectorExecution = "remote_http"                   # "remote_http" | "in_process" (L4 inner cycle)
    in_process_run: InProcessRun | None = None                      # async (query, payload) -> {"data": …}; required iff in_process
    expected_revision: str | None = None                            # backend rev this PP rev expects (paired w/ version_check)
    version_check: VersionCheck | None = None                       # async (http, base_url) -> str | None; init WARNs on drift
    preflight: PreflightFn | None = None                            # async (backend_url) -> None reachability probe; None opts out
    auth_token: AuthTokenFn | None = None                           # () -> str | None bearer for THIS backend; unset when in_process
```

`SessionProtocol` (`promptpotter/domain/connector.py`): `async set_terms(http, base_url, terms)` (backend handshake; noop ok) · `async recover(http, base_url)` (re-establish after transport error).

**Registering one, from your own package — no fork.** `promptpotter.connectors` is a published entry-point group:

```toml
[project.entry-points."promptpotter.connectors"]
anything = "my_package.connector:CONNECTOR"
```

The object named must be a `Connector`; **its `name` field is the registry key**, so the entry-point label is free and a package cannot claim a key its connector does not declare. No edits to `application/campaign_config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

Three rules a plugin is held to, all enforced at import in `connectors/__init__.py`:

- **Same validation as a built-in.** One `_validate` runs over ours and yours alike — key/`name` agreement, the three hooks callable, `execution` a declared mode, `in_process` paired with `in_process_run` and carrying no `auth_token`.
- **No shadowing.** A plugin may not register `termnorm` or `promptpotter`; those keys are read by name inside the loop.
- **A plugin that cannot load is fatal, not skipped** — the error names the distribution. A skipped one would return later as an unexplained `connector 'x' not registered`.

`CONNECTOR_ORIGINS` maps every registered name to `"built-in"` or `"<distribution>: <module>:<attr>"` (the entry point's *value*, not its label — the label is free, the value is what was imported), so a name that greps to nothing in this tree can still be traced to its package. Audit what is loaded with:

```bash
python -c "from promptpotter.connectors import CONNECTOR_ORIGINS as o; print(*o.items(), sep='\n')"
```

⚠️ **A connector is trusted code, not sandboxed.** Loading one imports its module into the PromptPotter process, where it sees the provider API keys, the tenant tree and the identity store — the same access any module we ship has. The capability scoping in [ADR-0005](../adr/0005-delegated-principals-and-capability-scoping.md) governs **API principals, not in-process code**, and nothing here changes that. Entry points do not lower the bar (anything able to install a distribution into your environment can already execute code in it), but they make the decision explicit: **installing a connector package is trusting its publisher completely.**

Adding one *to this repo* is still one new file under `promptpotter/connectors/` plus a `_BUILTIN` entry. Built-ins are deliberately **not** declared as entry points: reading them from install metadata would make a source-tree run with no metadata find zero backends.

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug · `extract_experiment` MUST return `(queries, index_terms)` (the latter may be empty).

---

## 2. Scoring formula DSL

Configured per dataset via `campaign.yaml::scoring`:

```jsonc
{
  "scoring": {
    "per_sample": "acc",            // required: scorer expression
    "per_round": "median(scores)",  // optional: round-level aggregator
    "scorer_id": "acc_v1"           // optional: explicit id
  }
}
```

**Addressable namespace** (`application/scoring/formula/compiler.py`):

- **Builtins:** the `_SAFE_BUILTINS` map in that module — arithmetic and `math` only. Nothing else from `__builtins__`.
- **Evaluators:** any registered name, per-sample or per-round. `all_evaluators()` (`application/scoring/evaluators.py`) is the registry, `evaluators_meta()` its served projection. Names are stable and implementations may change — read the registry, not a list here.

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
- `nodes` — node graph. Per-node: `runtime` (`backend`/`frontend`/`in_process`) · `node_type` (`candidate_source`/`ranker`/`enricher`/`cache`/`""`) · `optimizer.param_keys` (list — operator-tunable knobs) · `optimizer.observation_mappings` (wire-name → optimizer-name) · `optimizer.langfuse_type` · `config` (per-dataset overlay merged onto the wire payload).
- `pipelines` — named pipeline variants.
- `available_models` — model menu shown to L1.
- `resolved_prompts` — prompt-template map keyed by version. (`resolved_schemas` is a
  sibling file, not a key: for `_optimizer` it is generated into
  `resolved_schemas.json` by `scripts/build_optimizer_schemas.py`.)

### `campaign.yaml`

Campaign knobs + scoring + optimizer LLM. Validated by `application/campaign_config.py::CampaignConfig` with `extra="forbid"` — unknown keys raise at boot. See `CampaignConfig` for the full field list.

**Top-level keys.** `dataset_name`, `scoring`, `sp_budget_ttest`, `exclude_nodes` (drop pipeline nodes by name), `pipeline_overrides` (per-node config overlay), `optimization`. (The optimizer LLM is install-global — `promptpotter/assets/optimizer/pipeline.yaml` — not a campaign key.)

**`optimization` knobs:** the stable contract is the mechanism, not a
frozen key/default table (same rule as §4). Every knob is a
self-describing field on `OptimizationConfig` in
`application/campaign_config.py` — `Annotated[T, Knob(scope, *estimands)]` plus a
`Field(description=…)` — and `application/knobs.py::KNOBS` is the walked
taxonomy. Only `improvement_threshold` and `degradation_threshold` are
required; everything else defaults. Read defaults off the fields, never
off a doc.

**Optimizer LLM:** install-global, **not** in `campaign.yaml`. Provider, model, temperature, `reasoning_effort`, and `max_tokens` are per-node config in `promptpotter/assets/optimizer/pipeline.yaml` (`nodes.{l1_generate|l1_critique|l2_context|l3_plan|checkin}.config`), resolved inside `llm_call` like any other node tunable. One file configures the optimizer for every campaign.

Constants moved out of `campaign.yaml` (they live next to their consumer): L1 candidate-generation temperature (the `creativity` arg in `l1/generate.py`, driven by `l1_overrides.creativity`, defaulting to the `l1_generate` node temperature), L2/L3 transition temperatures (the `l2_context`/`l3_plan` node temperatures), runaway-loop ceiling (`runner/loop.py::HARD_CAP`), stale-data recovery ladder (`scoring/sample_measurement.py`). PoBB lock-in went the other way and stayed campaign config — `pobb_lock_in` / `pobb_lock_in_n_min` / `mechanisms.elimination.leader_lock_in`.

The yield-drought escalation rule (`l2_axis_yield_drought`) is permanent — no opt-in flag. L2 and L3 are always-on architecture.

### Other files

- **`prompts/{node}.yaml`** — 8-field `PromptTemplate` per node. Schema: `domain/opt_search_point.py::PromptTemplate`. Loaded by `application/datasets/prompts.py::load_node_prompt`.
- **`task_description.md`** — free-form markdown; decomposed at `init` into the `task_context` dict on `OptSearchPoint`.
- **`dataset.md`** — operator guide; free-form, not parsed.
- **`task_context.yaml`** — the committed task framing; written once by the `checkin` decomposition (or by web ingest at commit) and read free on every later run. See `application/optimization/task_context.py::load_or_build_task_context`.

---

## 4. DispatchHub INJECTIONS keys

`{{slot}}` names available in any optimizer prompt. Assembled into `dispatch/injections/registry.py::INJECTIONS` from the `@signal("<slot>", …)` decorator on each renderer (`injections/{panels,layer_state,catalogues,wounds}.py`). Adding a slot is one decorated renderer — key and body co-located. Using a slot not in the dict is a load-time `KeyError` via `validate_template`.

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
`scripts/build_release.py`, which is the supported way to build a wheel. A bare `uv build`
produces one that quietly serves no dashboard and resolves no dataset.

Running from a checkout (development, and `deploy-linux/`) resolves exactly the paths it
always has. There is no `REPO_ROOT`: the parent walk that once stood for all three roots
resolved to `site-packages/` when installed, which is both where `pip` deletes on upgrade
and where the HuggingFace `datasets` library lives.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the loop-mint verbs (lifecycle + diagnostic verbs also exist — see CLI reference). Stable flag set:

| Verb | Flag | Meaning |
|---|---|---|
| `new` | `<name>` (positional) | Mint a fresh session+cycle from `datasets/<name>/`. |
| `new` | `--config <path>` | Override the dataset's default `campaign.yaml`. |
| `new` | `--dataset-name <name>` | Alternative to the positional `<name>`. |
| `new` | `--sweep-batch` | Sweep mode: round 1 scored, round 2 generation-only. Mints siblings under `sweeps/`. |
| `new` | `--diag` | Diag mode: round 1 scored, force L2 on round-1 evidence, round 2 generation-only. |
| `new` / `resume` | `--halt-at <float>` | Halt with `TARGET_HIT` once `best_accuracy ≥ X`. |
| `new` / `resume` | `--spend-budget <float>` | Halt with `SPEND_BUDGET` once cycle spend ≥ X. |
| `resume` | `--from <N>` | Resume rewind: archive rounds > N and resume from round N+1. |
| `resume` | `--no-check` | Skip the rescore-and-replay divergence check at boot. |
| `resume` | `--fork-on-divergence` | On divergence, mint a sibling cycle rooted at the divergence point. |
| `resume` | `--diag` | Diag on the active cycle. |

**Behavior note:** every `new` invocation mints a fresh root cycle; on content-hash collision with an existing root, the `cycle_id` gets a `_r2` / `_r3` discriminator suffix so the new run lands in its own directory tree (separate dashboard, log, archive subtree). The prior campaign is preserved.

**Mutual exclusions:** `--sweep-batch` and `--diag` mutually exclusive on `new`.

The maintenance and diagnostic verbs have their own flag sets — see `presentation/cli/parsers.py`. Not part of v1 (M11 still touches them). There is no `sweep` verb: a sweep is `new --sweep-batch`, and `--sweep-batch` with no `sweep/*.yaml` payloads is a setup error, not a fall-through to a single unpaired cycle.

## 6. Ledger event types

Typed records on the per-cycle ledger (`.runtime/ledger.jsonl` — the
workspace-scoped sibling at `.workspace/events.jsonl` carries workspace
lifecycle, not cycle records). The record family — `PhaseRecord`,
`SnapshotRecord`, `ResumeCheckpointRecord`, `TokenUsageRecord`,
`LLMCallStartRecord`/`LLMCallRecord` — is the discriminated union in
`domain/run_records.py`; each record's fields are its dataclass, read
them there.

Forks within a family share one event stream via `CycleEventLog.inherit_from(parent_offset)`. The forked cycle's ledger starts with the parent's records up to `parent_offset` plus a `ResumeCheckpointRecord` of kind `FORK_CUT`.

Subscribers read via `DerivedView.on_record(record)` and MUST NOT write any campaign artifact beyond their declared allowlist (fails loud; see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## 7. Per-cycle artifact paths

Operator-visible files inside `campaigns/{campaign_id}/cycles/{cycle_id}/`. Webapp + downstream tooling read these directly — they are contract.

| Path | Writer | Description |
|---|---|---|
| `index.json` | `CampaignStore.create` / `update` | Per-cycle summary: header (dataset, lineage), rounds[], best. |
| `log.md` | `application/output.py::write_log_md` | Markdown digest of every closed round + forks + hard samples + final winner. |
| `review.md` | `application/output.py::write_review_md` | Per-round behavior-check + L1Stats narrative. |
| `rounds/round_NNNN.json` | `CampaignStore.save_round_file` | Full per-round detail: candidate scores, evaluators, prompt_fields, pipeline_params, OSP snapshot, decisions. |
| `dashboard.json` (per cycle) | `LiveDashboardView._persist` | Live operator view; rewritten on every record. Refresh: 2 s. |
| `langfuse/*.json` | `infrastructure/tracing/langfuse_sink.py` | Per-cycle Langfuse export snapshots. |
| `prompts/{node}.yaml` | `infrastructure/tracing/file_sink.py` | Resolved prompt templates for this cycle's runs. |
| `.runtime/ledger.jsonl` | `CycleEventLog.append` | The sole-ingress event log. Internal-but-stable shape (see §6). |
| `.runtime/cache/rounds/round_NNNN.json` | `AuditTrailView.flush` | Per-round audit cache (writer-buffered until round close). |
| `.runtime/cache/candidates/round_NNNN.json` | `CampaignStore.save_round_candidates` | Mid-round candidates checkpoint (deleted after L1 score on escalation). |
| `.runtime/streams/round_NNNN_p_best.jsonl` | `PoBBStreamView._handle_snapshot` | Per-sample P(best) trajectory. |
| `.runtime/pause.flag` | `api/middleware/command_dispatcher.py` (`POST /commands/{kind}`, kind=`pause-cycle`) | Single operator-interrupt signal; consumed by `session.pause_check`. Worker exits clean, cycle stays resumable (no separate `stop.flag`). |

Sibling cycles (forks, diag, sweeps) live flat under `cycles/` alongside the root. Each carries its own per-cycle artifacts, including its own `dashboard.json` (a fork's is seeded from its parent at the cut). `.runtime/` shapes may change between minor versions — the public `rounds/round_NNNN.json` tree + `index.json` + `log.md` are the contract for any tool reading per-cycle results.

## 8. What is NOT stable

- **Internal module structure** beyond §1–§7. The dispatch hub split into `hub/{bundle, injections, facade}` is internal — only the public symbols (`DispatchHub`, `INJECTIONS`, `build_bundle`, `validate_template`) are stable.
- **Private types** (`_Injection`, `_TEMPLATE_EXTRAS`, etc., plus any `_`-prefixed name). Package `__init__` files are namespace markers that re-export nothing — §1–§7 is the whole public surface, not whatever a package surfaces.
- **`__all__`** — this document is the public surface; `__all__` is a reader's hint and nothing more. It is mechanically inert here (`implicit_reexport = true`, no `import *` anywhere), so neither runtime nor mypy consults it, and a name listed there is not thereby promised. Prune an entry nothing imports rather than reading it as a contract.
- **Runtime dataclass shapes** not in §1–§7 (`CycleSlice`, `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc.).
- **In-memory caches** and their invalidation strategies (optimizer LRU caches, the dispatch hub's pipeline-param-catalogue cache, etc.).
- **Prompt templates** at `promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **Test helpers** (`tests/factories.py`, `tests/conftest.py`).
- **The `webapp/` layout.** The webapp + control plane ship and serve users; internal component layout stays free to move.
