# Improvement Tracking

How to read the composite score, watch its trajectory across rounds, and steer it interactively without restarting a campaign.

---

## What the composite score is

The composite score is a single `[0, 1]` number per round that combines accuracy with health, latency, recall, and prompt verbosity. It is what the operator watches to answer "is this round better than the last?" without having to read four separate columns.

The composite is **recorded, not gating**. The optimizer compares candidates on per-query accuracy (the user's scoring function in `campaign.json::scoring`); composite is recorded alongside so a win that came with hidden costs surfaces in the leaderboard rather than going invisible. The default formula is:

```
0.65 * accuracy
+ 0.15 * health         # mean of (1 - error_rate, 1 - degraded_rate, 1 - runtime_failure_rate)
+ 0.10 * latency_norm   # 1 - mean_latency_ms / 10_000
+ 0.05 * recall         # averaged over source_recall / candidate_recall / cache_hit_rate that apply
+ 0.05 * prompt_compactness  # 1 - len(rendered_prompt) / 4_000
```

Conceptual background: [`docs/concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md#composite-score-and-improvement-tracking).

---

## Where the trajectory lives on disk

Three files in `campaigns/{cycle_id}/` track the composite over time. None of them require running anything — they are written on every round-end and on finalize.

| File | Purpose | Granularity |
| --- | --- | --- |
| `dashboard.json` | Live scalar state — current round's composite, baseline, best | Per-callback (per-query → per-candidate cadence) |
| `index.json::trials` (+ `trials/trial_NNNN.json`) | Per-round checkpoint with `accuracy`, `composite`, `evaluators`, `improved` | Per-round |
| `log.md` | Markdown digest with the full trajectory + per-round composite block | Rendered every round-end and at finalize |

For a numerical trajectory, walk `trials/trial_NNNN.json` files in order and read `composite` + `evaluators` from each. The `evaluators` dict carries every named contribution to the composite — `accuracy`, `latency_norm`, `prompt_compactness`, etc. — so you can see *which* term moved when the composite shifted.

The optimizer state itself carries a compact mirror: `OptSearchPoint.round_history` is a list of `RoundSummary` records, each with `(round, accuracy, composite, improved, degraded_queries, ...)`. This is what `build_trajectory_report` consumes when L1/L2/L3 need to reason about prior rounds.

## Reading the composite block

Every per-candidate box, every L1_SCORE summary, every round summary, and every `log.md` round section now print a uniform composite block that names the formula and shows each evaluator value:

```
composite = 0.3667
formula:  0.65 * accuracy + 0.15 * (((1 - error_rate) + (1 -
          degraded_rate) + (1 - runtime_failure_rate)) / 3) +
          0.10 * latency_norm + 0.05 * accuracy + 0.05 *
          prompt_compactness
  accuracy=0.167              error_rate=0.000
  degraded_rate=0.000         runtime_failure_rate=0.000
  latency_norm=0.985          prompt_compactness=0.998
```

The block lists every named evaluator that appears in the formula, in first-appearance order, paired across two columns. Builtins (`min`, `max`, `log`) and bare numbers are filtered out — only registered evaluator names show. Sub-expressions like the health term are not decomposed; you see the formula text plus the inputs, and can do the per-term math from there.

When the formula is unset (rare — typically means rendering before init completes), the block collapses to `composite = 0.3667 (formula unavailable)`.

**`PROMPTPOTTER_COMPACT_DISPLAY=1`** in the environment reverts the live CLI / notebook surfaces to the legacy single-line `composite=0.4f` bottom rule (only when composite ≠ accuracy). `log.md` is unaffected — the digest is the operator's permanent record and always carries the full block.

---

## Reading verbosity

`prompt_compactness` is the evaluator term that flags overly verbose prompts. It is `1.0` for short prompts, `0.0` for prompts at or beyond 4 000 chars (the default budget — about 1 000 tokens). The mapping is linear:

| `prompt_compactness` | Approximate length |
| --- | --- |
| `1.00` | ≤ 0 chars (vacuous) — also returned when no candidate is supplied |
| `0.95` | ~200 chars |
| `0.50` | ~2 000 chars |
| `0.10` | ~3 600 chars |
| `0.00` | ≥ 4 000 chars |

To recover the raw character count from a recorded value: `chars = (1 - prompt_compactness) * 4_000`.

The `evaluators` dict in each trial JSON carries this term unconditionally, so a campaign whose prompts grew round-over-round shows up as a downward `prompt_compactness` line even when accuracy was steady. That is the signal the verbosity penalty is meant to surface.

---

## Steering the composite interactively

The cycle's per-round formula can be hot-swapped between rounds by dropping a JSON file. The next round-end consumes it; the running optimizer never restarts.

### File-drop mechanism

1. Author a new `per_round` formula. The namespace is the active per-round evaluator registry — check `evaluators` in any `trials/trial_NNNN.json` for the names that apply on your pipeline.
2. Write `campaigns/{cycle_id}/scoring_steer.json`:

   ```json
   {"per_round": "0.5 * accuracy + 0.3 * prompt_compactness + 0.2 * latency_norm"}
   ```

3. Wait for the next round to complete. The operator log emits a `scoring_steer applied` phase event with the new formula and a pointer to the archive copy.

What happens under the hood:

- The file is **shape-validated** (must be a JSON object with a non-empty string `per_round`).
- The formula is **smoke-compiled** against a synthetic namespace (every registered per-round evaluator at value `0.5`) so an undefined name or a syntax error fails before the swap.
- On success, `session.round_scorer` is replaced and the file is renamed to `scoring_steer.applied.{ts}.json` in the same directory. The next round and every round after is composited under the new formula.
- On failure (invalid JSON, missing `per_round` key, undefined name, syntax error), the running formula is **untouched** and the file is left in place — fix the formula and the next round will retry.

### What you can put in the formula

Names available by default (gated by `applies(schema)`):

| Name | Range | Meaning |
| --- | --- | --- |
| `accuracy` | `[0, 1]` | Mean per-query score |
| `error_rate` | `[0, 1]` | Fraction of queries that errored |
| `degraded_rate` | `[0, 1]` | Fraction with pipeline degradation warnings |
| `runtime_failure_rate` | `[0, 1]` | OptSP runtime-failure count, normalized |
| `latency_norm` | `[0, 1]` | `1 - mean_ms / 10_000`; 1.0 = instant |
| `prompt_compactness` | `[0, 1]` | `1 - len(rendered_prompt) / 4_000`; 1.0 = short |
| `pipeline_compactness` | `[0, 1]` | `1 - (active_steps - 1) / 11`; 1.0 = single-node |
| `source_recall` | `[0, 1]` | GT in candidate-source output (when a `candidate_source` node is active) |
| `candidate_recall` | `[0, 1]` | GT in ranker `final_ranking` (when a `ranker` node is active) |
| `cache_hit_rate` | `[0, 1]` | Cache-node short-circuit fraction |
| `mean_retrieval_shortfall` | `[0, 1]` | Mean `min(observed/target, 1.0)` across `max_*`/`num_*` nodes |

Helpers in scope: `min`, `max`, `float`, `int`, `bool`, `abs`, `round`, `log`, `sqrt`, `exp`, `pow`. Output is clamped to `[0, 1]`. Undefined names raise `NameError` — fail loud is the contract.

### Worked examples

**Crank the verbosity penalty.** A campaign whose prompts grew from 1 200 to 3 800 chars over six rounds, with accuracy moving by less than 1% — drop:

```json
{"per_round": "0.5 * accuracy + 0.3 * prompt_compactness + 0.2 * latency_norm"}
```

The next round selects on a composite where 30% of the budget is anti-verbosity. The optimizer's L2 directives will start asking L1 to compress.

**Mark only the worst-case verbose prompts.** Rather than a continuous penalty, treat the budget as a hard line:

```json
{"per_round": "0.85 * accuracy + 0.15 * (1 if prompt_compactness > 0.5 else 0)"}
```

A prompt over ~2 000 chars zeros the compactness term entirely; under it, the term is full. Step functions are useful when you want the penalty to be "yes/no" rather than gradual.

**Optimize for cache-warm pipelines.** When a `cache` node is active and you want it to *be used*:

```json
{"per_round": "0.7 * accuracy + 0.2 * cache_hit_rate + 0.1 * latency_norm"}
```

### When NOT to steer

Per-query steering is intentionally not supported by this file-drop. Changing `compile_scorer` mid-run rewrites the recorded `hit`/`score` semantics on every prior trace, which triggers the divergence-replay walker on next resume — the right tool for that is `optimize --fork-on-divergence`, which forks a new cycle from the divergence point under the new policy. See [`rewind-and-fork.md`](rewind-and-fork.md).

---

## Code references

- Evaluator registry + default formula: [`promptpotter/application/scoring/evaluators.py`](../../promptpotter/application/scoring/evaluators.py)
- Composite computation gateway: [`promptpotter/application/scoring/metrics.py::compute_composite_score`](../../promptpotter/application/scoring/metrics.py)
- Hot-swap module: [`promptpotter/application/scoring/scoring_steer.py`](../../promptpotter/application/scoring/scoring_steer.py)
- Wired into `_post_round` after `cb.on_round_complete`: [`promptpotter/application/campaign/runner.py`](../../promptpotter/application/campaign/runner.py)
- Per-round trajectory mirror: [`promptpotter/domain/opt_search_point.py::RoundSummary`](../../promptpotter/domain/opt_search_point.py)
