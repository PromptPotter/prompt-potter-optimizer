# Scoring and Memory

**Traces are facts. Scores are policy. The persistent memory of every measurement ever taken is the measurement archive — the central data interface.**

A trace records what the pipeline did (query, prediction, ground truth, node rankings, timeouts). A score judges *over* a trace; the answer changes with what you're optimizing for. Traces are written once and never edited; scores are a view produced by applying the active policy on demand.

**The archive's shape — the fold, its two derived views, its two retrieval keys, and the content-addressing that makes it cross-cycle** — owned by [`../developer/README.md`](../developer/README.md#4-cross-run-memory). One row there is one **measurement**, `(sample × config → outcome)`; this page owns only what a *score* over that row means.

## Score ledger + rescore-on-load

A trace can be judged under many policies, so scores are persisted as a ledger — `{score, hit, formula}` rows alongside the scorer that produced them. Cycle identity is hashed from pipeline + prompts + dataset, **not** the scoring formula. Editing the formula doesn't mint a new cycle; the traces stay addressable, the ledger gains another entry.

Every trace gets rescored under the active scorer when crossing from disk to memory. Fresh samples, cache hits, trial reloads, cross-campaign memory ingest — all four paths go through the same step. The `hit` / `score` you read at runtime is always the current policy's view.

---

One of the downstream consequences is the system keeps **two costs**, and they answer different questions:

- **The bill** — money that actually left the account. Cache hits contribute nothing to it. This is the headline, and it is what the spend budget caps. It has to stay this way: billing a replay would halt a run that cost nothing to make.
- **The incurred cost** — what the search would cost to run against a cold cache, with cache hits priced from the tokens they recorded (the cached payloads carry them, so nothing is estimated). This is what a *measurement of a candidate* has to divide by.

On a cold cache the two are equal — which is exactly why this could sit undetected until the archive got deep enough for an arm to start free-riding on it.


## Deprecated samples

**Which advisories and response shapes derive a fatal or infra code** — owned by [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md), whose rule table is the only version of it; the word itself is [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md)'s. What belongs here is what a *score* over such a row means: a deprecated result is excluded from primary statistics and evicted from cache, **but the trace itself stays in `measurements/`** — a score is policy and can be withdrawn, a fact cannot, so the archive keeps the forensic record either way.

## Decision replay + fork

Optimizer choices derive from scored numbers, so they are replayable at all — that is this page's thesis paying out. On resume the optimizer rescores under the current scorer and re-runs each recorded decision against the rescored view: match → the round stands, mismatch → halt at the divergence point. What happens next is a fork, and its mechanics are [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § Recovery.

Decision records are two-tier, on the same facts-vs-policy line: **replayable** (which candidate won, the parameters that gated the choice) vs **archival** (full LLM outputs, diagnostic context — never read by replay).

## Composite — recorded, not gating

Round-winner selection compares candidates on difficulty-adjusted ability (θ on the cycle's fixed δ ruler — subset-invariant, so candidates scored on different adaptive subsets stay comparable); **accuracy** and the **composite** (per-round accuracy + health + latency + recall + verbosity) display alongside as subset-relative numbers so a win that came with hidden costs surfaces in the leaderboard. **Changing the composite forks the cycle rather than swapping inside it** — owned by [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § Changing the composite formula — fork, never swap.

θ is the standard IRT/CAT fix: a small statistical model that **structurally** removes the per-round sample-set drift — when the adaptive picker hands each candidate a different subset, raw accuracy is no longer comparable, but ability is. Today it's 1PL (difficulty only); a richer **2PL** variant adds per-sample signal-to-noise (discrimination), giving more power once enough data is collected, and graduates per-dataset only when it beats 1PL out-of-sample. The model itself is owned by [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md).

Why two forks share one archive without duplicating a measurement — content-addressing, in [`campaign-tree.md`](campaign-tree.md).
