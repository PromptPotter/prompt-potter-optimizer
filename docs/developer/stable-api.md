# Stable API surface — what forks can rely on

> **Stable API v1** · Last reviewed: 2026-06-11

What downstream forks build on without breaking on the next refactor. Anything not listed is **internal** — free to rename, restructure, or delete in any PR. Forks on internal symbols are on their own. Breaking changes here bump the major; pre-release the version is informational only. Non-promises spelled out in §8.

## 1. Connector protocol

Frozen dataclass at `promptpotter/connectors/protocol.py::Connector`:

```python
@dataclass(frozen=True)
class Connector:
    name: str                                                       # lowercase id; matches pipeline.json::backend_type
    wire_adapter: Callable[[str, dict | None], dict]                # outbound HTTP body shaper
    session_factory: Callable[[], SessionProtocol]                  # fresh session per BackendClient
    extract_experiment: Callable[[dict], tuple[list[dict], list[str]]]  # → (queries, index_terms)
    execution: ConnectorExecution = "remote_http"                   # "remote_http" | "in_process" (L4 inner cycle)
    expected_revision: str | None = None                            # backend rev this PP rev expects (paired w/ version_check)
    version_check: VersionCheck | None = None                       # async (http, base_url) -> str | None; bootstrap WARNs on drift
    preflight: PreflightFn | None = None                            # async (backend_url) -> None reachability probe; None opts out
```

`SessionProtocol` (`promptpotter/domain/connector.py`): `async set_terms(http, base_url, terms)` (backend handshake; noop ok) · `async recover(http, base_url)` (re-establish after transport error).

Each connector self-registers at import via `promptpotter/connectors/__init__.py::CONNECTORS`. Adding a connector is one new file under `promptpotter/connectors/` — no edits to `application/config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug · `extract_experiment` MUST return `(queries, index_terms)` (the latter may be empty).

---

## 2. Scoring formula DSL

Configured per dataset via `campaign.json::scoring`:

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

- **Builtins:** `min`, `max`, `sum`, `mean` (arithmetic — `statistics.fmean`). Nothing else from `__builtins__`.
- **Per-sample evaluators:** any registered name. Today: `acc` (rank-1 exact match), `gt_in_ranked_items`, `gt_in_source`, `composite` (multi-axis blend). Names stable; implementation may change.
- **Per-round aggregators:** `mean`, `median`, `min`, `max`, `count`, plus per-sample evaluator names lifted to round level.

Constants, name lookups, arithmetic operators (`+ - * / % **`) addressable. **Calls outside the registry are rejected at compile time** (enforced, not convention).

Two stable signals every measurement carries: **`hit`** (boolean, rank-1 exact — feeds SampleIndex / cohort analysis) · **`score`** (continuous, formula-driven — feeds the optimizer).

---

## 3. Dataset config schema

Each dataset lives at `datasets/{name}/` with:

### `pipeline.json`

Connector-described pipeline (the shape `GET /pipeline` exposes, plus an operator overlay). Required top-level keys:

- `name`, `version` — pipeline identity.
- `backend_type` — connector name; must match a registered connector.
- `backend_name` — display name for operator surfaces.
- `nodes` — node graph. Per-node: `runtime` (`python`/`llm`/`cache`/`network`) · `short_circuit` (bool) · `node_type` (`candidate_source`/`ranker`/`enricher`/`cache`/`""`) · `optimizer.param_keys` (list — operator-tunable knobs) · `optimizer.observation_mappings` (wire-name → optimizer-name) · `optimizer.langfuse_type` · `config` (per-dataset overlay merged onto the wire payload).
- `pipelines` — named pipeline variants.
- `available_models` — model menu shown to L1.
- `resolved_schemas`, `resolved_prompts` — JSON-Schema and prompt-template maps keyed by version.

### `campaign.json`

Campaign knobs + scoring + optimizer LLM. Validated by `application/config.py::CampaignConfig` with `extra="forbid"` — unknown keys raise at boot. See `CampaignConfig` for the full field list.

**Top-level keys.** `dataset_name`, `scoring`, `sp_budget_ttest`, `exclude_nodes` (drop pipeline nodes by name), `pipeline_overrides` (per-node config overlay), `optimization`. (The optimizer LLM is install-global — `datasets/_optimizer/pipeline.json` — not a campaign key.)

**`optimization` knobs:** the stable contract is the mechanism, not a
frozen key/default table (same rule as §4). Every knob is a
self-describing field on `OptimizationConfig` in
`application/config.py` — `Annotated[T, Knob(scope, *estimands)]` plus a
`Field(description=…)` — and `application/knobs.py::KNOBS` is the walked
taxonomy. Only `improvement_threshold` and `degradation_threshold` are
required; everything else defaults. Read defaults off the fields, never
off a doc.

**Optimizer LLM:** install-global, **not** in `campaign.json`. Provider, model, temperature, `reasoning_effort`, and `max_tokens` are per-node config in `datasets/_optimizer/pipeline.json` (`nodes.{l1_generate|l1_critique|l2_context|l3_plan|checkin}.config`), resolved inside `llm_call` like any other node tunable. One file configures the optimizer for every campaign.

Constants moved out of `campaign.json` (they live next to their consumer): L1 candidate-generation temperature (the `creativity` arg in `l1/generate.py`, driven by `l1_overrides.creativity`, defaulting to the `l1_generate` node temperature), L2/L3 transition temperatures (the `l2_context`/`l3_plan` node temperatures), PoBB lock-in (`l1/execute.py::POBB_LOCK_IN`), runaway-loop ceiling (`runner/loop.py::HARD_CAP`), stale-data recovery ladder (`scoring/sample_measurement.py`).

The yield-drought escalation rule (`l2_axis_yield_drought`) is permanent — no opt-in flag. L2 and L3 are always-on architecture.

### Other files

- **`prompts/{node}.json`** — 8-field `PromptTemplate` JSON per node. Schema: `domain/opt_search_point.py::PromptTemplate`. Loaded by `application/datasets/prompts.py::load_node_prompt`.
- **`task_description.md`** — free-form markdown; decomposed at `init` into the `task_context` dict on `OptSearchPoint`.
- **`dataset.md`** — operator guide; free-form, not parsed.
- **`recon_variants.json`** *(optional)* — per-dataset axis-mutation library: the pre-computed L1 sweep variants a recon run reads.
- **`task_context.json`** — the committed task framing; written once by the `checkin` decomposition (or by web ingest at commit) and read free on every later run. See `application/optimization/task_context.py::load_or_build_task_context`.

---

## 4. DispatchHub INJECTIONS keys

`{{slot}}` names available in any optimizer prompt. Assembled into `dispatch/hub/injections/registry.py::INJECTIONS` from the `@signal("<slot>", …)` decorator on each renderer (`injections/{panels,layer_state,catalogues,wounds}.py`). Adding a slot is one decorated renderer — key and body co-located. Using a slot not in the dict is a load-time `KeyError` via `validate_template`.

**The stable contract is the mechanism, not the slot list** — the set evolves (22 today; e.g. the four old per-wound slots merged into `l1_wounds` + `guard_breaches`), so this page doesn't freeze a table that drifts. The live set is the registry itself; the doc-level reference with per-slot detail is [`dispatch-hub.md`](dispatch-hub.md) § Reference.

**Per-template extras** (caller-supplied via `compile_prompt(**hub_dict, **extras)`): `l1_generate` → `{n_variants}` · `l1_critique`/`l2_context`/`l3_plan` → `{}` · `checkin` → `{consultation_instruction}`.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the loop-mint verbs (lifecycle + diagnostic verbs also exist — see CLI reference). Stable flag set:

| Verb | Flag | Meaning |
|---|---|---|
| `new` | `<name>` (positional) | Mint a fresh session+cycle from `datasets/<name>/`. |
| `new` | `--config <path>` | Override the dataset's default `campaign.json`. |
| `new` | `--dataset-name <name>` | Alternative to the positional `<name>`. |
| `new` | `--sweep-batch` | Sweep mode: round 1 scored, round 2 generation-only. Mints siblings under `sweeps/`. |
| `new` | `--diag` | Diag mode: round 1 scored, force L2 on round-1 evidence, round 2 generation-only. |
| `new` / `resume` | `--halt-at-accuracy <float>` | Halt with `TARGET_HIT` once `best_accuracy ≥ X`. |
| `new` / `resume` | `--max-spend-usd <float>` | Halt with `MAX_SPEND` once cycle spend ≥ X. |
| `resume` | `--from <N>` | Resume rewind: archive rounds > N and resume from round N+1. |
| `resume` | `--no-check` | Skip the rescore-and-replay divergence check at boot. |
| `resume` | `--fork-on-divergence` | On divergence, mint a sibling cycle rooted at the divergence point. |
| `resume` | `--diag` | Diag on the active cycle. |

**Behavior note:** every `new` invocation mints a fresh root cycle; on content-hash collision with an existing root, the `cycle_id` gets a `_r2` / `_r3` discriminator suffix so the new run lands in its own directory tree (separate dashboard, log, archive subtree). The prior campaign is preserved.

**Mutual exclusions:** `--sweep-batch` and `--diag` mutually exclusive on `new`.

Other subcommands (`sweep`, plus the maintenance verbs) have their own flag sets — see `presentation/cli/parsers.py`. Not part of v1 (M11 still touches them).

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
| `log.md` | `application/output/writers.py::write_log_md` | Markdown digest of every closed round + forks + hard samples + final winner. |
| `review.md` | `application/output/writers.py::write_review_md` | Per-round behavior-check + L1Stats narrative. |
| `rounds/round_NNNN.json` | `CampaignStore.save_round_file` | Full per-round detail: candidate scores, evaluators, prompt_fields, pipeline_params, OSP snapshot, decisions. |
| `dashboard.json` (per cycle) | `LiveDashboardView._persist` | Live operator view; rewritten on every record. Refresh: 2 s. |
| `langfuse/*.json` | `infrastructure/tracing/langfuse_push.py` | Per-cycle Langfuse export snapshots. |
| `prompts/{node}.json` | `infrastructure/tracing/langfuse_push.py` (and CLI `init`) | Resolved prompt templates for this cycle's runs. |
| `.runtime/ledger.jsonl` | `CycleEventLog.append` | The sole-ingress event log. Internal-but-stable shape (see §6). |
| `.runtime/cache/rounds/round_NNNN.json` | `AuditTrailView.flush` | Per-round audit cache (writer-buffered until round close). |
| `.runtime/cache/candidates/round_NNNN.json` | `CampaignStore.save_round_candidates` | Mid-round candidates checkpoint (deleted after L1 score on escalation). |
| `.runtime/streams/round_NNNN_p_best.jsonl` | `PoBBStreamView._handle_snapshot` | Per-sample P(best) trajectory. |
| `.runtime/pause.flag` | `api/middleware/command_dispatcher.py` (`POST /commands/{kind}`, kind=`pause-cycle`) | Single operator-interrupt signal; consumed by `session.pause_check`. Worker exits clean, cycle stays resumable (no separate `stop.flag`). |

Sibling cycles (forks, diag, sweeps) live flat under `cycles/` alongside the root. Each carries its own per-cycle artifacts, including its own `dashboard.json` (a fork's is seeded from its parent at the cut). `.runtime/` shapes may change between minor versions — the public `rounds/round_NNNN.json` tree + `index.json` + `log.md` are the contract for any tool reading per-cycle results.

## 8. What is NOT stable

- **Internal module structure** beyond §1–§7. The dispatch hub split into `hub/{bundle, injections, facade}` is internal — only the public symbols (`DispatchHub`, `INJECTIONS`, `build_bundle`, `validate_template`) are stable.
- **Private types** (`_Injection`, `_TEMPLATE_EXTRAS`, etc., plus any `_`-prefixed name or dataclass not re-exported through its package `__init__`).
- **Runtime dataclass shapes** not in §1–§7 (`CycleSlice`, `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc.).
- **In-memory caches** and their invalidation strategies (optimizer LRU caches, the dispatch hub's pipeline-param-catalogue cache, etc.).
- **Prompt templates** at `datasets/_optimizer/pipeline.json::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **Test helpers** (`tests/_helpers.py`).
- **The `webapp/` layout.** The webapp + control plane ship and serve users; internal component layout stays free to move.
