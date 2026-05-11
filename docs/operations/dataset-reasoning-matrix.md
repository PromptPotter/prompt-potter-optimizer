# Dataset Reasoning Matrix — Per-Dataset `pipeline.json` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.json`. Operators tune per-cycle via `campaign.json` overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `meta-llama/llama-3.3-70b-instruct:nitro` (OpenRouter) | `low` | absent | Competition math. Settled on Llama:nitro on 2026-05-11 after a four-way OpenRouter test (Llama:nitro / Gemma-27b / Qwen3-30b-a3b / Mistral-small-24b:nitro). Llama is fast+stable+cheap; weak 1/10 origin is intentional headroom for L1 to explore upward. Empirical table at the bottom of this file. |
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
