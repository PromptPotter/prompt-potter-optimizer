# Dataset Reasoning Matrix — Per-Dataset `pipeline.yaml` Defaults

Single canonical view of the model + reasoning_effort + max_tokens defaults shipped with each dataset's `pipeline.yaml`. Operators tune per-cycle via overrides; this table is the **starting point**, not the only valid setting.

| Dataset | model (default) | `reasoning_effort` | `max_tokens` | Notes |
|---|---|---|---|---|
| `aime_2025` | `openai/gpt-oss-20b:nitro` | model floor | absent | Competition math. Chosen on price ($0.03/$0.14) with `:nitro` routing to the highest-throughput provider at no cost premium. |
| `gsm8k` | `openai/gpt-oss-120b` | `medium` | absent | Grade-school math word problems. Medium reasoning is enough. |
| `bbeh` | `mistralai/mistral-small-3.2-24b-instruct` (openrouter) | `low` | absent | "Big-Bench Extra Hard" puzzles. `low` is intentional — the rationale in `task_description.md` is written against Groq's `gpt-oss-20b` output ceiling, which is where the dataset's screening numbers were taken; `available_models` now admits this model only. |
| `justlogic-d234` | `openai/gpt-oss-20b:nitro` | model floor | absent | JustLogic (Chen 2025), 3-class deductive reasoning. iid random mix of depths 2, 3, 4 (200/depth from HF `train`, seed=42, interleaved). Each depth cut is a separate dataset name sharing no cache key with another — never compare across cuts (`datasets/CLAUDE.md` § L4). |
| `justlogic-d234-held` | `openai/gpt-oss-20b:nitro` | model floor | absent | `justlogic-d234`'s pin, verbatim; the cut is `datasets/justlogic-d234-held/dataset.md`'s. |
| `lca-termnorm` | `openai/gpt-oss-120b` | n/a | absent (`null`) | Multi-node TermNorm pipeline; not a single-call reasoning dataset. |
| `lca-bom-termnorm` | `entity_profiling` → `openai/gpt-oss-20b` | `low` (entity_profiling) | absent (`null`) | Tenant material-matching pipeline (`web_search → entity_profiling → token_matching`, no `llm_ranking`). `entity_profiling` emits **native** `json_schema` and pins `reasoning_effort: low` — the cap is load-bearing, see § The Groq output ceiling. Multi-node, so the single-call columns describe the profiling node only. Tenant config on disk, gitignored. |
| `spreadsheetbench-s10` | `inception/mercury-2.5` (agent) | unset | `4096` (a spend limit) | Harbor agent episode: the prompt is an injected `SKILL.md`. The agent model is chosen under § The agent model on a Harbor dataset. |
| `spreadsheetbench-s20` | `qwen/qwen3.7-flash` (agent, `route_order: [alibaba]`) | `none` | `4096` (a spend limit) | s10's episode on twenty tasks, the search panel; origin on all twenty, fifteen per candidate per round. Pinned to the configuration the Qwen campaign on s10 ran its origin with, so their shared cells replay. |
| `sealqa-longseal-12` | `qwen/qwen3.7-flash:nitro` (agent) | unset | `4096` (a spend limit) | Harbor agent episode, `max_turns: 4`, graded by a `gpt-oss-120b` judge. Same selection section. |
| `reactome-typeql-42` | `xiaomi/mimo-v2.6-flash`, pinned to `xiaomi` | `none` | `8192` (upstream's reply cap) | TypeDB's query-generation benchmark: the model writes TypeQL, the harness executes it and feeds errors back. The development model only, chosen on price in [`model-screens.md`](../../datasets/reactome-typeql-42/model-screens.md) § One run per question, three models. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.yaml` node config — the provider ceiling applies. Held by convention, not by a test, so check the overlay rather than assuming. **A Harbor agent node is the exception:** there `max_tokens`, `max_input_tokens` and `max_turns` are the limits we send the agent, and the only thing its cell's spend is bounded by — leave one out and no cell runs under a spend ceiling (`connectors/harbor.py::_sent_spend_bound`).

**The default for a new dataset** is `openai/gpt-oss-20b:nitro` via OpenRouter with no `reasoning_effort` — cheapest, fastest, and it leaves L1 headroom.

**"model floor" is a rung the MODEL decides, never the file.** A declared `reasoning_effort` no layer sets resolves to the lowest rung (`none` < `minimal` < `low` < `medium` < `high`) the running model's capability answer offers — `low` on `openai/gpt-oss-*`, which refuses `none`; `none` on a model that takes it. A model no layer answers for has no floor, and the field is omitted. A rung spelled in the file is an explicit pin (`gsm8k`, `bbeh`) and follows no model a campaign swaps in.

**What a dataset DECLARES is not what the axis SEARCHES.** `PipelineSchema.param_options` replaces these defaults with the model's own answer at run time — widening as often as narrowing — while a CAMPAIGN narrowing intersects instead, so an operator's closing still binds (`promptpotter/infrastructure/CLAUDE.md` § LLM client owns both halves). The columns above are the starting point in the literal sense: they say what the file asks for, never what the endpoint takes. Two consequences a reader of this table has to hold:

- **`reasoning_effort: none` is HTTP 400 on both `openai/gpt-oss-*` models** — *"Reasoning is mandatory for this endpoint and cannot be disabled."* Declaring it as a searchable value hands L1 a configuration that cannot be sent; `registry._MODEL_PROFILES` carries the refusal, so the rung is no longer offered.
- **A rung is a setting, not a dial.** No model measured so far orders its ladder monotonically — `medium` costs more than `high` on every one — so a candidate that moves one rung has changed the call, never scaled it.

## What the endpoints answered

**What each model's endpoint refuses, and which of its rungs are indistinct, is `registry._MODEL_PROFILES`** — that table is what the code reads, and a row is earned with `probe-reasoning <model>`: one terse-answer prompt, reasoning tokens read off `completion_tokens_details`, flat and nested `reasoning_effort` spellings alike. Re-probe rather than trust a remembered reading; a model with nothing to narrow carries no row.

The flash models carry no `reasoning_effort` in the catalogue and honour `none` regardless, which is why the offered ladder cannot be derived from the parameter list. **An indistinct ladder is not narrowed** — every rung stays searchable and the finding is served as a caveat, so a round stops paying cells to separate two spellings of one call.

**The `min_max_tokens` floors** are first estimates from observed `reasoning_budget_exhausted` failures: 8000 catches the egregious case (`l1_critique` at 4000 → 0 content) without flagging the working nodes (`l2_context`/`l3_plan` at 8000, `checkin` at 10000, `l1_generate` at 12000).

## The Groq output ceiling

Groq enforces a per-model output ceiling — ~2048 tokens on `gpt-oss-20b` — and reasoning-trace tokens are charged against that same budget on `openai/gpt-oss-*`. With `reasoning_effort: medium` on a hard BBEH puzzle the model burns 8000+ chars of internal reasoning and runs out of budget before any visible content emerges: `finish_reason=length`, empty content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, the result is deprecated and retried, which trips the same trap again.

## Reading a model A/B

**Read it with `evidence --metric latency`, which serves the factorial half** — the factors a
selection varies on, the marginal at each level, and **which factors are ALIASED**, meaning they
cut the roster into the identical groups so no evidence in it can tell them apart. That last one
is the trap this section exists to stop: an `agent.model` split that `dataset` aliases exactly is
a slow bank read as a slow model. A
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

## The agent model on a Harbor dataset

On a Harbor dataset the model runs inside an agent episode (`terminus-2`), and the candidate prompt reaches it only as an injected `SKILL.md` that the agent must choose to open. Choosing that model follows its own rules, which are operator decisions from 2026-09-16:

- **Two delivery modes, and a result names its mode.**
  - **Advanced, the default and today's working mode:** the agent must open the skill file itself. If a model never opens `SKILL.md`, its score says nothing about what the optimizer changed, however high the score is, so read the `skill_opened` observation before the score.
  - **Primitive:** the literature's setting, kept for the one-to-one comparison. WikiSkill injects the whole skill into the agent's system prompt; SkillOpt prepends it to the context. Under this mode, not opening the skill does not disqualify a model.
  - Every row below was measured in the advanced mode.
- **Rank by speed, then headroom, then cost.**
  - **Speed:** rank on the agent phase (`step_phases.agent_execution`), never on a cell's total wall clock, which the verifier dominates.
  - **Headroom:** a model that solves 9–10 of 10 at the origin leaves the optimizer nothing to find.
  - **Cost:** paying 1.5–2.7× the prompt-optimization default is acceptable if it buys speed. The ceiling is 3× `gpt-oss-20b` ($0.225/$0.90 per M tokens).
- **Two models are banned from every agent and LLM pipeline on cost:** `google/gemini-3.5-flash` and `qwen/qwen3.6-27b`. A model that costs an order of magnitude more per cell than the current candidates is not screened at all.
- **Pin one host per model, and probe hosts first.** The same weights ran at 11.5 tok/s on one host and 120 tok/s on another (`qwen/qwen3.5-9b` on DeepInfra vs. Venice). A screen pins each model to one host through `route_order`, so every result names who served it.

### Pilot: `spreadsheetbench-s10`, all ten cells

Measured 2026-09-16, origin only, one pinned host per model. Column meanings:
- **Agent s:** median agent-phase seconds.
- **$/cell:** median of the `cost_usd` the provider returned, not the list price.
- **Opened:** cells where the agent opened `SKILL.md`.
- **Solved:** cells solved, out of the cells that got a grade.

The WikiSkill paper's models are marked †. Qwen-3.5-4B is not served on OpenRouter. Gemma-4-31B's screen was cut off by the key's daily spend limit before any cell was graded; its fastest host measured 34 tok/s.

| Model | Host | Effort | Agent s | $/cell | Opened | Solved | Verdict |
|---|---|---|---|---|---|---|---|
| `inception/mercury-2.5` | inception | default | 41 | 0.0020 | 8/10 | 4/9 | **Pick.** The fastest model that passes every bar. One cell lost its grade to a provider APIError. |
| `qwen/qwen3.7-flash` | alibaba | `none` | 66 | 0.0013 | 10/10 | 6/10 | Headroom and price are fine; too slow. |
| `z-ai/glm-5.3-flash` | baseten/fp8 | `low` | 62 | 0.0045 | 10/10 | 8/10 | The host throttled it (HTTP 429), and one cell hit the 600 s agent timeout. |
| `deepseek/deepseek-v4-flash-0731` | wafer/fast | `none` | 55 | 0.0042 | 6/10 | 9/10 | Saturated, and often skips the skill. |
| † `google/gemini-3.5-flash` | google-ai-studio | `low` | 37 | 0.053 | 1/10 | 9/10 | **Banned.** Costs 26× the pick. Also saturated, and solves without opening the skill. |
| † `qwen/qwen3.6-27b` | alibaba | `low` | 83 | 0.024 | 10/10 | 8/10 | **Banned.** Costs 12× the pick. Also slow, because the model writes a lot rather than because of the host. |
| † `qwen/qwen3.5-9b` | venice/fp8 | `low` | 157 | 0.0090 | 8/10 | 4/8 | Too slow, at 4.5× the pick's cost. The daily spend limit cost two cells their grades. |
| `qwen/qwen3.6-35b-a3b` | venice/fp8 | `low` | 29 | 0.0096 | 2/2 | 1/2 | Fast and opens the skill, but costs 5× the pick. The daily spend limit ended it after two graded cells, too few to rank it. |

**Dropped after two cells** (`10452`, `105-24`): `openai/gpt-oss-120b` on groq (gave up after 2 turns, solved 0 of 2); `google/gemini-2.5-flash-lite` and `mistralai/mistral-small-2603` (never opened the skill); `poolside/laguna-s-2.1` (hit the 20-turn cap); `qwen/qwen3.8-flash` at `low` (129 s); `deepseek/deepseek-v4.1-flash` on fireworks (70 s and 579 s).

**Dropped in the first screen, which ran before the harness fixes:** never opened the skill — `gpt-oss-20b:nitro` (the only model near 30 s, at 13 s), `gpt-oss-120b:nitro` (19 s), `mistral-nemo`; too slow — `gpt-5-nano` (219–330 s), `nemotron-3.5-lightning` (166 s), `nemotron-3-nano` (104 s); hit the 20-turn cap at about 5× the cost — `ministral-3b`, `nova-micro`.

### Pilot: `sealqa-longseal-12`, all twelve cells

Measured 2026-09-16, origin only, with the same hosts as above. **Correct** comes from the campaign's judges. The trial's own reward only says whether the episode left an answer, so it reads 1.0 on almost every cell.

| Model | Effort | Agent s | $/cell | Opened | Correct |
|---|---|---|---|---|---|
| `inception/mercury-2.5` | default | 16 | 0.0011 | 0/12 | 1/12 |
| `qwen/qwen3.7-flash` | `none` | 42 | 0.0004 | 3/12 | 1/9 (the host's rate limit left three cells without a grade) |

No other arm produced a graded cell. With `max_turns: 4`, both models answer from the documents in two to four turns, mostly without reading the skill. In the advanced mode an optimized skill therefore barely reaches the model on this dataset, and both models sit at the floor. SealQA is a dataset for the primitive mode.

What the pilot established:

- **Cheap, fast models do not open a skill they have to go and read.** Only models that open it can be optimized in skill mode, so a model's speed in this table matters only once it opens the skill.
- **litellm silently drops `reasoning_effort` for every `:nitro` name and for any model it does not know**, so two effort arms can send the same request. The harbor connector sends the effort as `extra_body.reasoning` instead (`connectors/harbor.py::_REASONING_CHANNEL`).
- **Screen results are summaries; the campaigns behind them are disposable.** These tables are the record kept after the campaigns and traces are deleted. Re-measure before relying on any row: hosts change their throughput and prices from week to week.

### What the optimization runs on these benchmarks established

The campaigns were retired and their artifacts kept locally in `.scratch/retired-campaigns-2026-09-24/` (`digest.md` is one line per cycle); what they decided:

- **A ten-cell cut is used up in two promotions.** `inception/mercury-2.5` on `spreadsheetbench-s10` (advanced mode) took the overlap line from 4 to 6 to 8 of the same ten cells in two rounds and found no winner in the four after. Neither promotion was separable on its own — both intervals include zero, so `rounds_to_separable` stayed unset while `rounds_to_improved` was 1. A further campaign needs a larger cut, which is what `spreadsheetbench-s20` is.
- **Headroom alone is not enough.** `qwen/qwen3.7-flash` at `none` found nothing that beat the origin on `spreadsheetbench-s10` or on `-s20`.
- **SealQA's floor moves under an edit to `task_intent` and `instruction`** — decomposing the question's constraints and verifying each one, aimed at misread temporal ordinals (`qwen/qwen3.7-flash:nitro`, twenty cells). No lift interval was stamped, so it is a level, not a separable promotion.
- **On a label-mapping upload the lever is the label list, then the catch-all** (`swiss-invoices-eval`, a tenant upload: 20 invoices onto 25 account codes): list every code with a one-line description, then force an explicit category match before the catch-all. `openai/gpt-oss-20b` at `low` was the best-value arm, every lift interval clear of zero; at `high` it reached a perfect score whose last step was not separable.

## The model on an executed-query dataset

On `reactome-typeql-42` the model writes a query, the harness runs it, and a rejected query comes back with its error for another attempt. Every attempt re-sends the whole prompt, about 16k tokens, and replies are a few hundred. So the input price sets the cost, and a model that reasons before answering multiplies the clock without a matching gain.

- **Turn reasoning off where the model allows it.** `qwen/qwen3.8-flash` at `low` and `xiaomi/mimo-v2.6-flash` at its default each took about two minutes per call and were stopped unscored; with `none` both answer in seconds. `z-ai/glm-5.3-flash` refuses `none` with HTTP 400, as `openai/gpt-oss-*` does, and runs at `low`.
- **A model's listed price is its cheapest host's.** `deepseek/deepseek-v4.1-flash` lists at $0.003 per M input tokens, which is one host of thirty; the others charge $0.025 to $0.45. A cost is only known for a pinned host or from the bill.
- **This is the development model only.** The published rows are Claude Sonnet 5 and DeepSeek V4 Pro, and the reported cells run on the operator's pick.

The screens, their bills and the hour each was read are the dataset's own record: [`datasets/reactome-typeql-42/model-screens.md`](../../datasets/reactome-typeql-42/model-screens.md).

## Pick the model for what the run does

No one model is the development model for every kind of run on a dataset. Two things decide which fits, and both are read from a model screen, on any dataset:

**Why the model is cheap.** The three models screened on `reactome-typeql-42` are cheap for three different reasons, and each reason holds only for some runs.

| Model | What makes it cheap | Holds when | Breaks when |
|---|---|---|---|
| `xiaomi/mimo-v2.6-flash` on `xiaomi` | The prompt cache: almost all its input bills at the cached price. | Many calls share one prompt on one host, as within one candidate of a search. | The prompt changes between calls. Every ablation arm and every new candidate starts cold, at up to the list price. |
| `deepseek/deepseek-v4.1-flash` on `relace` | Input costs almost nothing ($0.003 per M tokens), cached or not. The bill is nearly all output, at $2.40 per M. | The prompt keeps changing, since no cache is needed. | The model writes long replies or many retries. |
| `z-ai/glm-5.3-flash` on `relace` | A moderate input price with no cache discount. | The prompt is short, or the run is about shortening it: a shorter prompt shows up directly as a lower bill. | The prompt is long and sent many times. |

**How much room the accuracy leaves, and in which direction.** A question a model already misses cannot register harm, and one it already gets right cannot register a gain.

- **A search needs room to climb.** Use a model that misses a fair share of the questions, so a better prompt has something to win.
- **An ablation needs room to fall.** Use a model that gets most questions right, so a removed section it needed shows as a miss. On the twelve questions `mimo` already misses every hard one, so a removal can only show damage on the seven easier ones; `glm` gets eleven right, so nearly every question can show it.
- **A reported comparison uses neither rule.** It runs on the model the published rows used, or the operator's pick.

## Per-sample timings understate wall-clock

The `[ N] XX.Ys` per-sample line reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries — so summing the per-row lines understates true wall-clock whenever retries fire. A UX issue, not a correctness one; the fix belongs in the TermNorm repo.

## More datasets

**Which dataset is queued next, and every recon measurement behind it** — owned by [`dataset-selection-rationale.md`](dataset-selection-rationale.md) § The roster. This page gains a row only when one is actually wired.
