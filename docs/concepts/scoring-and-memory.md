# Scoring and Memory

Traces are facts. Scores are policy. The persistent memory of every measurement ever taken is the **measurement archive** — the central data interface.

A trace records what the pipeline did — query, prediction, ground truth, node rankings, timeouts. A score judges *over* a trace; the answer changes with what you're optimizing for. Traces are written once and never edited. Scores are a view, produced by applying the active policy on demand.

---

## Score ledger

A trace can be judged under many policies, so scores are persisted as a ledger. Every evaluation writes a `{score, hit, formula}` row alongside the scorer that produced it. Past interpretations stay retrievable. Two cycles sharing a trace corpus under different scorers each see their own reading without corrupting each other.

Cycle identity reflects the split — a cycle is hashed from its pipeline + prompts + dataset, **not** its scoring formula. Editing the formula doesn't mint a new cycle; the traces stay addressable, the ledger gains another entry.

## Rescore-on-load

Every trace gets rescored under the active scorer when crossing from disk to memory. Fresh samples, cache hits, trial reloads, cross-campaign memory ingest — all four paths go through the same rescoring step. The `hit`/`score` you read at runtime is always the current policy's view.

## Deprecated samples

Some traces describe non-observations — LLM exhausted reasoning budget before emitting visible content, empty response, content filter fired. The backend emits **neutral advisory** warnings (e.g. `llm_only:content_empty`) plus raw response shape (`finish_reason`, reasoning token count). PromptPotter's `classify_result()` (`application/optimization/elimination.py`) walks those signals and derives **fatal codes** — a result whose classifier returns any fatal code is deprecated.

Three effects at the load boundary:

- **Excluded from primary statistics.** `hits`, `total`, `errors`, accuracy denominator computed over valid rows only. The `deprecated` count surfaces alongside.
- **Evicted from cache.** Loaded prior measurements filter out deprecated entries before any cache-hit logic. The query falls through to a fresh backend call. Re-measurements tagged `retry_of_deprecated_cache`, prefixed 🔄.
- **Tagged `DEPR` in the per-query view.** Not HIT, not MISS — third class. Round summaries print `hits/total (N deprecated)`.

The trace itself stays in `library/measurements/` — the archive is the forensic record. Eviction lives one layer up. Rescoring re-applies the active scorer's *judgment*; deprecation re-applies the runtime's *validity check*.

## Decision replay

Optimizer choices — round winner, early elimination, escalation L1→L2, L3 replan — all derive from scored numbers. That makes them replayable: the same decision function, given freshly rescored inputs, produces whatever outcome those inputs justify.

When a campaign commits a decision, it records the kind, enough to re-derive it, and the outcome. On resume, after rescoring under the current scorer, the optimizer walks each recorded decision and re-runs the function against the rescored view. Match → round stands. Mismatch → divergence point. The campaign halts to prevent silent drift onto a path the current scorer no longer chooses. The user sees a concrete report and decides.

**Two-tier decision records:**

- **Flow-determining** — what the divergence check reads. Pointers + invariants only (candidate ids, round numbers, gate parameters that don't depend on the active scorer). Anything that's a function of scored numbers is derived on replay, never stored — a persisted value computed under the old scorer would manufacture false divergences.
- **Archival** — full LLM outputs, diagnostic context, recorded threshold under the old scorer. Replay never reads this half. A noisy rescore that doesn't change the flow passes silently.

L2's surface mutations are not separate decision kinds. They live on each round's `opt_search_point` snapshot in the trial JSON. The only L2 decision recorded per fire is `probe_round_commitment` — outcome is whether the next round runs as `normal_round` or `probe_round`.

## Fork commits to the new policy

`fork` mints a new cycle rooted at the divergence point with `parent_cycle_id`. Pre-divergence trials are copied; the shared trace data stays in place. The old cycle is left untouched. From the fork point forward, the new cycle decides under the current scorer; the old cycle remains the record under the original.

How to run fork: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

## Composite — recorded, not gating

The scorer split above is per-query: each trace gets a `score`/`hit`. The **composite** is one level up — a single per-round number combining accuracy with health, latency, recall, and prompt verbosity. What the operator watches.

Composite is **recorded, not gating**. Round-winner selection compares candidates on per-query accuracy; composite displays alongside so a win that came with hidden costs (errors that cancelled gains, latency blow-up, doubled prompt) surfaces in the leaderboard. Hot-swap mechanism + full evaluator list + steering examples: [`../operations/persistence-and-state.md § Steering composite scoring`](../operations/persistence-and-state.md).

---

## The measurement archive

PromptPotter's persistent memory of every measurement ever taken. Lives at `library/` under each tenant root. Append-only, content-addressed, cross-cycle, cross-session, cross-tenant. **The central data interface** — if you want to know what PromptPotter has learned, you read the archive.

One row = one **measurement** = `(sample × config → outcome)`.

### Two natural keys, both first-class

| View | Group by | Question | Method |
|------|----------|----------|--------|
| By training example | `sample_id` | "Every measurement of this example, every config." | `measurements_for_sample(sample_id)` |
| By searchpoint | pipeline config (predicate) | "Every measurement under this config, every sample." | `measurements_for_config(predicate)` |

Both return `list[Measurement]`. They compose:

- *"How does config X handle the hard examples?"* → `measurements_for_config({"llm_only": {"model": "X"}})`, group by `sample_id`.
- *"Which configs got sample 42 right?"* → `measurements_for_sample(42)`, group by `node_configs`.

### On-disk layout

```
{tenant_id}/library/
├── measurements/{run_id}.json    ← facts, append-only
└── measurements.json             ← index over the batches
```

Files are content-addressed by `JobSearchPoint.content_hash(dataset)` — same config + same dataset upserts the same file. This is what makes the archive cross-cycle: cycle A and cycle B that evaluate the same `(config, dataset)` share one stored measurement.

The `Measurement` row is a frozen dataclass (`promptpotter/domain/sample.py`): `run_id, content_hash, sample_id, query, ground_truth, predicted, hit, score, run_scores, node_configs, pipeline_data, created_at`. Denormalized — every row carries enough context to group, filter, or render without a second lookup.

### Lifecycle

- **Write**: every `score_search_point()` call appends one batch via `MeasurementArchive.save(...)`. Content-hash collision = upsert.
- **Cache reuse read**: `score_search_point()` calls `load_reusable_results(node_configs, ...)` — positional-prefix-exact lookup. Skips the backend on hit.
- **Discovery read**: operators / scripts call `measurements_for_sample(...)` / `measurements_for_config(...)`.
- **Digest read**: `AxisIndex.refresh()` walks new entries via `load_since(...)`.

### Derived views — `SampleIndex` + `AxisIndex`

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

Each digest method (`digest_for_l1_generate`, `digest_for_l1_critique`, `digest_for_l2`, `digest_for_l3`) is the LLM-context surface for that prompt site.

### What the archive is *not*

- **Not a cache.** The cache is the archive viewed in `prefix_exact` mode. No separate runtime cache.
- **Not the optimizer's working state.** That's `OptSearchPoint` + per-cycle `campaigns/{cycle_id}/trials/`. See [`state-record.md`](state-record.md).
- **Not session metadata.** Sessions / journals / dashboards live under `sessions/{session_id}/` and `campaigns/{cycle_id}/`.
- **Not the LLM-digest layer.** `AxisIndex` sits *on top of* the archive — a pure derived view.

## See also

- [`../developer/README.md § Cross-run memory`](../developer/README.md) — `Measurement` dataclass, write/read paths, extension seams.
- [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) — full tenant-tree reference; composite scorer formula and hot-swap.
- [`campaign-tree.md`](campaign-tree.md) — how forks share the archive without duplication.
