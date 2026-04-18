# Observability Audit Table (C2 fixture)

Built before any refactor code lands. Documents every Langfuse-touching call
site in the codebase — `(layer, object_type, parent, metadata, file output)`
— so the post-refactor `LangfuseSink` and `FileSink` can be tested against a
fixture rather than a hope.

**Status:** complete. Use this table to construct the test fixture in
`tests/test_tracing_parity.py` once the new sinks land.

---

## 1. Two distinct Langfuse trace topologies (load-bearing finding)

PromptPotter currently emits **two structurally different Langfuse outputs**.
This is not duplication — they serve different views:

### Topology A — Live optimization trace (forward path)

Source: `ObsLogger._cloud_*` methods, called inline during a campaign.

```
trace: optimization_loop
  ├─ tags: ["campaign", "optimization_loop"]
  ├─ session_id: <langfuse_session_id>
  ├─ input: {campaign_id, baseline_accuracy, config}
  ├─ score: baseline_accuracy
  │
  ├─ span: round_<n>                       (start_span → end_observation)
  │   ├─ as_type: span
  │   ├─ metadata: {round, candidates_scored, optimizer_templates?}
  │   │
  │   ├─ span: <node_id>                   (start_span, parent=round)
  │   │   ├─ as_type: generation | span
  │   │   ├─ metadata: {node_type, ...}
  │   │   └─ end via end_observation
  │   │
  │   ├─ span: prompt_version              (create_span, parent=round)
  │   │   ├─ as_type: tool
  │   │   └─ metadata: {layer1_fields}
  │   │
  │   └─ span: run_<run_id_prefix>         (create_span, parent=round)
  │       ├─ as_type: tool
  │       └─ output: {accuracy, hits, total}
  │
  ├─ score: accuracy_round_<n>
  ├─ score: best_accuracy
  └─ end_trace                              (output: {best_accuracy, n_rounds, stop_reason})
```

**Use:** Real-time monitoring of an optimization campaign. One trace per
campaign run. This is the **OPTIMIZER LAYER** (`OptSearchPoint`) view —
rounds, candidates, prompt evolution.

### Topology B — Per-query evaluation trace (backfill path)

Source: `langfuse_backfill.push_run()`, called post-hoc from a notebook cell.

```
trace: <pipeline_name>_pipeline             (one per query, not per campaign)
  ├─ tags: ["eval", <origin>, "pipeline"]
  ├─ session_id: dataset_<backend_id>
  ├─ metadata: {run_id, llm_provider, prompt_fields_id, pipeline_params}
  ├─ input: {query, ground_truth}
  │
  ├─ span: cache_lookup                    (create_span, no parent)
  │   ├─ as_type: <node.langfuse_type>
  │   ├─ input: {query}
  │   ├─ output: {<node.output_keys>}
  │   └─ metadata: {duration_s, pipeline_params}
  │
  ├─ span: fuzzy_matching                  (sibling)
  ├─ span: web_search                      (sibling)
  ├─ span: entity_profiling                (sibling, model=llm_provider)
  ├─ span: token_matching                  (sibling)
  │
  ├─ score: hit                            (1.0 or 0.0)
  ├─ update_trace: output={predicted, ground_truth, hit, total_time, ...node outputs}
  ├─ end_trace
  └─ link_item_to_run(dataset_item_id, trace_id, run_name=run_id)
```

**Use:** Building Langfuse evaluation datasets. One trace per (query,
dataset_run) pair, linked to dataset items. This is the **TARGET LAYER**
(`JobSearchPoint`) view — pipeline behavior on individual queries, with
ground truth comparison.

### Implication for the refactor

The plan's original "delete `langfuse_backfill.py`" line is **wrong** and
must be revised. Both topologies are load-bearing:

- Topology A is what live runs see.
- Topology B is what evaluation/comparison cells see.

The refactor must preserve **both** as distinct event flows through the new
sink architecture. Concretely, `LangfuseSink` needs to handle two event
families:

1. `OptimizationEvent` family (CampaignStart, RoundStart, RoundEnd, NodeStart,
   NodeEnd, PromptVersion, DatasetRun, CampaignEnd) → emits Topology A.
2. `EvaluationEvent` family (DatasetRegister, QueryEvalStart, QueryEvalEnd,
   QueryNodeSpan, QueryScore) → emits Topology B.

`langfuse_backfill.py` becomes a **replayer**: it reads `dataset_runs/`
JSON and emits `EvaluationEvent`s into the same `LangfuseSink`. The actual
Langfuse SDK calls live in one place (the sink), not two. Net file impact:

- `langfuse_backfill.py` shrinks from 564 LOC → ~150 LOC (just the
  read-from-disk + emit-events loop). The trace-topology logic moves to the
  sink.
- `_cloud_*` methods on `ObsLogger` (the 12 Topology-A wrappers) move to the
  sink and become straight method dispatch on event dataclass type.
- The `_cloud_trace_ids` shadow state moves to the sink and is persisted
  (the resume fix).

**LOC delta updated:**
- Removed: shadow state (~6 fields), `_cloud()` wrapper, 12 `_cloud_*` paired
  methods (~250 LOC), 414 LOC from backfill (the parts that re-implement
  trace structure).
- Net: ~2197 LOC → ~1300 LOC. Less aggressive than original estimate
  (2200 → 1100), but the win is the *single* Langfuse code path, not the
  raw line count.

---

## 2. Public ObsLogger API → file output → cloud output

Each row = one public method = one event dataclass in `events.py`.

| Method | Layer | File output | Langfuse object | Parent | Notes |
|---|---|---|---|---|---|
| `register_dataset(dataset_name, dataset)` | shared | `obs/langfuse/datasets/{name}/{item_id}.json`; `events.jsonl: dataset_registered` | `create_dataset` + `create_dataset_item` per query (skipped if >100 items, deferred to backfill) | — | item_id = sha256(`{dataset_name}:{query}`)[:16] |
| `log_dataset_run(run_id, content_hash, accuracy, total, hits, prompt_fields_id)` | TARGET | `obs/langfuse/traces/{trace_id}.json` (name=`dataset_run`); `obs/langfuse/scores/{trace_id}.jsonl: accuracy`; `events.jsonl: dataset_run` | `create_span` (as_type=tool, name=`run_{run_id[:8]}`) | `_cloud_active_round_obs_id` (under active round) | This is currently TARGET-LAYER scoring nested **under** the OPTIMIZER round span — the topology mixes layers |
| `log_campaign_start(campaign_id, config, baseline_accuracy, session_id)` | OPTIMIZER | `obs/experiments/{campaign_id}/meta.yaml`; `obs/langfuse/traces/{trace_id}.json` (name=`optimization_loop`); `scores/{trace_id}.jsonl: baseline_accuracy`; `events.jsonl: campaign_start` | `create_trace(name="optimization_loop")` | — (root) | Sets `_cloud_active_trace_id`, `_cloud_active_session_id`, `_cloud_trace_ids[campaign_id]` |
| `log_node_start(trace_id, node_id, node_type, obs_type, input_data, metadata)` | TARGET | `obs/langfuse/observations/{trace_id}/{obs_id}.json`; `events.jsonl: node_start` | `start_span(as_type=generation|span)` | `_cloud_active_round_obs_id` | Returns obs_id for end pairing. Used by `observed_node` ctxmgr |
| `log_node_end(obs_id, trace_id, node_id, output_data, metrics, error)` | TARGET | Updates observation JSON in place; `events.jsonl: node_end` | `end_observation(cloud_obs_id, output, metadata)` | matches start | `_cloud_active_step_obs_ids.pop(node_id)` |
| `log_round_start(campaign_id, round_num)` | OPTIMIZER | `obs/langfuse/observations/{trace_id}/round_{n}_start.json` | `start_span(name="round_{n}", as_type=span)` | parent=trace (campaign) | Sets `_cloud_active_round_obs_id` |
| `log_round_end(campaign_id, round_num, accuracy, hits, total, improved, winner_prompt_fields_id, candidate_scores, next_action, model, temperature, n_variants, optimizer_templates)` | OPTIMIZER | observation JSON (name=`round_{n}`); `scores/{trace_id}.jsonl: accuracy`; MLflow run dir under `obs/experiments/{campaign_id}/{run_id}/` (params, metrics, tags); `events.jsonl: round_complete` | `end_observation(round_obs_id)` + `create_score(name="accuracy_round_{n}")` | round obs | Clears `_cloud_active_round_obs_id` after |
| `log_prompt_version(prompt_fields_id, rendered_prompt, layer1_fields, parent_id)` | OPTIMIZER | `obs/prompts/optimizer_prompt/{version}/prompt.txt`; `metadata.json`; `events.jsonl: prompt_version` | `create_span(name="prompt_version", as_type=tool)` | `_cloud_active_round_obs_id` | **BUG:** uses `next(reversed(_cloud_trace_ids.values()))` — relies on dict insertion order, fragile across resume |
| `log_campaign_end(campaign_id, best_accuracy, n_rounds, stop_reason, best_round)` | OPTIMIZER | Updates trace JSON output; `scores/{trace_id}.jsonl: best_accuracy`; `events.jsonl: campaign_end` | `create_score(name="best_accuracy")` + `update_trace(output)` + `end_trace` | trace | Clears `_cloud_active_trace_id`, `_cloud_active_session_id` |
| `flush()` | — | — | `lf.flush()` (Langfuse SDK flush) | — | Called from `runner._finalize_run` |
| `get_file_trace_id(campaign_id)` | accessor | — | — | — | Returns `_campaign_traces[campaign_id]` |
| `get_cloud_trace_id(campaign_id)` | accessor | — | — | — | Returns `_cloud_trace_ids[campaign_id]` — **broken across resume** |
| `start_campaign(...)` (classmethod) | convenience | wraps log_campaign_start + register_dataset under graceful | — | — | Constructed in `optimize.py:484` and `runner.py` paths |
| `end_campaign(obs_campaign_id, best_accuracy, n_rounds, stop_reason, best_round)` | convenience | wraps log_campaign_end + flush + get_cloud_trace_id | — | — | Returns cloud_trace_id (which is None on resume because of the bug) |

---

## 3. Backfill API (Topology B)

| Function | File output | Langfuse object | Notes |
|---|---|---|---|
| `push_all_runs(store, backend_id)` | `obs/langfuse/backfill_state.json` (registry of pushed run_ids) | calls push_run() per dataset_run | Idempotent — skips runs in `backfilled_run_ids` |
| `_register_dataset_items(lf, gt_map)` | updates `state.dataset_items` | `create_dataset` + `create_dataset_item` (or `update_dataset_item`) | Reconciles existing Langfuse dataset items by query string |
| `push_run(lf, store, backend_id, run_id, schema, query_to_item_id, session_id)` | updates `state.backfilled_run_ids` + `langfuse_trace_ids[run_id]` | per query: `create_trace` + N×`create_span` + `create_score("hit")` + `update_trace` + `end_trace` + `link_item_to_run` | One trace per query — Topology B |

---

## 4. Call sites (where each method is invoked)

| Call site | Method | Layer constructor |
|---|---|---|
| `application/scoring/search_point_scorer.py:266` | `log_scoring_to_obs` → `log_dataset_run` | TARGET (per-SP scoring) |
| `application/optimization/nodes/escalation.py:215` | `observed_node` → `log_node_start/end` | TARGET (per-node within escalation) |
| `application/optimization/nodes/round_execution.py:131,231` | `observed_node` → `log_node_start/end` | TARGET (per-node within round execution: l1_generate, etc.) |
| `application/optimization/nodes/round_execution.py:285` | `log_round_start` | OPTIMIZER |
| `application/optimization/nodes/round_execution.py:355` | `log_round_end` | OPTIMIZER |
| `application/optimization/nodes/round_execution.py:371` | `log_prompt_version` | OPTIMIZER |
| `application/campaign/runner.py:457` | `obs.end_campaign` → `log_campaign_end` + flush | OPTIMIZER |
| `application/campaign/runner.py:474` | `obs.get_cloud_trace_id` | accessor |
| `application/campaign/runner.py:88` | `obs.get_file_trace_id` | accessor |
| `application/campaign/data.py:151` | `obs.register_dataset` | shared (campaign init) |
| `presentation/ui/campaign/optimize.py:484` | `ObsLogger(...)` direct construction | shared (notebook display path; uses `langfuse=None` so file-only) |
| `domain/scoring.py:100,117,136` | `obs: ObsLogger \| None` typed parameter | type annotation only |

**One-factory invariant:** all live constructors of `ObsLogger` flow through
either `ObsLogger.start_campaign(...)` (via `runner.py`) or the file-only
direct construction in `optimize.py:484`. The refactor's
`ObservabilityBridge` factory replaces these in `init_services` (or
equivalent) and `optimize.py:484` switches to a `bridge.file_only()`
constructor. No surface-specific subclasses.

---

## 5. Latent bugs to fix as part of the refactor

1. **Resume cloud trace map loss.** `_cloud_trace_ids` is in-memory; on
   resume, `get_cloud_trace_id` returns None and post-resume rounds attach
   to nothing. Fix: persist to `campaigns/{cycle_id}/langfuse/state.json`
   (per-cycle, Wave C).
2. **`_cloud_prompt_version` fragile lookup.** Uses
   `next(reversed(_cloud_trace_ids.values()))` — depends on dict insertion
   order and breaks on resume. Fix: pass `campaign_id` through the
   `PromptVersion` event dataclass; sink looks up by campaign.
3. **Topology A and B never share dataset_item ids.**
   `_cloud_register_dataset` (live) and `_register_dataset_items` (backfill)
   each maintain their own `query → item_id` map. After this refactor,
   both paths read from one shared `campaigns/{cycle_id}/langfuse/state.json`
   so a campaign that ran `register_dataset` live can later be backfilled
   without duplicating items. Treat as a side-effect win, not a separate task.

---

## 6. Test fixture shape

`tests/test_tracing_parity.py` (new) drives a recorded fixture campaign
through the bridge with a recording `LangfuseSink` (captures every SDK call
as a tuple) and asserts:

```python
expected = [
    ("create_trace", "optimization_loop", None, {"campaign_id": ..., ...}),
    ("create_score", "baseline_accuracy", trace_id, ...),
    ("start_span", "round_1", trace_id, ...),
    ("start_span", "l1_generate_r1", round_obs_id, ...),
    ("end_observation", "l1_generate_r1", ..., ...),
    ("create_span", "prompt_version", round_obs_id, ...),
    ("create_span", "run_<id>", round_obs_id, ...),
    ("end_observation", "round_1", ..., ...),
    ("create_score", "accuracy_round_1", trace_id, ...),
    # ... more rounds ...
    ("create_score", "best_accuracy", trace_id, ...),
    ("update_trace", trace_id, ...),
    ("end_trace", trace_id),
]
assert recorder.calls == expected
```

A second fixture exercises Topology B via the backfill replayer against a
recorded `dataset_runs/` directory and asserts the per-query trace
structure. Both fixtures get committed under `tests/fixtures/tracing/`.
