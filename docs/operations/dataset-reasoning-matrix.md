# Dataset Reasoning Matrix — Per-Dataset `pipeline.yaml` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.yaml`. Operators tune per-cycle via `campaign.json` overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-20b:nitro` | `low` | absent | Competition math. Chosen on price ($0.03/$0.14) with `:nitro` routing to the highest-throughput provider at no cost premium. Empirical A/B table below. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `bbeh` | `openai/gpt-oss-20b` | `low` | absent | "Big-Bench Extra Hard" puzzles. `low` is intentional. |
| `justlogic-d234` | `openai/gpt-oss-20b:nitro` | `low` | absent | JustLogic (Chen 2025), 3-class deductive reasoning (`TRUE`/`FALSE`/`Uncertain`). iid random mix of depths 2, 3, 4 (200/depth from HF `train`, seed=42, interleaved). Each depth cut is a separate dataset name and shares no cache key with another — never compare across cuts (`datasets/CLAUDE.md` § L4). |
| `lca-termnorm` | `openai/gpt-oss-120b` | n/a | absent (`null`) | Multi-node TermNorm pipeline; not a single-call reasoning dataset. |
| `lca-bom-termnorm` | `entity_profiling` → `openai/gpt-oss-20b` | `low` (entity_profiling) | absent (`null`) | Tenant material-matching pipeline (`web_search → entity_profiling → token_matching`, no `llm_ranking`). `entity_profiling` emits **native** `json_schema` and pins `reasoning_effort: low` (the cap is load-bearing — see section below). Multi-node, so the single-call columns describe the profiling node only. Tenant config on disk, gitignored. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.yaml` node config — provider ceiling applies. Held by convention, not by a test: nothing enforces it, so check the overlay rather than assuming.

## The Groq output ceiling

Groq enforces a per-model output ceiling. On `gpt-oss-20b` it's ~2048 tokens. Reasoning-trace tokens are charged against this same budget on `openai/gpt-oss-*` models. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges — `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried (which trips the same trap again).

## The default model for a new dataset

The floor default for a **new** dataset is `openai/gpt-oss-20b:nitro @ low` via OpenRouter (cheapest, fastest, leaves L1 headroom).

## Per-sample timings understate wall-clock

The `[ N] XX.Ys` per-sample line in the PromptPotter CLI reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries — so summing the per-row lines understates true wall-clock whenever retries fire. A UX issue, not a correctness one; the fix belongs in the TermNorm repo.

## AIME 2025 model A/B/C tests

Operator A/B/C hunt for a cost+speed+quality sweet spot on AIME competition math. All routes: OpenRouter, `reasoning_effort: low`, `temperature: 0.0`, dataset 20-query origin slice (≥10 samples measured before swap). Latency figures are provider-dependent and were taken on one day — treat them as a ranking, not a spec.

| Model | Avg latency / query | Output tokens (avg) | Origin hits (n / measured) | Quality | Cost | Verdict |
|---|---|---|---|---|---|---|
| `openai/gpt-oss-20b:nitro` | **3.1-9.6s, ~5s median** | ~2300 mean (range 524-6935; tail-heavy — two outliers at 6.8k/6.9k on hard combinatorics problems) | **6/20 = 30%** origin |  | ✓ identical price to non-:nitro ($0.03/$0.14) | **✅ The current AIME pin.** |
| `meta-llama/llama-3.3-70b-instruct:nitro` | ~3.0s median, ~3.8s mean | ~975 | 1/10 | 2/5 ★★ | ✓ cheap, very fast (Cerebras/SambaNova routing) |  Fast+cheap; let L1 explore upward via prompt mutation. |
| `google/gemma-3-27b-it` | ~28s mean (n=2 completed before interrupt) | ~700 | 1/2 | insufficient data | n/a | **Unstable on OpenRouter** — ReadTimeouts + provider-side `timeout_s=60 attempt=3/3` retries |
| `mistralai/mistral-small-3.2-24b-instruct:nitro` | ~29s mean (n=4 before interrupt) | ~3000 (high) | 3/4 | ★★★ quality OK | $0.10/$0.30 nominal — but slow | Quality decent, speed not. |
| `qwen/qwen3-30b-a3b-instruct-2507` | ~27s mean per [RESP] | ~1700 (high variance: 754…5782) | **6/6** on cached replay |  |  | **Smartest of all tested models on AIME math, but unstable on OpenRouter** |

# More models to look at

Realistic candidates — production tier (not `:free`), priced ≤ ~$0.05 in / ~$0.20 out, fast routing available:

## The queue (speed + intelligence unconfirmed; **prices are live**, read off the OpenRouter catalogue 2026-08-06)

The tok/s and AA-intelligence columns are still vendor claims taken on unknown, non-uniform
measurement parameters — a ranking hint at best. The price column is no longer a guess, and
four of the eight rows moved materially when checked, which is why nothing here is quoted
without a re-read. Rows marked ✅ were measured on JustLogic in the section below.

| Model                      | Role guess    | AA Intel. | Speed              | TTFT       | Price in / out | Effort knob | Fit / verdict   |
| -------------------------- | ------------- | --------- | ------------------ | --------------- | ------------- | ----------------- | ------ |
| gpt-oss-120b               | baseline opt  | 33.3      | 216 tok/s          | 0.46s           | $0.037 / $0.170 | high / low / med  | |
| GLM-4.7                    | Smarter opt   | 42.1      | 79.8 tok/s         | 0.84s           | $0.40 / $1.75 | reasoning | queued as an OUTER arm, unrun |
| GLM-5.2                    | High-end opt  | 51        | 176 tok/s          | 1.50s           | unconfirmed | thinking-effort | excluded by operator decision — do not add to an arm list |
| Nemotron 3 Ultra           | candidate opt | unknown   | 65 tok/s           | unknown         | $0.60 / $3.60 | unknown     | ~20× the incumbent optimizer's output price; the 65 tok/s makes it the likeliest speed-gate casualty |
| gpt-oss-20b                | Worker        | 61.1      | 265 tok/s          | 0.46s           | $0.030 / $0.130 | high / low | ✅ |
| inclusionai/ling-3.0-flash | Fast worker    | unknown   | vendor: 1000 tok/s peak | <100 ms claimed | $0.075 / $0.220 | hybrid reasoning  | ✅ **out** — no structured-output support |
| nemotron-3-nano-30b-a3b    | Agentic worker | unknown   | unknown | unknown | $0.050 / $0.200 | low  | ✅ |
| xiaomi/mimo-v2-flash       | Smart fallback worker | 39.2      | 125 tok/s  | 1.69s           | **delisted** | reasoning enabled | superseded by `xiaomi/mimo-v2.5` ($0.14/$0.28) ✅ |

## JustLogic-d234 model A/B

**This ranking is tied to JustLogic's I/O shape and must be re-run per dataset.** JustLogic
sends a long premise block and returns a short label plus a reasoning trace, so input dominates
and per-Mtok *input* price carries weight it would not carry on a generate-heavy task. Ranked in
the operator's order: **speed, then cost, then quality.** All routes OpenRouter,
`reasoning_effort: low`, `temperature: 0.0`; 20 fresh calls per arm on a seed-20 10-row draw
(constant-answer floor 0.400). Accuracy from a 10-row draw is **not** comparable to the 40-row
panel bank — `draw_bank` samples, so a 10-draw is a different bank, not a prefix.

| Arm | Median latency | $ / 40-row pass | Out tokens (reasoning) | Top-label share | Hits/20 | Verdict |
|---|---|---|---|---|---|---|
| `openai/gpt-oss-20b:nitro` | **3.5s** | $0.0088 | 619 (453) | 95% | 9/20 | ✅ **retained** — fastest by 2×, and speed is axis 1 |
| `mistralai/mistral-small-3.2-24b-instruct:nitro` | 6.8s | **$0.0053** | 325 (0) | 50% | 12/20 | cheaper *and* less degenerate, but ~2× the wall-clock — rejected on axis 1 |
| `nvidia/nemotron-3-nano-30b-a3b:nitro` | 9.1s | $0.0207 | 2424 (2208) | 85% | 11/20 | rejected — the 3B-active speed advantage is spent on reasoning tokens |
| `xiaomi/mimo-v2.5:nitro` | 17.9s (mean 25, max 92) | $0.1142 | 1358 (1039) | 50% | 18/20 | clearly the smartest; ~13× the cost and ~5× the latency. ~$6.4 per L4 C0 panel vs the incumbent's ~$0.49 |
| `z-ai/glm-4.7-flash` | 35-40s then ReadTimeout, both routes | — | — | 100% | **dead** | returns `content_empty` + `finish_reason=stop` + 5352 reasoning chars, triggering a schema-repair re-prompt before timing out |
| `inclusionai/ling-3.0-flash` | HTTP 405 | — | — | — | **dead** | DeepInfra: `json_schema response format is not supported` — every node here is schema-bearing |

Three things this measurement established that no catalogue would have:

- **`:nitro` changes PRICE, not only speed.** It routes to the fastest provider and that
  provider sets its own rate — mimo billed ~6× its listed $0.14/$0.28. Price an arm off the
  **wire** `cost_usd` the provider returns, never off the listing.
- **The retained worker is the most degenerate one.** 95% of `gpt-oss-20b`'s answers went to a
  single label against a 0.400 floor. It is kept on speed, not on behaviour, and that near-floor
  operation is a live candidate for why L4 outer cells carry SEs that swamp their deltas.
- **The optimizer, not the worker, owns the clock.** Every inner cell spends 88-108s and
  4500-4800 output tokens per optimizer call, identical across all six worker arms — 35-45% of
  each cell's wall-clock. Swapping workers cannot reach it; the dispatch package can.


| Candidate | In $/Mtok | Out $/Mtok | Δ vs current | Why queue it |
|---|---|---|---|---|
| **`nvidia/nemotron-3-nano-30b-a3b`** | $0.05 | $0.20 | +67% in / +43% out | 30B MoE with **3B active** → potentially *faster* than gpt-oss-20b's 3.6B-active path on the right provider. Default AIME-2025 is 0.992 (too high), so the question is whether toggling `reasoning: false` (OR's reasoning-on/off knob, not OpenAI's `reasoning_effort`) drops it into the 30-60% band. Architecturally different — useful diversity for the matrix even if it lands outside the band. |

- `mistralai/mistral-small-3.2-24b-instruct` — almost on par with gpt-oss-120b on accuracy, but unstable on OpenRouter.

1. `meta-llama/llama-3.3-70b-instruct:nitro`
2. `qwen/qwen3-30b-a3b-instruct-2507` — **quality signal positive (perfect 6/6 on the measured AIME subset — smartest of all tested)**, but defer for cost (verbose outputs ~2× effective cost vs Llama:nitro) and stability (OpenRouter routing timeouts). Watch for a less-verbose Qwen3 variant.
3. `google/gemma-3-27b-it` — not fully assessed; defer until OpenRouter routing stabilizes.
4. **Rejected**: `google/gemini-2.5-flash` stable, but **~8× too expensive on output tokens** even at `reasoning_effort: low`.

---

# More datasets

**Which dataset is queued next, and every recon measurement behind it** — owned by
[`dataset-selection-rationale.md`](dataset-selection-rationale.md) § Next-priority after
JustLogic. This page gains a row only when one is actually wired.
