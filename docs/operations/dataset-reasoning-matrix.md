# Dataset Reasoning Matrix — Per-Dataset `pipeline.yaml` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.yaml`. Operators tune per-cycle via overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-20b:nitro` | `low` | absent | Competition math. Chosen on price ($0.03/$0.14) with `:nitro` routing to the highest-throughput provider at no cost premium. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `bbeh` | `mistralai/mistral-small-3.2-24b-instruct` (openrouter) | `low` | absent | "Big-Bench Extra Hard" puzzles. `low` is intentional — the rationale in `task_description.md` is written against Groq's `gpt-oss-20b` output ceiling, which is where the dataset's screening numbers were taken; `available_models` now admits this model only. |
| `justlogic-d234` | `openai/gpt-oss-20b:nitro` | `low` | absent | JustLogic (Chen 2025), 3-class deductive reasoning. iid random mix of depths 2, 3, 4 (200/depth from HF `train`, seed=42, interleaved). Each depth cut is a separate dataset name sharing no cache key with another — never compare across cuts (`datasets/CLAUDE.md` § L4). |
| `lca-termnorm` | `openai/gpt-oss-120b` | n/a | absent (`null`) | Multi-node TermNorm pipeline; not a single-call reasoning dataset. |
| `lca-bom-termnorm` | `entity_profiling` → `openai/gpt-oss-20b` | `low` (entity_profiling) | absent (`null`) | Tenant material-matching pipeline (`web_search → entity_profiling → token_matching`, no `llm_ranking`). `entity_profiling` emits **native** `json_schema` and pins `reasoning_effort: low` — the cap is load-bearing, see § The Groq output ceiling. Multi-node, so the single-call columns describe the profiling node only. Tenant config on disk, gitignored. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.yaml` node config — the provider ceiling applies. Held by convention, not by a test, so check the overlay rather than assuming.

**The floor default for a new dataset** is `openai/gpt-oss-20b:nitro @ low` via OpenRouter — cheapest, fastest, and it leaves L1 headroom.

## The Groq output ceiling

Groq enforces a per-model output ceiling — ~2048 tokens on `gpt-oss-20b` — and reasoning-trace tokens are charged against that same budget on `openai/gpt-oss-*`. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges: `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried, which trips the same trap again.

## Reading a model A/B

**A ranking is tied to the dataset's I/O shape and must be re-run per dataset.** JustLogic sends a long premise block and returns a short label plus a reasoning trace, so input dominates and per-Mtok *input* price carries weight it would not carry on a generate-heavy task. Rank in the operator's order: **speed, then cost, then quality.** Accuracy from a 10-row draw is **not** comparable to the 40-row panel bank — `draw_bank` samples, so a 10-draw is a different bank, not a prefix.

Three findings from the JustLogic-d234 A/B that no catalogue would have given:

- **`:nitro` changes PRICE, not only speed.** It routes to the fastest provider and that provider sets its own rate — `xiaomi/mimo-v2.5` billed ~6× its listed $0.14/$0.28. Price an arm off the **wire** `cost_usd` the provider returns, never off the listing.
- **The retained worker is the most degenerate one.** 95% of `gpt-oss-20b`'s answers went to a single label against a 0.400 constant-answer floor. It is kept on speed, not on behaviour, and that near-floor operation is a live candidate for why L4 outer cells carry SEs that swamp their deltas.
- **The optimizer, not the worker, owns the clock.** Every inner cell spends 88-108s and 4500-4800 output tokens per optimizer call, identical across all six worker arms — 35-45% of each cell's wall-clock. Swapping workers cannot reach it; the dispatch package can.

Two arms are dead rather than slow, and both fail on the same surface: `z-ai/glm-4.7-flash` returns `content_empty` with `finish_reason=stop` and 5352 reasoning chars, triggering a schema-repair re-prompt before timing out; `inclusionai/ling-3.0-flash` answers HTTP 405 — `json_schema response format is not supported`, and every node here is schema-bearing. `GLM-5.2` is excluded by operator decision; do not add it to an arm list.

## Per-sample timings understate wall-clock

The `[ N] XX.Ys` per-sample line reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries — so summing the per-row lines understates true wall-clock whenever retries fire. A UX issue, not a correctness one; the fix belongs in the TermNorm repo.

## More datasets

**Which dataset is queued next, and every recon measurement behind it** — owned by [`dataset-selection-rationale.md`](dataset-selection-rationale.md) § The roster. This page gains a row only when one is actually wired.
