# Scoring and Traces

Traces are facts. Scores are policy.

A trace records what the pipeline did — query, prediction, ground truth, node rankings, timeouts. A score judges *over* a trace; the answer changes with what you're optimizing for. Traces are written once and never edited. Scores are a view, produced by applying the active policy on demand.

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

Composite is **recorded, not gating**. Round-winner selection compares candidates on per-query accuracy; composite displays alongside so a win that came with hidden costs (errors that cancelled gains, latency blow-up, doubled prompt) surfaces in the leaderboard.

Default formula when `campaign.json::scoring` declares no `per_round`:

```
0.65 * accuracy
+ 0.15 * health        # mean of (1 - error_rate, 1 - degraded_rate, 1 - runtime_failure_rate)
+ 0.10 * latency_norm  # 1 - mean_latency_ms / 10_000
+ 0.05 * recall        # source_recall / candidate_recall / cache_hit_rate, averaged
+ 0.05 * prompt_compactness  # 1 - len(rendered_prompt) / 4_000
```

Every term ∈ `[0, 1]`; weights sum to 1.0.

`prompt_compactness` is soft, not a hard reject — a 4 200-char prompt isn't broken, just costs slightly more. The 5% default weight is intentionally small (≤ 0.025 swing across the range). Override the weight via `campaign.json::scoring`.

Hot-swap mechanism + full evaluator list + steering examples: [`../operations/persistence-and-state.md § Steering composite scoring`](../operations/persistence-and-state.md).
