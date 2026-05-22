# Stable API surface — what forks can rely on

> **Stable API v1** · Last reviewed: 2026-05-14

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
    resolve_ground_truth: Callable[[dict, str], str | None]
```

`SessionProtocol` (`promptpotter/domain/connector.py`): `async set_terms(terms)` (backend handshake; noop ok) · `async recover()` (re-establish after transport error).

Each connector self-registers at import via `promptpotter/connectors/__init__.py::CONNECTORS`. Adding a connector is one new file under `promptpotter/connectors/` — no edits to `application/config.py` or `infrastructure/backend.py`. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

**Contracts beyond `protocol.py`:** wire adapters MUST be pure `(query, pipeline_params) → dict` — no I/O, no logging above debug · `extract_experiment` MUST return `(queries, index_terms)` (the latter may be empty) · `resolve_ground_truth` MUST return `str | None`.

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
- `llm_defaults` — snapshot of `GET /pipeline` defaults. Informational; do not repurpose.
- `resolved_schemas`, `resolved_prompts` — JSON-Schema and prompt-template maps keyed by version.

### `campaign.json`

Campaign knobs + scoring + optimizer LLM. Validated by `application/config.py::CampaignConfig` with `extra="forbid"` — unknown keys raise at boot. See `CampaignConfig` for the full field list.

**Top-level keys.** `dataset_name`, `scoring`, `sp_budget_ttest`, `exclude_nodes` (drop pipeline nodes by name), `pipeline_overrides` (per-node config overlay), `optimization`, `optimizer_llm`.

**`optimization` knobs:**

| Key | Default | What it does |
|---|---|---|
| `improvement_threshold` | — *required* | Min accuracy delta a round must beat to count as improved. |
| `degradation_threshold` | — *required* | Mid-eval abort threshold (0 disables). |
| `max_rounds` | 10 | Cycle round budget (None = unlimited, up to the `HARD_CAP=100` floor). |
| `l1_patience` | 3 | Stalled-rounds before L2 fires. Set to 0 for "fire L2 every round" cadence. |
| `l2_patience` | 2 | L2 fires before L3 takes over. |
| `l3_patience` | 1 | L3 fires before stop. |
| `n_variants` | 5 | Candidates per round (L2 can override via `l1_overrides.n_variants`). |
| `elimination_n_min` | 6 | Minimum queries before PoBB elimination fires. |
| `pobb_epsilon` | 0.05 | Stop a candidate when P(best) < ε. |
| `improvement_significance` | 1.0 | Significance gate (disabled by default; <1.0 requires p < this). |
| `zero_signal_filter_enabled` | False | Round-boundary prune always-hit/always-miss samples from the dataset. |
| `forbidden_axes_strict` | True | Reject L1 candidates that mutate operator-fixed axes (`model`, `provider`). |
| `exploration.swap_out_delta_se` | 0.7 | Rasch SE threshold for the scoring-set swap-out. |
| `exploration.swap_in_kg_threshold` | 0.01 | Rasch KG threshold for swap-in. |
| `exploration.max_swaps_per_round` | 3 | Cap on scoring-set churn per round. |

**`optimizer_llm` knobs:** `provider` (`groq`/`openai`/`anthropic`/`openrouter`), `model` (provider-specific). Per-node temperature + max_tokens come from `datasets/_optimizer/pipeline.json`, not the campaign config.

Constants moved out of `campaign.json` (they live next to their consumer): L1 candidate-generation temperature (`l1/generate.py::L1_CREATIVITY`), L2/L3 transition temperatures (`LayerStrategy.default_temperature` in `escalation/firing/{l2,l3}_driver.py`), PoBB lock-in (`l1/execute.py::POBB_LOCK_IN`), runaway-loop ceiling (`runner/loop.py::HARD_CAP`), stale-data recovery ladder (`scoring/sample_measurement.py`).

The yield-drought escalation rule (`l2_axis_yield_drought`) is permanent — no opt-in flag. L2 and L3 are always-on architecture.

### Other files

- **`prompts/{node}.json`** — 8-field `PromptTemplate` JSON per node. Schema: `domain/opt_search_point.py::PromptTemplate`. Loaded by `application/datasets.py`.
- **`task_description.md`** — free-form markdown; decomposed at `init` into the `task_context` dict on `OptSearchPoint`.
- **`dataset.md`** — operator guide; free-form, not parsed.
- **`scan_variants.json`** *(optional)* — per-dataset axis-mutation library for sensitivity scans.

---

## 4. DispatchHub INJECTIONS keys

`{{slot}}` names available in any optimizer prompt. Defined in `dispatch/hub/injections/registry.py::INJECTIONS`. Adding a slot is one entry; using a slot not in the dict is a load-time `KeyError` via `validate_template`.

| Slot | Kind | Description |
|---|---|---|
| `plan` | TRACE | L3's strategic plan text. Persistent until next L3 fire. |
| `l3_to_l2_note` | DIRECTIVE | Sticky L3→L2 pointer. Mounted only in L2's template. |
| `rendered_prompt` | TRACE | Current best searchpoint's compiled prompt body. |
| `pipeline_param_catalogue` | DERIVED | Per-node param menu + ≤4-value enum hint, plus available models. |
| `diagnostics` | DERIVED | Layer-agnostic round readout: STATUS header + RoundDiagnostics body. |
| `validation_failures` | MEASUREMENT | Wound 1: L1 parse-time validator failures. |
| `runtime_failures` | MEASUREMENT | Wound 2: DegradationCheck mid-eval failures. |
| `l2_guard_breaches` | MEASUREMENT | Wound 4: L2 post-parse guard outcomes; non-empty force-triggers L3. |
| `l3_guard_breaches` | MEASUREMENT | L3 post-parse guard outcomes; L3 sees own past breaches. |
| `task_context` | TRACE | Persistent task framing refined by L2; broadcast to all four prompts. |
| `critique` | TRACE | Compact view of the most recent L1_CRITIQUE output dict. |
| `l1_overrides` | TRACE | Current L1 runtime knobs (creativity, n_variants, etc.) as JSON. |
| `l1_signal_catalogue` | DERIVED | Sorted L1_POSSIBLE names L2 may use in l1_layout. |
| `axis_memory` | DERIVED | Cross-cycle axis-keyed digest from AxisIndex. |

**Per-template extras** (caller-supplied via `compile_prompt(**hub_dict, **extras)`): `l1_generate` → `{n_variants}` · `l1_critique`/`l2_context`/`l3_plan` → `{}` · `checkin` → `{consultation_instruction}`.

## 5. CLI flags — `new` and `resume`

`python -m promptpotter new <name>` and `python -m promptpotter resume` are the two write verbs. Stable flag set:

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

Other commands (`report`, `inspect`) have their own flag sets — see `presentation/cli/parsers.py`. Not part of v1 (M11 still touches them).

## 6. Ledger event types

Typed records in `events.jsonl` from `domain/run_records.py`:

- **`PhaseRecord`** — phase enter/exit + round-boundary events. Fields: `record_type="phase"`, `phase`, `event`, `round`, `payload`.
- **`SnapshotRecord`** — per-sample / per-candidate snapshots inside a round. Fields: `record_type="snapshot"`, `event`, `round`, `candidate_idx`, `candidate_total`, `sample_idx`, `sample_total`, `payload`.
- **`ResumeCheckpointRecord`** — replayed-vs-archival decision rows feeding resume divergence checks. Fields: `record_type="checkpoint"`, `round`, `kind` (`ResumeCheckpointKind`), `payload`.
- **`TokenUsageRecord`** — optimizer LLM token rollups for spend. Fields: `record_type="token_usage"`, `model`, `provider`, `input_tokens`, `output_tokens`, `usd_cost`, `round`, `node`.
- **`LLMCallStartRecord`** — paired in-flight marker for live `dashboard.json::in_flight`. Fields: `record_type="llm_call_start"`, `call_id`, `node`, `model`, `round`, `candidate_idx`, `started_at_ms`.
- **`LLMCallRecord`** — paired LLM-call audit record. Fields: `record_type="llm_call"`, `call_id`, `node`, `model`, `round`, `prompt`, `response`, `parsed`, `usage`, `latency_ms`.

Forks within a family share one event stream via `CycleEventLog.inherit_from(parent_offset)`. The forked cycle's `events.jsonl` starts with the parent's records up to `parent_offset` plus a `ResumeCheckpointRecord` of kind `FORK_CUT`.

Subscribers read via `DerivedView.on_record(record)` and MUST NOT write any campaign artifact beyond their declared allowlist — enforced by `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores`.

## 7. Per-cycle artifact paths

Operator-visible files inside `campaigns/{cycle_id}/`. Webapp + downstream tooling read these directly — they are contract.

| Path | Writer | Description |
|---|---|---|
| `index.json` | `CampaignStore.create` / `update` | Campaign metadata: id, parent_session_id, dataset_name, backend_id, n_rounds, best_accuracy, rounds[]. Top-level summary. |
| `log.md` | `presentation/writers.py::write_log_md` | Markdown digest of every closed round + forks + hard samples + final winner. |
| `review.md` | `presentation/writers.py::write_review_md` | Per-round behavior-check + L1Stats narrative. |
| `rounds/round_NNNN.json` | `CampaignStore.save_round_file` | Full per-round detail: candidate scores, evaluators, prompt_fields, pipeline_params, OSP snapshot, decisions. |
| `dashboard.json` (at family root) | `LiveDashboardView._persist` | Live operator view; rewritten on every record. Refresh: 2 s. |
| `output.log` (at family root) | `LiveDashboardView._persist` (indirectly) | ANSI-colored line stream; CLI narration tail. Refresh: 1 s. |
| `langfuse/*.json` | `infrastructure/tracing/langfuse_push.py` | Per-cycle Langfuse export snapshots. |
| `prompts/{node}.json` | `infrastructure/tracing/langfuse_push.py` (and CLI `init`) | Resolved prompt templates for this cycle's runs. |
| `.runtime/ledger.jsonl` | `CycleEventLog.append` | The sole-ingress event log. Internal-but-stable shape (see §6). |
| `.runtime/cache/rounds/round_NNNN.json` | `AuditTrailView.flush` | Per-round audit cache (writer-buffered until round close). |
| `.runtime/cache/candidates/round_NNNN.json` | `CampaignStore.save_round_candidates` | Mid-round candidates checkpoint (deleted after L1 score on escalation). |
| `.runtime/streams/round_NNNN_p_best.jsonl` | `PoBBStreamView._handle_snapshot` | Per-sample P(best) trajectory. |
| `.runtime/stop.flag` | `presentation/api/routers/active.py` `POST /stop` | Operator stop signal; consumed by `session.stop_check`. |
| `.runtime/archived/resumed_at_<ts>/` | `CampaignStore.rewind_to_round` | Rewound rounds + candidates moved here on `--from N`. |

Sibling cycles (forks, diag, sweeps) live under `forks/`, `diag/`, `sweeps/<batch>/forks/` within the family root. Each carries its own per-cycle artifacts; `dashboard.json` + `output.log` are family-root-only (shared across forks). `.runtime/` shapes may change between minor versions — the public `rounds/round_NNNN.json` tree + `index.json` + `log.md` are the contract for any tool reading per-cycle results.

## 8. What is NOT stable

- **Internal module structure** beyond §1–§7. The dispatch hub split into `hub/{bundle, injections, facade, builder}` is internal — only the public symbols (`DispatchHub`, `INJECTIONS`, `build_bundle`, `validate_template`) are stable.
- **Private types** (`_Injection`, `_TEMPLATE_EXTRAS`, etc., plus any `_`-prefixed name or dataclass not re-exported through its package `__init__`).
- **Runtime dataclass shapes** not in §1–§7 (`CycleSlice`, `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc.).
- **In-memory caches** and their invalidation strategies (optimizer LRU caches, the dispatch hub's pipeline-param-catalogue cache, etc.).
- **Prompt templates** at `datasets/_optimizer/pipeline.json::resolved_prompts` — data, intentionally tunable. Forks may edit; we may also edit on any release.
- **Test helpers** (`tests/_helpers.py`).
- **The `webapp/` layout.** M11 surface + M12 control plane still iterating.
