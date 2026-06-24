# Scoring and Memory

**Traces are facts. Scores are policy. The persistent memory of every measurement ever taken is the measurement archive — the central data interface.**

A trace records what the pipeline did (query, prediction, ground truth, node rankings, timeouts). A score judges *over* a trace; the answer changes with what you're optimizing for. Traces are written once and never edited; scores are a view produced by applying the active policy on demand.

```
MeasurementArchive   ← facts (append-only, persisted at archive/measurements/{run_id}.json)
   │
   ├── SampleIndex   ← per-sample derived view (in-memory; rebuilt every refresh)
   └── AxisIndex     ← axis-keyed derived view (in-memory; rebuilt every refresh)
```

One row = one **measurement** = `(sample × config → outcome)`. Two natural keys, both first-class: `measurements_for_sample(sample_id)` and `measurements_for_config(predicate)`. Files are content-addressed by `JobSearchPoint.content_hash(dataset)` — same config + same dataset upserts the same file. This is what makes the archive cross-cycle.

## Score ledger + rescore-on-load

A trace can be judged under many policies, so scores are persisted as a ledger — `{score, hit, formula}` rows alongside the scorer that produced them. Cycle identity is hashed from pipeline + prompts + dataset, **not** the scoring formula. Editing the formula doesn't mint a new cycle; the traces stay addressable, the ledger gains another entry.

Every trace gets rescored under the active scorer when crossing from disk to memory. Fresh samples, cache hits, trial reloads, cross-campaign memory ingest — all four paths go through the same step. The `hit` / `score` you read at runtime is always the current policy's view.

## Deprecated samples

Backend emits neutral advisories (e.g. `llm_only:content_empty`) + raw response shape; `classify_result()` walks those signals and derives **fatal codes** (reasoning budget exhausted, empty response, content filtered). A result whose classifier returns any fatal code is deprecated and excluded from primary statistics; evicted from cache (fresh re-measurements tagged `retry_of_deprecated_cache`, prefixed 🔄); tagged `DEPR` in per-sample view. The trace itself stays in `archive/measurements/` — the archive is the forensic record.

## Decision replay + fork

Optimizer choices derive from scored numbers, so they're replayable. On resume the optimizer rescores under the current scorer and re-runs each recorded decision against the rescored view. Match → round stands. Mismatch → halt at divergence point. `resume --fork-on-divergence` mints a sibling cycle rooted at the divergence point with `parent_cycle_id`; pre-divergence trials are copied, shared trace data stays in place. From the fork point forward, the new cycle decides under the current scorer; the old cycle remains the record under the original.

Two-tier decision records: **replayable** (which candidate won, parameters that gated the choice) vs **archival** (full LLM outputs, diagnostic context — never read by replay).

## Composite — recorded, not gating

Round-winner selection compares candidates on difficulty-adjusted ability (θ on the cycle's fixed δ ruler — subset-invariant, so candidates scored on different adaptive subsets stay comparable); **accuracy** and the **composite** (per-round accuracy + health + latency + recall + verbosity) display alongside as subset-relative numbers so a win that came with hidden costs surfaces in the leaderboard. Hot-swap via `campaigns/{campaign_id}/cycles/{cycle_id}/scoring_steer.json`.

θ is the standard IRT/CAT fix: a small statistical model that **structurally** removes the per-round sample-set drift — when the adaptive picker hands each candidate a different subset, raw accuracy is no longer comparable, but ability is. Today it's 1PL (difficulty only); a richer **2PL** variant adds per-sample signal-to-noise (discrimination), giving more power once enough data is collected, and graduates per-dataset only when it beats 1PL out-of-sample. Rationale: [`../specs/fitness-comparability.md`](../specs/fitness-comparability.md).

## Pointers

- Glossary entries for `MeasurementArchive`, `AxisIndex`, `SampleIndex`, `score_search_point()`, `classify_result()`: [`../glossary.md`](../glossary.md)
- Application contract: [`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
- Operator-facing forks + composite hot-swap: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)
- Campaign-tree forks share the archive without duplication: [`campaign-tree.md`](campaign-tree.md)
