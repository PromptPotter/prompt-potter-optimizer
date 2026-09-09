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

**What a dataset DECLARES is not what the axis SEARCHES.** `PipelineSchema.param_options` replaces these defaults with the model's own answer at run time — widening as often as narrowing — while a CAMPAIGN narrowing intersects instead, so an operator's closing still binds (`promptpotter/infrastructure/CLAUDE.md` § LLM client owns both halves). The columns above are the starting point in the literal sense: they say what the file asks for, never what the endpoint takes. Two consequences a reader of this table has to hold:

- **`reasoning_effort: none` is HTTP 400 on both `openai/gpt-oss-*` models** — *"Reasoning is mandatory for this endpoint and cannot be disabled."* Declaring it as a searchable value hands L1 a configuration that cannot be sent; `registry._MODEL_PROFILES` carries the refusal, so the rung is no longer offered.
- **A rung is a setting, not a dial.** No model measured so far orders its ladder monotonically — `medium` costs more than `high` on every one — so a candidate that moves one rung has changed the call, never scaled it.

## What the endpoints answered

Measured 2026-09-08/09 against the live endpoints with `probe-reasoning <model>`: one terse-answer prompt, reasoning tokens read off `completion_tokens_details`, flat and nested `reasoning_effort` spellings alike. Re-measure rather than trust this table — it is the provenance behind `registry._MODEL_PROFILES`, which is what the code reads.

| model | unset | `none` | minimal / low / medium / high | what the profile records |
|---|---|---|---|---|
| `openai/gpt-oss-20b` | 351 | **HTTP 400** | 89 / 75 / 549 / 135 | refuses `none`; rungs distinct (7.3× spread) |
| `openai/gpt-oss-120b` | 336 | **HTTP 400** | 79 / 45 / 528 / 405 | refuses `none`; rungs distinct (11.7× spread) |
| `qwen/qwen3.7-flash` | 1506 | 0 | 1595 / 2765 / 2169 / 2309 | rungs INDISTINCT — scatter around the default |
| `qwen/qwen3.8-flash` | 2853 | 0 | unmeasured | nothing to narrow, so it carries no row |
| `inclusionai/ling-3.0-flash` | 818 | 0 | 2350 / 2497 / 1486 / 1262 | rungs INDISTINCT; `low` overran a 3000-token cap |
| `deepseek/deepseek-v4-flash` | ~4k (tail 11.4k) | unmeasured | unmeasured | `min_max_tokens=8000` only |

The three flash models carry no `reasoning_effort` in the catalogue and honour `none` regardless, which is why the offered ladder cannot be derived from the parameter list. **An indistinct ladder is not narrowed** — every rung stays searchable and the finding is served as a caveat, so a round stops paying cells to separate two spellings of one call.

**The `min_max_tokens` floors** are first estimates from observed `reasoning_budget_exhausted` failures: 8000 catches the egregious case (`l1_critique` at 4000 → 0 content) without flagging the working nodes (`l2_context`/`l3_plan` at 8000, `checkin` at 10000, `l1_generate` at 12000).

## The Groq output ceiling

Groq enforces a per-model output ceiling — ~2048 tokens on `gpt-oss-20b` — and reasoning-trace tokens are charged against that same budget on `openai/gpt-oss-*`. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges: `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried, which trips the same trap again.

## Reading a model A/B

**Read it with `evidence --metric latency`, which serves the factorial half** — the factors a
selection varies on, the marginal at each level, and **which factors are ALIASED**, meaning they
cut the roster into the identical groups so no evidence in it can tell them apart. That last one
is the trap this section exists to stop: on five banked campaigns `agent.model` split 292.2s
against 58.5s and was aliased exactly by `dataset`, so the slow "model" was the harbor bank. A
marginal with an alias named beside it is one contrast under several headings, never several.
`--grid dataset,llm_only.model` crosses two of them for which COMBINATION leads; every other factor
is marginalised into the cells and named there. **Aliasing is what a hand-assembled roster earns** —
a panel declaring `axes:` (`inner_tasks.yaml`) generates the full product, and a full product cannot
alias. That is the reason to generate the cells rather than list them.

**A ranking is tied to the dataset's I/O shape.** JustLogic sends a long premise block and returns a short label plus a reasoning trace, so input dominates and per-Mtok *input* price carries weight it would not carry on a generate-heavy task. Rank in the operator's order: **speed, then cost, then quality** — the metric catalogue's own keys, so the order is a pick rather than a re-derivation. Accuracy from a 10-row draw is **not** comparable to the 40-row panel bank — `draw_bank` samples, so a 10-draw is a different bank, not a prefix.

Three findings from the JustLogic-d234 A/B that no catalogue would have given:

- **`:nitro` changes PRICE, not only speed.** It routes to the fastest provider and that provider sets its own rate — `xiaomi/mimo-v2.5` billed ~6× its listed $0.14/$0.28. Price an arm off the **wire** `cost_usd` the provider returns, never off the listing.
- **The retained worker is the most degenerate one.** 95% of `gpt-oss-20b`'s answers went to a single label against a 0.400 constant-answer floor. It is kept on speed, not on behaviour, and that near-floor operation is a live candidate for why L4 outer cells carry SEs that swamp their deltas.
- **The optimizer, not the worker, owns the clock.** Every inner cell spends 88-108s and 4500-4800 output tokens per optimizer call, identical across all six worker arms — 35-45% of each cell's wall-clock. Swapping workers cannot reach it; the dispatch package can.

Two arms are dead rather than slow, and both fail on the same surface: `z-ai/glm-4.7-flash` returns `content_empty` with `finish_reason=stop` and 5352 reasoning chars, triggering a schema-repair re-prompt before timing out; `inclusionai/ling-3.0-flash` answers HTTP 405 — `json_schema response format is not supported`, and every node here is schema-bearing. `GLM-5.2` is excluded by operator decision; do not add it to an arm list.

## Per-sample timings understate wall-clock

The `[ N] XX.Ys` per-sample line reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries — so summing the per-row lines understates true wall-clock whenever retries fire. A UX issue, not a correctness one; the fix belongs in the TermNorm repo.

## More datasets

**Which dataset is queued next, and every recon measurement behind it** — owned by [`dataset-selection-rationale.md`](dataset-selection-rationale.md) § The roster. This page gains a row only when one is actually wired.
