# Measurement Archive

PromptPotter's persistent memory of every measurement ever taken. Lives at `library/` under each tenant root. Append-only, content-addressed, cross-cycle, cross-session, cross-tenant. **The central data interface** — if you want to know what PromptPotter has learned, you read the archive.

One row = one **measurement** = `(sample × config → outcome)`.

## Two natural keys, both first-class

| View | Group by | Question | Method |
|------|----------|----------|--------|
| By training example | `sample_id` | "Every measurement of this example, every config." | `measurements_for_sample(sample_id)` |
| By searchpoint | pipeline config (predicate) | "Every measurement under this config, every sample." | `measurements_for_config(predicate)` |

Both return `list[Measurement]`. They compose:

- *"How does config X handle the hard examples?"* → `measurements_for_config({"llm_only": {"model": "X"}})`, group by `sample_id`.
- *"Which configs got sample 42 right?"* → `measurements_for_sample(42)`, group by `node_configs`.

## On-disk layout

```
{tenant_id}/library/
├── measurements/{run_id}.json    ← facts, append-only
└── measurements.json             ← index over the batches
```

Files are content-addressed by `JobSearchPoint.content_hash(dataset)` — same config + same dataset upserts the same file. This is what makes the archive cross-cycle: cycle A and cycle B that evaluate the same `(config, dataset)` share one stored measurement.

Each `measurements/{run_id}.json` carries:

- **Run-level**: `run_id`, `content_hash`, `node_configs`, `pipeline_params`, `scores`, `created_at`.
- **`measurements: list[Measurement]`**: each has `sample_id`, `query`, `ground_truth`, `predicted`, `hit`, `score`, `pipeline_data`.

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

Frozen, denormalized — every row carries enough context to group, filter, or render without a second lookup.

## Predicate shape (config-keyed retrieval)

`predicate: dict[str, dict]` — node-name → required key/value subset.

```python
{"web_search": {"max_sites": 5}}                 # exact value
{"llm_only": {"model": "openai/gpt-oss-120b"}}   # exact value
{"web_search": {}, "llm_rerank": {}}             # node presence (any config)
```

A run matches iff for every `(node, subdict)` in the predicate, the run's `node_configs` contains a `[node, cfg]` pair where `subdict.items() <= cfg.items()`. Empty subdict = node presence test. **Empty predicate returns `[]`** (unbounded queries are rejected).

## Lifecycle

- **Write**: every `score_search_point()` call appends one batch via `MeasurementArchive.save(...)`. Content-hash collision = upsert.
- **Cache reuse read**: `score_search_point()` calls `load_reusable_results(node_configs, ...)` — positional-prefix-exact lookup. Skips the backend on hit.
- **Discovery read**: operators / scripts call `measurements_for_sample(...)` / `measurements_for_config(...)`.
- **Digest read**: `AxisIndex.refresh()` walks new entries via `load_since(...)`.

Cache reuse and discovery share one matcher (`_node_config_matches`) with two modes: `prefix_exact` (cache reuse — positional, full equality, stops at first mismatch) and `subset` (discovery — unordered, per-node subset).

## Derived views — `SampleIndex` + `AxisIndex`

```
MeasurementArchive   ← facts (append-only, persisted)
   │
   ├── SampleIndex   ← per-sample derived view (in-memory; rebuilt every refresh)
   └── AxisIndex     ← axis-keyed derived view (in-memory; rebuilt every refresh)
```

Both digest layers are **in-memory only** — rebuilt from the archive on every refresh, no on-disk file.

The archive answers *"what was actually measured?"* The axis index answers *"across all measurements, which axes shifted fitness?"* The sample index answers *"which queries are informative?"*

`AxisIndex` tracks three things:

- **Parameter impact** — effect size of each axis. Classified `consistently impactful / sometimes impactful / dead`. Drives which axes L1 prioritises.
- **Query patterns** — hits/misses per query across all configs. Informative queries are *discriminating* (some hit, some miss); always-easy and always-hard are noise. The zero-signal filter physically excludes the noise; scoring-set evolution gently swaps it out.
- **Failure modes** — where in the pipeline failures cluster. *"40% of misses fail at web_search"* is the strategic signal L3 needs.

Each digest method (`digest_for_l1_generate`, `digest_for_l1_critique`, `digest_for_l2`, `digest_for_l3`) is the LLM-context surface for that prompt site:

| Consumer | Sees |
|----------|------|
| L1 Generate | Failure clusters, dead queries, top axes, best values |
| L1 Critique | Discriminating queries, failure clusters, tractability, exhausted axes, value trends |
| L2 Refine | Axis rankings, bottleneck distribution, failure × axis correlations, persistent failures |
| L3 Plan | Axis rankings, bottleneck distribution, failure clusters, persistent failures |

## What the archive is *not*

- **Not a cache.** The cache is the archive viewed in `prefix_exact` mode. No separate runtime cache.
- **Not the optimizer's working state.** That's `OptSearchPoint` + per-cycle `campaigns/{cycle_id}/trials/`.
- **Not session metadata.** Sessions / journals / dashboards live under `sessions/{session_id}/` and `campaigns/{cycle_id}/`.
- **Not the LLM-digest layer.** `AxisIndex` sits *on top of* the archive — a pure derived view.

## See also

- [`../developer/README.md § Cross-run memory`](../developer/README.md) — `Measurement` dataclass, write/read paths, extension seams.
- [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) — full tenant-tree reference.
- [`scoring-and-traces.md`](scoring-and-traces.md) — how measurements are written: rescore-on-load, decision-replay, fork.
