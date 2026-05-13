# Dataset Reasoning Matrix — Per-Dataset `pipeline.json` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.json`. Operators tune per-cycle via `campaign.json` overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-20b:nitro` (OpenRouter) — **confirmed 2026-05-13** | `low` | absent | Competition math. **Currently `openai/gpt-oss-20b:nitro @ low`** (swapped to `:nitro` 2026-05-13 for speed after non-:nitro slice showed ~40%+ accuracy at 10-60s/q — accuracy band hit, speed was the bottleneck). Previous pin was `meta-llama/llama-3.3-70b-instruct:nitro` (2026-05-11 four-way winner — fast+stable+cheap, "good but signal-to-noise weak at ~10% origin"); flipped because the 1/10 origin packed the candidate population too tightly to differentiate prompts. gpt-oss-20b is cheaper ($0.03/$0.14 vs ~$0.10/$0.32), and `:nitro` routes to the highest-throughput provider (Groq at ~845 tok/s) at no cost premium. `reasoning_effort` is the *headroom dial*. **Watch**: Groq enforces a ~2048-tok output ceiling that counts reasoning tokens — at `low` it's usually fine, but `finish_reason=length` on a few hard problems would prompt a per-cycle `max_tokens: 8192` override (matrix lines 21-31). Empirical A/B table at the bottom of this file. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `hotpotqa` | `openai/gpt-oss-120b` | `medium` | absent | Multi-hop QA. Medium reasoning. |
| `bbeh` | `openai/gpt-oss-20b` *(prod: 120b — see below)* | `low` | absent | "Big-Bench Extra Hard" puzzles. `low` is intentional — see Groq ceiling note below. |
| `lca-termnorm` | `openai/gpt-oss-120b` | n/a | absent (`null`) | Multi-node TermNorm pipeline; not a single-call reasoning dataset. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.json` node config — provider ceiling applies. Enforced by `tests/test_dataset_pipeline_defaults.py`.

## Groq daily-volume model swap

`openai/gpt-oss-120b` is the canonical default for all reasoning datasets. During development, when the operator's Groq daily volume on 120b is exhausted, swap the `model` field in the relevant `pipeline.json` to `openai/gpt-oss-20b` to keep iterating. Flip back to 120b for benchmarking / publication runs.

The current value committed in `datasets/bbeh/pipeline.json` may be either — treat the field as a **live operator knob**, not a fixed default.

## Why BBEH ships `reasoning_effort: low` (not `medium`/`high`)

Groq enforces a per-model output ceiling. On `gpt-oss-20b` it's ~2048 tokens. Reasoning-trace tokens are charged against this same budget on `openai/gpt-oss-*` models. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges — `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried (which trips the same trap again).

`low` keeps a chain-of-thought benefit — BBEH genuinely needs reasoning — while staying clear of the budget trap on the small model. On the larger 120b the same `low` setting works fine.

If a hard subtask still trips on either model, the operator can:
1. Override per-cycle: `pipeline_overrides.llm_only.reasoning_effort = "none"` in `campaign.json`.
2. Or override `pipeline_overrides.llm_only.max_tokens = 8192` (or higher) — but this is model-specific tuning, not a dataset default.

See `datasets/bbeh/task_description.md` for the trap description and `promptpotter/application/optimization/elimination.py` for the `classify_result()` rule table.

## AIME 2025 model A/B/C tests (2026-05-11)

Live operator A/B/C hunt for a cost+speed+quality sweet spot on AIME competition math. All routes: OpenRouter, `reasoning_effort: low`, `temperature: 0.0`, dataset 20-query origin slice (≥10 samples measured before swap).

| Model | Avg latency / query | Output tokens (avg) | Origin hits (n / measured) | Quality | Cost | Verdict |
|---|---|---|---|---|---|---|
| `meta-llama/llama-3.3-70b-instruct:nitro` ★ pinned | ~3.0s median, ~3.8s mean | ~975 | 1/10 | 2/5 ★★ | ✓ cheap, very fast (Cerebras/SambaNova routing) | **Operator's pragmatic pick (2026-05-11).** Fast+stable+cheap; weak origin (1/10) is acceptable — let L1 explore upward via prompt mutation. |
| `google/gemma-3-27b-it` | ~28s mean (n=2 completed before interrupt) | ~700 | 1/2 | insufficient data | n/a — stability blocked test | **Too unstable on OpenRouter today — defer.** ReadTimeouts + provider-side `timeout_s=60 attempt=3/3` retries; new model (April 2026 release), routing fleet not yet stabilized. Re-test in a few weeks once OpenRouter providers settle. |
| `mistralai/mistral-small-3.2-24b-instruct:nitro` | ~29s mean (n=4 before interrupt) | ~3000 (high) | 3/4 | ★★★ quality OK | $0.10/$0.30 nominal — but slow → effective cost-per-result poor | **`:nitro` flag doesn't materially help Mistral speed on OpenRouter today.** Same provider-routing flakiness as the non-:nitro variant. Quality decent, speed not. |
| `qwen/qwen3-30b-a3b-instruct-2507` | ~27s mean per [RESP], but ~65s/q tqdm rate (failed retries don't show in per-row timer) | ~1700 (high variance: 754…5782) | **6/6** on cached replay | 5/5 ★★★★★ math accuracy at origin | ✗ verbose outputs → ~2× the effective cost of Llama:nitro per sample | **Smartest of all tested models on AIME math** — perfect on the measured subset — but unstable on OpenRouter today (timeouts) **and** verbose enough to drive effective cost above the operator's ceiling. Re-evaluate when OpenRouter routing settles + watch for less verbose Qwen3 variants. |
| `openai/gpt-oss-20b` (no `:nitro`) — measured 2026-05-13 | ~10s fastest, ~60s slowest (high variance — operator-reported) | — not recorded | **~40%+** on initial slice (operator estimate; not full 20-query t-test) | ★★★★ — accuracy band hit, signal clearly above Llama:nitro's 10% baseline | ✓ $0.03 in / $0.14 out per Mtok — cheapest production option | **✅ Accuracy band hit (~40%, target ~50%) — the headroom dial worked at `low`.** Speed is the bottleneck (10-60s/q tail suggests OR routed to a slow provider like Cloudflare/Novita rather than Groq at 845 tok/s). **Action: flip to `:nitro` (same cost, pins highest-throughput provider) and re-measure speed.** |
| `openai/gpt-oss-20b:nitro` ★ **measured 2026-05-13** (speed-tier swap from non-:nitro) | **3.1-9.6s, ~5s median** (full 20-query slice, n=20, no retries observed) | ~2300 mean (range 524-6935; tail-heavy — two outliers at 6.8k/6.9k on hard combinatorics problems) | **6/20 = 30%** origin | ★★★★ — accuracy band hit (target ~50%, landed at 30% on cold slice; refusal at #013 "I'm sorry, but I can't provide…") | ✓ identical price to non-:nitro ($0.03/$0.14) — `:nitro` is routing, not a price tier | **✅ Promoted as current AIME pin 2026-05-13.** `:nitro` dropped the 10-60s tail to a tight 3-10s band; ~3× cheaper than Llama:nitro and ~3× the origin signal. Headroom is healthy (30% leaves 70% ceiling for L1 to climb). **One observed quirk**: model occasionally refuses ("I'm sorry…" at #013) — not yet a pattern. **Groq 2048-tok ceiling did NOT bite** at `low` on the full 20-query slice (no `finish_reason=length`). Next: see if any model in the same $0.03-0.10 / sub-10s band gives equal-or-better signal — candidates listed in *"Adjacent-band candidates to A/B against gpt-oss-20b:nitro"* below. |

### Adjacent-band candidates to A/B against `gpt-oss-20b:nitro` (deferred 2026-05-13)

**Status:** operator confirmed `gpt-oss-20b:nitro` as the AIME pin — speed dominates the band and 30% origin gives healthy headroom. The candidates below are *deferred*, kept as a forward reference if the optimization later needs more signal headroom (e.g. L1 plateaus too low / too high on a future cycle). No A/B planned right now.

Realistic candidates — production tier (not `:free`), priced ≤ ~$0.05 in / ~$0.20 out, fast routing available:

| Candidate | In $/Mtok | Out $/Mtok | Δ vs current | Why queue it |
|---|---|---|---|---|
| **`openai/gpt-oss-120b:nitro`** ★ first to try | **$0.039** | **$0.18** | +30% in / +29% out — closest neighbor | Same family / same OR :nitro routing → speed should be in the same 3-10s band. Bigger sibling (117B MoE, 5.1B active), known-good quality (this is the operator's pre-quota AIME default). At `reasoning_effort: low` it should land *somewhere in 50-80% AIME-2025* — probably above current 30% but plausibly still inside the useful headroom band. The minor cost bump buys real signal headroom *upward* without giving up speed. |
| **`nvidia/nemotron-3-nano-30b-a3b`** | $0.05 | $0.20 | +67% in / +43% out | 30B MoE with **3B active** → potentially *faster* than gpt-oss-20b's 3.6B-active path on the right provider. Default AIME-2025 is 0.992 (too high), so the question is whether toggling `reasoning: false` (OR's reasoning-on/off knob, not OpenAI's `reasoning_effort`) drops it into the 30-60% band. Architecturally different — useful diversity for the matrix even if it lands outside the band. |
| `openai/gpt-oss-20b:free` | $0 | $0 | strictly cheaper | Free-tier slug, same weights → same 30% AIME, same routing band *if* OR doesn't shunt :free to slower providers. Catch: rate-limited to ~20 req/min, 200/day — fine for a 20-query reconnaissance slice, won't sustain a multi-round campaign. Cost-extreme reference only. |

**Suggested A/B order** (operator-pick, but the path of least surprise): `gpt-oss-120b:nitro` → if signal too high, drop `reasoning_effort` to `none`; if speed degrades vs 20b → revert. Then `nemotron-3-nano` if 120b doesn't dominate. Models *not* queued and why: `mistralai/mistral-small-3.2-24b-instruct` (already tested 2026-05-11, unstable on OR); `meta-llama/llama-4-scout` ($0.08/$0.30, AIME ≤ Maverick's 0.048 likely); `qwen/qwen3-30b-a3b-instruct-2507` (already tested, verbose); `x-ai/grok-4.1-fast` ($0.20/$0.50, AIME 0.893 — out of band on price *and* too smart for headroom).

**Per-row timing caveat observed during these tests.** The `[ N] XX.Ys` per-sample line in the PromptPotter CLI reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries. The tqdm ETA (`X.YYs/q`) is the honest aggregate. If you see tqdm's `s/q` rate >> the displayed per-row durations, retries are eating your wall-clock silently. Filed as a UX issue, not a correctness issue.

**Operator-recalled baselines (not re-measured this session):**
- `mistralai/mistral-small-3.2-24b-instruct` — operator's prior favorite, "almost on par with gpt-oss-120b" on accuracy, but observed unstable on OpenRouter routing.
- `google/gemini-2.5-flash` — stable, but **~8× too expensive on output tokens** even at `reasoning_effort: low` (verbose thinking traces). Abandoned.
- `openai/gpt-oss-120b` (Groq, `reasoning_effort: high`) — original AIME default; hit Groq daily quota, prompted the OpenRouter migration.

**Operator preference ranking (2026-05-11):**
1. **`mistralai/mistral-small-3.2-24b-instruct`** and **`meta-llama/llama-3.3-70b-instruct:nitro`** — tied first (the former for quality, the latter for stability+speed; chase whichever the workload allows).
2. `openai/gpt-oss-120b` (Groq when quota available; or OpenRouter route as fallback) — known-good quality fallback.
3. `qwen/qwen3-30b-a3b-instruct-2507` — **quality signal positive (perfect 6/6 on the measured AIME subset — smartest of all tested)**, but defer for cost (verbose outputs ~2× effective cost vs Llama:nitro) and stability (OpenRouter routing timeouts). Watch for a less-verbose Qwen3 variant.
4. `google/gemma-3-27b-it` — not fully assessed; defer until OpenRouter routing stabilizes (new model, April 2026).
5. **Rejected**: `google/gemini-2.5-flash` (cost-prohibitive on output tokens).

Update each row as the test completes. Once a winner emerges, promote it to the top-of-file dataset table and prune this section.
