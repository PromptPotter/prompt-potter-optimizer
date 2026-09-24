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
| `spreadsheetbench-s10` | `qwen/qwen3.7-flash:nitro` (agent) | unset | `4096` (a spend limit) | Harbor agent episode: the prompt is an injected `SKILL.md`. The agent model is chosen under § The agent model on a Harbor dataset. |
| `spreadsheetbench-s20` | `qwen/qwen3.7-flash` (agent, `route_order: [alibaba]`) | `none` | `4096` (a spend limit) | s10's episode on twenty tasks, the search panel; origin on all twenty, fifteen per candidate per round. Pinned to the configuration the Qwen campaign on s10 ran its origin with, so their shared cells replay. |
| `sealqa-longseal-12` | `qwen/qwen3.7-flash:nitro` (agent) | unset | `4096` (a spend limit) | Harbor agent episode, `max_turns: 4`, graded by a `gpt-oss-120b` judge. Same selection section. |

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.yaml` node config — the provider ceiling applies. Held by convention, not by a test, so check the overlay rather than assuming. **A Harbor agent node is the exception:** there `max_tokens`, `max_input_tokens` and `max_turns` are the limits we send the agent, and the only thing its cell's spend is bounded by — leave one out and no cell runs under a spend ceiling (`connectors/harbor.py::_sent_spend_bound`).

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

**Dropped after two cells** (`10452`, `105-24`):

| Model | Host | Why dropped |
|---|---|---|
| `openai/gpt-oss-120b` | groq | Gave up after 2 turns; solved 0 of 2. |
| `google/gemini-2.5-flash-lite` | — | Never opened the skill. |
| `mistralai/mistral-small-2603` | — | Never opened the skill. |
| `poolside/laguna-s-2.1` | — | Hit the 20-turn cap. |
| `qwen/qwen3.8-flash` (`low`) | — | 129 s. |
| `deepseek/deepseek-v4.1-flash` | fireworks | 70 s and 579 s. |

**Dropped in the first screen, which ran before the harness fixes:**
- **Never opened the skill:**
  - `gpt-oss-20b:nitro`, the only model near 30 s (13 s);
  - `gpt-oss-120b:nitro` (19 s);
  - `mistral-nemo`.
- **Too slow:**
  - `gpt-5-nano` (219–330 s);
  - `nemotron-3.5-lightning` (166 s);
  - `nemotron-3-nano` (104 s).
- **Hit the 20-turn cap at about 5× the cost:**
  - `ministral-3b`;
  - `nova-micro`.

### Pilot: `sealqa-longseal-12`, all twelve cells

Measured 2026-09-16, origin only, with the same hosts as above. **Correct** comes from the campaign's judges. The trial's own reward only says whether the episode left an answer, so it reads 1.0 on almost every cell.

| Model | Effort | Agent s | $/cell | Opened | Correct |
|---|---|---|---|---|---|
| `inception/mercury-2.5` | default | 16 | 0.0011 | 0/12 | 1/12 |
| `qwen/qwen3.7-flash` | `none` | 42 | 0.0004 | 3/12 | 1/9 (the host's rate limit left three cells without a grade) |

The other arms produced no graded cell. Gemini-3.5-Flash's calls each reserved 65k output tokens, and OpenRouter refused them for lack of credit (HTTP 402). The rest hit the key's daily spend limit.

With `max_turns: 4`, both models answer from the documents in two to four turns, mostly without reading the skill. In the advanced mode an optimized skill therefore barely reaches the model on this dataset, and both models sit at the floor. SealQA is a dataset for the primitive mode.

What the pilot established:

- **Cheap, fast models do not open a skill they have to go and read.** Only models that open it can be optimized in skill mode, so a model's speed in this table matters only once it opens the skill.
- **litellm silently drops `reasoning_effort` for every `:nitro` name and for any model it does not know.** Until `harbor_wire_adapter` moved the effort to `extra_body.reasoning` (`_REASONING_CHANNEL`), a screen's `low` and `none` arms sent the same request.
- **Screen results are summaries; the campaigns behind them are disposable.** This table is the record kept after the campaigns and traces are deleted. Re-measure before relying on any row: hosts change their throughput and prices from week to week.

### Optimization: `inception/mercury-2.5` on `spreadsheetbench-s10`, advanced mode

Measured 2026-09-16: six rounds, two variants per round, `deepseek/deepseek-v4-flash:nitro` as the optimizer model. The run stopped at `max_rounds`.

| Round | Winner | Solved | θ | Lift over parent (95% CI) |
|---|---|---|---|---|
| 0 | C0, the origin | 4/10 | −1.04 | — |
| 1 | C1.1 | 6/10 | −0.19 | +0.2 (−0.10 to +0.50) |
| 2 | C2.1 | 8/10 | +0.91 | +0.2 (−0.10 to +0.50) |
| 3–6 | no winner | — | +0.91 | — |

- **What the claim rests on.** The overlap line compares the winners on the same ten cells: C0 solves 4, C1.1 solves 6, C2.1 solves 8. Neither promotion is separable on its own, because both intervals include zero, so `rounds_to_separable` stays unset while `rounds_to_improved` is 1.
- **What changed.** The winning prompt adds two checks to the origin's instruction. Before writing a transformation, the agent traces the first row against the expected result. After each formula, it reopens the saved workbook, reads the evaluated value, and confirms that every referenced column exists.
- **Cost:** $0.39 billed, of which $0.37 was the agent and $0.03 the optimizer. Priced with cache hits included, it comes to $0.64.
- **Time:** 2 h 41 min wall clock, of which 2 h 34 min was cell scoring; each round took 26–38 min.
- **What comes next.** The ten-cell cut is nearly used up: the winner solves eight cells and leaves two to win, so a further campaign needs a larger cut.

### Optimization: the other arms on the same benchmarks

Measured 2026-09-16 to 2026-09-19; the campaigns were retired on 2026-09-24 and their artifacts
kept locally in `.scratch/retired-campaigns-2026-09-24/` (`digest.md` is one line per cycle).

| Dataset | Agent model | Rounds | Origin → best | Cost | Reading |
|---|---|---|---|---|---|
| `sealqa-longseal-12` (20 cells) | `qwen/qwen3.7-flash:nitro` | 4 | 0.075 → 0.50–0.55 (r2–r3) | $0.20 | **The floor moves.** The winner rewrote `task_intent` and `instruction` toward decomposing the question's constraints and verifying each one, aimed at misread temporal ordinals. No lift interval was stamped, so it is a level, not a separable promotion. A second run of the same arm reached 0.45 in two rounds before diverging. |
| `spreadsheetbench-s10` | `qwen/qwen3.7-flash`, `none` | 7 | 0.70 → no winner | $0.34 | Six rounds of candidates at 0.0–0.7; nothing beat the origin. Headroom alone was not enough. |
| `spreadsheetbench-s10` | `inception/mercury-2.5` | 3 | 0.70 → 0.70 | $0.24 | Stopped on provider throttling; lift +0.00 (−0.34 to +0.34). |
| `spreadsheetbench-s20` (20 cells) | `qwen/qwen3.7-flash`, `none` | 3 | 0.65 → no winner | $0.37 | Candidates 0.40–0.53; stopped when the backend became unreachable. |

**`swiss-invoices-eval`** (a tenant upload, not in `datasets/`; 20 invoices, map each to one of 25
account codes). Seven campaigns, `llm_only`, all starting at 0.05–0.10:

- **The lever is the code list, then the catch-all.** The origin names a few codes; the first
  winning edit lists all 25 with one-line descriptions, and the next forces an explicit
  category match before the `6500` catch-all, whose "last resort" wording the winner removes.
  Service expenses (cleaning, freight, marketing, travel, bank fees) are where `6500` absorbs errors.
- **`openai/gpt-oss-20b`, `low`:** 0.65–0.70 after one or two rounds for about $0.03, lift +0.40
  to +0.55 with every interval clear of zero — the best value arm.
- **`upstage/solar-pro4`, `low`:** 0.75 by round 2, lift +0.40 (+0.12 to +0.68), $0.07.
- **`openai/gpt-oss-20b`, `high`:** 1.00 at round 9 (`perfect_score`), $0.08 — but the last step's
  lift is +0.05 (−0.05 to +0.15), so the final climb is not separable.
- **`inclusionai/ling-3.0-flash`:** 0.40, lift not separable.

## Per-sample timings understate wall-clock

The `[ N] XX.Ys` per-sample line reports only the duration of the **successful** backend HTTP call, not cumulative wall-clock including retries — so summing the per-row lines understates true wall-clock whenever retries fire. A UX issue, not a correctness one; the fix belongs in the TermNorm repo.

## More datasets

**Which dataset is queued next, and every recon measurement behind it** — owned by [`dataset-selection-rationale.md`](dataset-selection-rationale.md) § The roster. This page gains a row only when one is actually wired.
