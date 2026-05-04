# Measurement Archive — the database core

The **measurement archive** is PromptPotter's persistent memory of every measurement ever taken. It lives at `library/` under each tenant root and is **the central data interface** of the system — append-only, content-addressed, cross-cycle, cross-session, cross-tenant.

One row of the archive = one **measurement** = `(sample × config → outcome)`.

If you want to know what PromptPotter has learned, you read the archive.

## Why a separate concept

Three things use accumulated data:

1. The **optimizer loop** reuses prior measurements as cache (skip backend calls when an identical config already ran a sample). This is the cache-reuse path.
2. **LLM digests** project the archive into short text strings injected into L1/L2/L3 prompts (`AxisIndex.digest_for_*`).
3. **Operators / webapp / scripts** want direct, structured access — *"what did we measure for sample 42?"*, *"every measurement under model X?"*

(1) and (2) used to dominate; (3) had no first-class API. The archive concept exposes (3) directly. (1) and (2) are now derived views over the same store.

## Two natural keys, both first-class

Every measurement is keyed by two dimensions equally. The archive supports retrieval along either, with the same return shape:

| View | Group by | Question | Method on `MeasurementArchive` |
|---|---|---|---|
| **By training example** | `sample_id` | *"Show me every measurement of this example, under every config it's ever run against."* | `measurements_for_sample(sample_id)` |
| **By searchpoint** | pipeline config (predicate) | *"Show me every measurement under this config, across every sample, every campaign."* | `measurements_for_config(predicate)` |

Both return `list[Measurement]` — the same denormalized row type. They compose:

- *"How does config X handle the hard examples?"* — start with `measurements_for_config({"llm_only": {"model": "X"}})`, group by `sample_id`.
- *"Which configs got sample 42 right?"* — start with `measurements_for_sample(42)`, group by `node_configs`.

## On-disk layout

```
{tenant_id}/library/
├── measurements/              ← MeasurementArchive: facts (append-only)
│   ├── {run_id}.json          ← one batch = one config × dataset pass
│   └── ...
└── measurements.json          ← index over the batches
```

Both digest layers (`AxisIndex` axis-keyed, `SampleIndex` sample-keyed) are in-memory only — rebuilt from the archive on every refresh, no on-disk file.

Each `measurements/{run_id}.json` carries:

- **Run-level**: `run_id`, `content_hash`, `node_configs`, `pipeline_params`, `scores`, `created_at`.
- **`measurements: list[Measurement]`** (the items): each has `sample_id`, `query`, `ground_truth`, `predicted`, `hit`, `score`, `pipeline_data`.

Files are content-addressed by `JobSearchPoint.content_hash(dataset)` — running the same config against the same dataset upserts the same file. This is what makes the archive cross-cycle: cycle A and cycle B that happen to evaluate the same `(config, dataset)` share one stored measurement.

## The `Measurement` row

```python
@dataclass(frozen=True, slots=True)
class Measurement:
    run_id: str
    content_hash: str
    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: bool
    score: float | None
    node_configs: list[tuple[str, dict]]
    pipeline_data: dict
    created_at: str
    run_scores: dict
```

Frozen, denormalized — every row carries enough context (`node_configs`, `created_at`, `run_scores`) that you can group, filter, or render without a second lookup.

## Predicate shape (config-keyed retrieval)

`predicate: dict[str, dict]` — node-name → required key/value subset.

```python
{"web_search": {"max_sites": 5}}                 # exact value
{"llm_only": {"model": "openai/gpt-oss-120b"}}   # exact value
{"web_search": {}, "llm_rerank": {}}             # node presence (any config)
{"llm_only": {}}                                  # any run with llm_only in chain
```

A run matches iff for every `(node, subdict)` in the predicate, the run's `node_configs` contains a `[node, cfg]` pair where `subdict.items() <= cfg.items()`. Empty subdict = node presence test. **Empty predicate returns `[]`** (an unbounded query is rejected so callers don't accidentally retrieve everything).

## Lifecycle

- **Write**: every `score_search_point()` call appends one batch to `measurements/{run_id}.json` via `MeasurementArchive.save(...)`. Content-hash collision means upsert (replaces), not duplicate.
- **Read for cache reuse**: `score_search_point()` calls `load_reusable_results(node_configs, ...)` — a positional-prefix-exact lookup that returns a `dict[query → QueryResult]` for the active config. Skips the backend on hit.
- **Read for discovery**: operators / scripts call `measurements_for_sample(...)` / `measurements_for_config(...)` — see above.
- **Read for digests**: `AxisIndex.refresh()` walks new entries via `load_since(...)` for the sample side, then rebuilds the axis side from `list_all(...)` in memory. Pure derivation — no parallel persistence.

## Relationship to cache reuse

Both cache reuse and discovery retrieval use one shared matcher (`_node_config_matches`) with two modes:

| Mode | Used by | Semantics |
|---|---|---|
| `prefix_exact` | `find_by_node_configs` (cache reuse) | Positional, full dict equality, stops at first mismatch — for safety. A "matching" prior run is one we can reuse outputs from up to the matched node. |
| `subset` | `measurements_for_config` (discovery) | Unordered, per-node dict subset — for inspection. A "matching" run is one whose chain satisfies the predicate. |

Same archive, same matcher primitive, two viewpoints.

## What the archive is *not*

- **Not a cache.** The cache is the archive viewed in `prefix_exact` mode. There is no separate runtime cache (no `IntermediateCache` class — that was an aspirational note that never landed).
- **Not the optimizer's working state.** That's `OptSearchPoint` + per-cycle `campaigns/{cycle_id}/trials/`.
- **Not session metadata.** Sessions / journals / dashboards live under `sessions/{session_id}/` and `campaigns/{cycle_id}/`.
- **Not the LLM-digest layer.** `AxisIndex` sits *on top of* the archive — a pure derived view that projects it into prompt-injectable text.

## See also

- [`../developer/README.md § Cross-run memory`](../developer/README.md) — `Measurement` dataclass, write/read paths, extension seams.
- [`docs/operations/persistence-and-state.md`](../operations/persistence-and-state.md) — full tenant-tree reference, including non-archive surfaces (sessions, campaigns).
- [`docs/concepts/scoring-and-traces.md`](scoring-and-traces.md) — how measurements are written: rescore-on-load, decision-replay, fork.
- [`docs/concepts/axis-index.md`](axis-index.md) — the digest layer that consumes the archive.
