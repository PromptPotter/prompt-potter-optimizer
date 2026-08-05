# Dataset Reasoning Matrix — Per-Dataset `pipeline.yaml` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.yaml`. Operators tune per-cycle via `campaign.json` overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-20b:nitro` | `low` | absent | Competition math. Chosen on price ($0.03/$0.14) with `:nitro` routing to the highest-throughput provider at no cost premium. Empirical A/B table below. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `hotpotqa` | `openai/gpt-oss-120b` | `medium` | absent | Multi-hop QA. Medium reasoning. |
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

## Shot-in-the-open numbers (unconfirmed; measurement parameters are unknown and might not be uniform)

| Model                      | Role guess    | AA Intel. | Speed              | TTFT       | Price in / out | Effort knob | Fit / verdict   |
| -------------------------- | ------------- | --------- | ------------------ | --------------- | ------------- | ----------------- | ------ |
| gpt-oss-120b               | baseline opt  | 33.3      | 216 tok/s          | 0.46s           | $0.15 / $0.60 | high / low / med  | |
| GLM-4.7                    | Smarter opt   | 42.1      | 79.8 tok/s         | 0.84s           | $0.60 / $2.20 | reasoning | |
| GLM-5.2                    | High-end opt  | 51        | 176 tok/s          | 1.50s           | $0.70 / $2.20 | thinking-effort | |
| Nemotron 3 Ultra           | candidate opt | unknown   | 65 tok/s           | unknown         | $0.50 / $2.20 | unknown     | |
| gpt-oss-20b                | Worker        | 61.1      | 265 tok/s          | 0.46s           | $0.03 / $0.14 | high / low | |
| inclusionai/ling-3.0-flash | Fast worker    | unknown   | vendor: 1000 tok/s peak | <100 ms claimed | roughly ¥0.40 / ¥1.20 | hybrid reasoning  | no independent benchmark yet |
| nemotron-3-nano-30b-a3b    | Agentic worker | unknown   | unknown | unknown | unknown | low  | Probably the most interesting |
| xiaomi/mimo-v2-flash       | Smart fallback worker | 39.2      | 125 tok/s  | 1.69s           | $0.00 / $0.00 pricing not stable | reasoning enabled | Much smarter than gpt-oss-20b, but not fast |


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

## Held — next-priority after JustLogic

Two recon-measured in-band candidates queued for wiring after JustLogic delivers its first cycle. Same model + effort + latency band as JustLogic — they slot into the same matrix row when wired. Full rationale + per-subtask measurements: [`dataset-selection-rationale.md`](dataset-selection-rationale.md) "Next-priority after JustLogic" section.

| Dataset (planned) | Projected model | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `planbench` *(not yet wired)* | `openai/gpt-oss-20b:nitro` (OpenRouter) | `low` | absent | PlanBench `task_1_plan_generation` (`tasksource/planbench`). Recon **36% (9/25)** at 1.5s/sample on multi-domain stratified slice (5 domains: blocksworld + logistics + 3 obfuscated). Wire-time work: PDDL plan-validator scorer (~half-day) replaces the recon's 50% action-call overlap. **Brand-new family** for the portfolio — no overlap with deduction (JustLogic), math (AIME), mixed reasoning (BBEH). |
| `naturalplan` *(not yet wired)* | `openai/gpt-oss-20b:nitro` (OpenRouter) | `low` | absent | NaturalPlan (`google-deepmind/natural-plan` raw GitHub — NOT on HF). Recon **36% macro / 43% on `meeting_planning`-only**, 0.5s/sample. **Wire path: `meeting_planning`-only** (other two subtasks: `trip_planning` floor at 0%, `calendar_scheduling` ceiling at 67%). Per-subtask scorer dispatch required (day+time-slot for calendar, joined-list overlap for meeting, token overlap for trip). |

Lower-priority subtask cuts (revisit only if PlanBench + NaturalPlan don't pan out):
- **MuSiQue `3hop`-only** — 38% on the 3hop split; substring scorer with `answer_aliases` is clean. Multi-hop RC overlap with BBEH lowers marginal-diversity value.

Rejected from this round: **AR-LSAT** (72% ceiling — solved at low) and **MuSiQue macro** (60% ceiling — 2hop coasts at 89%).
