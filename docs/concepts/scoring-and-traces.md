# Scoring and Traces

Traces and scores are separate concerns. Understanding the split matters whenever you edit a scoring formula, resume a campaign, or try to compare runs across policy changes.

---

## Data vs. scoring policy

A trace is a record of what the pipeline did — the query, the prediction, the ground truth, how nodes ranked candidates, what timed out. A score is a judgment *over* a trace — "how good was this?" — and the answer changes with what you're optimizing for. The two belong to different worlds: the trace is a fact, the score is a policy, and conflating them is how campaigns end up silently drifting when a scoring formula is edited mid-flight.

PromptPotter keeps them separate. Traces are written once and never edited. Scores are a view, produced by applying the active scoring policy on demand.

### Traces carry a ledger of scores

Since a trace can be judged under many policies, scores are persisted as a ledger rather than a single slot. Every time a trace is evaluated, the result is written alongside the identity of the scorer that produced it. The ledger grows; past interpretations stay retrievable. Two cycles sharing the same trace corpus but running under different scorers each see their own reading of the same underlying data, without corrupting each other.

Cycle identity reflects this split. A cycle is hashed from its pipeline, prompts, and dataset — not from its scoring formula. Editing the formula doesn't mint a new cycle; the traces it produces are still addressable in the shared corpus, and their ledgers simply gain another entry.

### Rescore-on-load

The separation is enforced at one seam: whenever a trace crosses from disk into memory, it gets rescored under the currently active scorer. Fresh samples, cache hits, trial reloads, cross-campaign memory ingest — all four paths go through the same rescoring step. The hit and score fields you read at runtime are always the current policy's view, even if the trace was captured under an older one.

### Deprecated samples

Some traces describe measurements that are not valid observations — the LLM exhausted its reasoning budget before emitting visible content, the provider returned an empty response, the content filter fired, or some other condition where the response cannot be treated as the model's real attempt at the query. The backend (TermNorm or any other connector) emits **neutral advisory** warnings (e.g. `llm_only:content_empty`) plus raw response shape (`finish_reason`, `reasoning` token count) on every LLM call. PromptPotter's `classify_result()` in `application/optimization/diagnostics.py` walks those signals and derives **fatal codes** (`reasoning_budget_exhausted`, `empty_response`, `output_truncated`, `content_filtered`) — a result whose classifier returns any fatal code is deprecated. Backend = facts, optimizer = policy: the rule table lives in `diagnostics.py` and a new fatal pattern is added there, not in any cross-system string protocol.

A deprecated sample has three effects at the **load boundary**:

- **Excluded from primary statistics.** `hits`, `total`, `errors`, and the accuracy denominator are all computed over the *valid* rows only. The `deprecated` count surfaces alongside so the operator sees how many samples were attempted-but-discarded.
- **Evicted from cache.** When a prior dataset_run is loaded, deprecated entries are filtered out before any cache hit logic runs. The query falls through to a fresh backend call rather than replaying the known-bad measurement. Fresh re-measurements are tagged `retry_of_deprecated_cache` and prefixed with 🔄 in the per-query view.
- **Tagged `DEPR` in the per-query view.** Not a HIT, not a MISS — a third class. Round summaries print `hits/total (N deprecated)` so the smaller denominator is never silently shrinking.

The trace itself stays in `library/dataset_runs/` — the archive is the forensic record of what the backend actually returned, even when the measurement was unusable. Eviction and exclusion live one layer up, on the path from disk to the optimizer's view of "valid observations."

This is consistent with the rescore-on-load principle: rescoring re-applies the active scorer's *judgment*, while deprecation re-applies the runtime's *validity check*. A trace that is "valid but scored differently" gets rescored; a trace that is "not a valid observation" gets evicted before scoring runs.

---

## Decision replay and fork

### Decisions are pure functions of scored results

The optimizer's choices — which candidate wins a round, which ones get eliminated early, when to escalate from L1 to L2, when L3 replans — all derive from scored numbers. That makes them replayable: the same decision function, given freshly rescored inputs, will produce whatever outcome those inputs justify.

When a campaign commits a decision, it also records that decision — its kind, enough to re-derive it, and the outcome it reached. On resume, after rescoring prior trials under the current scorer, the optimizer walks each recorded decision and re-runs the corresponding decision function against the rescored view. If the re-run matches the record, that round stands; if it differs, that's the divergence point — the first place the current policy would have sent the campaign somewhere other than where it actually went.

At the first divergence, the campaign stops. The halt exists to prevent silent drift onto a path the current scorer no longer chooses. The user sees a concrete report — round, decision kind, recorded outcome, current outcome — and decides how to proceed.

### Two-tier decision records

Every decision record splits into two halves:

- **Flow-determining half** — what the divergence check reads. Stores pointers and invariants only: candidate identifiers, round numbers, and gate parameters that do not depend on the active scorer. Anything that is a function of scored numbers is derived on replay from the rescored trial, never stored, because a persisted value computed under the old scorer would manufacture false divergences.
- **Archival half** — everything that matters for meta-analysis but has no business in a gate: full LLM outputs, diagnostic context, the recorded threshold under the old scorer. Replay never reads this half. A rescore that wiggles numeric inputs but leaves the gate intact does not flip the archival payload — the split is what lets a "noisy rescore that doesn't change the flow" pass silently.

### Fork commits to the new policy

If the user wants the new scoring policy to continue, `fork` mints a new cycle rooted at the divergence point with a pointer back to its parent. Trials up to the divergence round are copied into the new tree; the shared trace data stays in place. The old cycle is left untouched. From the fork point forward, the new cycle makes its decisions under the current scorer; the old cycle remains the record of what happened under the original one.

See [../operations/rewind-and-fork.md](../operations/rewind-and-fork.md) for how to run fork and the mechanical differences from rewind.

---

## Composite score and improvement tracking

The scorer split above is per-query: each trace gets a `score` and a `hit` under the active per-query formula. The **composite** is one level up — a single per-round number that combines accuracy with health, latency, recall, and prompt verbosity into a comparable scalar. It is what the operator watches to answer "is this round better than the last?"

The composite is **recorded, not gating**. Round-winner selection compares candidates on per-query accuracy (the user's scoring function); composite is displayed and persisted alongside so a win that came with hidden costs — three errors that cancelled out a higher hit rate, a 4× latency blow-up, a doubled prompt — surfaces in the leaderboard rather than going invisible.

### The default formula

When `campaign.json::scoring` declares no `per_round` formula, the default is:

```
0.65 * accuracy
+ 0.15 * health        # mean of (1 - error_rate, 1 - degraded_rate, 1 - runtime_failure_rate)
+ 0.10 * latency_norm  # 1 - mean_latency_ms / 10_000
+ 0.05 * recall        # source_recall / candidate_recall / cache_hit_rate, averaged over what applies
+ 0.05 * prompt_compactness  # 1 - len(rendered_prompt) / 4_000
```

Every term is in `[0, 1]` and the weights sum to `1.0`. A round of 100% accuracy with no errors, no latency, no retrieval misses, and a short prompt scores `1.0`.

### Verbosity penalty

`prompt_compactness` is the term that punishes overly verbose prompt templates. It reads `len(opt_sp.render())` — the rendered string that goes onto `pipeline_params[prompt_node]["prompt"]` — and returns `1 - length / PROMPT_BUDGET_CHARS` clamped to `[0, 1]`. The default budget is 4 000 characters (~1 000 tokens), a comfortable ceiling for a well-decomposed 8-field prompt; longer prompts push the term toward zero.

Two reasons this is a soft term, not a hard reject:

- A 4 200-char prompt isn't broken — it just costs slightly more for slightly more guidance. A linear curve degrades the term gracefully so a small overage gets a small penalty, not a cliff.
- The 5% default weight is intentionally small. It moves the composite by `0.025` between a 4 000-char prompt and a 0-char prompt, which is enough to break ties but not enough to dominate accuracy. Operators who want a stronger penalty (e.g. paying for tokens out-of-pocket) crank the weight; operators who don't care set it to zero.

To change the weight, override `campaign.json::scoring`:

```json
{"scoring": {"per_round": "0.5 * accuracy + 0.3 * prompt_compactness + 0.2 * latency_norm"}}
```

To mark prompts above a hard threshold instead of a continuous penalty, build a step function:

```
0.7 * accuracy + 0.3 * (0 if prompt_compactness < 0.5 else 1)
```

### Interactive steering

Long runs sometimes reveal that the formula's weights are wrong — the operator wants a stronger verbosity penalty after seeing prompts grow round-over-round, or to flip from accuracy-only to a composite that values latency. The cycle's per-round formula can be hot-swapped without restarting: drop a JSON file at `campaigns/{cycle_id}/scoring_steer.json` with `{"per_round": "..."}`, and the next round-end consumes it. The compile-and-validate step happens before the swap, so a typo (an undefined evaluator name, a syntax error) leaves the running formula intact and the file untouched for the operator to fix.

The steer file is per-cycle (different cycles can carry different formulas) and per-round formula only (per-query steering is intentionally not supported via this seam — changing the per-query scorer mid-run rewrites recorded `hit`/`score` semantics and triggers divergence-replay, which the operator should opt into via `optimize --fork-on-divergence`).

See [../operations/improvement-tracking.md](../operations/improvement-tracking.md) for the operator playbook.
