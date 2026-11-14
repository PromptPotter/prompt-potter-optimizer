# Datasets — which ones, which model, how to add one

The measurement roster, the admission bar and the order of use are [`../research/benchmarks.md`](../research/benchmarks.md)'s (§ Every dataset we measured, § The admission bar, § Order of use). A dataset's model pin is its own `{dataset}/dataset.yaml::pipeline`; this page holds the rules choosing it.

## Selection criteria

A focus dataset for L1 optimizer prompt evolution:

1. **Origin in band** — the band, and how far to trust it, is [`../research/benchmarks.md`](../research/benchmarks.md) § The admission bar's.
1b. **Origin clears its CONSTANT-ANSWER floor** — a stub emitting the commonest label, which every origin round serves paired against the origin on its cells (`RoundHealth.floor`, `warn` where the floor is above).
2. **Reachable ceiling** of 50–75% under strong prompt engineering.
3. **N ≥ 400, preferably 800+**; smaller N needs per-subtask stratification.
4. **Multiple distinct subtask categories** — each an independent L1 lever.
5. **Deterministic per-sample grading** (exact match, MC, F1, regex). An LLM judge is admissible only through `promptpotter/application/scoring/judges/`, only where no matcher can grade, validated against the published grader before a number is quoted.
6. **No mode-collapse on one gold class** — a modal answer over 40% of gold inflates the origin; stratify by `prop` / `category` / `subtask` first.
7. **HF-loadable.** 8. **Contamination-resistant** — 2024+ or synthetic. 9. **Anchored by measurement** — a 25-sample recon on `gpt-oss-20b @ low` is the verdict.

Toy and demo datasets are picked on deliberation length: prefer one-pass tasks; read `reasoning_tokens` against `output_tokens` in `dashboard.json::spend.by_kind.backend` on the first round. The pin holds because these datasets leave `model` out of `optimizer.param_keys`; `provider` cannot be opened (`PARAM_FORBIDDEN_KEYS`). **A verdict is an ENVIRONMENT's** — the dataset under the solver named. The L4 pool screens a second solver, `meta-llama/llama-3.1-8b-instruct:nitro`, seated only through a list campaign whose arm names that solver.

### Rejected without trial

Not to be re-investigated: **GPQA Diamond / Main** (top of band, N=198); **MMLU-Pro**, **AGIEval-EN**, **StrategyQA** (above band); **HLE**, **ZebraLogic**, **ARC-AGI-2**, **FrontierMath** (floors); **SimpleBench** (private test set), **LiveBench** (monthly refreshes); **DROP** (F1 tokenization bug); **ReClor** (bimodal), **ANLI R3** (strictly worse than FOLIO); **LogiQA 2.0** (translation artifacts), **HumanEval / CRUX** (single axis), **IFEval** (superseded by IFBench); **τ-bench** (tool calls outside the connector boundary); **AA-LCR** (a per-sample judge — re-read criterion 5 first); **GDPval-AA** (pairwise Elo breaks PoBB).

**JustLogic:** depth cuts do not compare — [`../../research/datasets/CLAUDE.md`](../../research/datasets/CLAUDE.md) § L4 — `promptpotter-self`; its hedge to `Uncertain` yields to attacking the chain, not the conclusion.

**Deferred, pending a recon at the bar:** **AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`; floor risk); **the Popper task families** ([arXiv 2005.02259](https://arxiv.org/abs/2005.02259)); **Nemotron-PII** (`nvidia/Nemotron-PII`; needs a span-F1 matcher).

A trialed dataset rejected for a measured reason (no headroom over its floor, saturation, a broken grader) is recorded in benchmarks.md with that reason, and its tenant definition and recipe are deleted.

### What a cell costs

A containerized agent episode costs 14–48× an `llm_only` call. Latency survives a cache replay; the tail is the host's more than the model's — a wall-clock claim pins `route_order` and names the host.

## The model a dataset runs

- **`max_tokens` is never a numeric default in a `{dataset}/dataset.yaml::pipeline` node config** — the provider ceiling applies. A Harbor or DB-LLM-Bench node is the exception: its `max_tokens` (and Harbor's `max_turns`) bound its cell's spend beside the window its model's catalogue card holds, and a node missing one refuses before a cell is bought.
- **The default for a new dataset** is `openai/gpt-oss-20b:nitro` via OpenRouter with no `reasoning_effort`.
- **"Model floor" is a rung the MODEL decides.** An unset `reasoning_effort` resolves to the lowest rung the running model offers (`low` on `openai/gpt-oss-*`, which refuses `none`); a rung spelled in the file is a pin and follows no model a campaign swaps in.
- **What a dataset DECLARES is not what the axis SEARCHES** — `PipelineSchema.param_options` replaces the declared values with the model's own answer, a campaign narrowing intersects ([`../../promptpotter/infrastructure/llm/CLAUDE.md`](../../promptpotter/infrastructure/llm/CLAUDE.md) § LLM client).
- **What each endpoint refuses is `registry._MODEL_PROFILES`**, a row earned by a round whose arms differ in `reasoning_effort` (a rung the endpoint refuses reads REFUSED on its arm); re-measure rather than trust a remembered reading. An indistinct ladder is served as a caveat, never narrowed.
- **Groq caps output per model** (~2048 tokens on `gpt-oss-20b`) and charges reasoning against it: `medium` on a hard puzzle exhausts it before any content (`llm_only:reasoning_budget_exhausted`), and the retry trips the same trap.

### Reading a model A/B

`evidence --metric latency` names which factors are ALIASED — an `agent.model` split that `dataset` aliases is a slow bank read as a slow model; a panel declaring `axes:` (`application/runner/inner/tasks.py::InnerTasks`) generates the full product, which cannot alias. Rank speed, then cost, then quality. `:nitro` changes PRICE, not only speed — price an arm off the wire `cost_usd`, never the listing. `GLM-5.2` is excluded by operator decision.

### The agent model on a Harbor dataset

The candidate prompt reaches the agent only as an injected `SKILL.md`. Operator decisions: **two delivery modes, and a result names its mode** — *advanced* (default; the agent must open the skill, so read `skill_opened` before the score) and *primitive* (the skill injected into context; cheap, fast models do not open a skill, so SealQA at `max_turns: 4` is primitive-mode). **Rank by speed** (`step_phases.agent_execution`, never total cell time), then headroom, then cost (ceiling 3× `gpt-oss-20b`); banned on cost: `google/gemini-3.5-flash`, `qwen/qwen3.6-27b`. **Pin one host per model via `route_order`, and probe hosts first** — one model's hosts differed tenfold in throughput.

### Pick the model for what the run does

A prompt-cache discount holds only while many calls share one prompt on one host (dies on every new candidate or ablation arm); a flat input price rewards shortening it. A listed price is the cheapest host's — a cost is known only for a pinned host or from the bill. A search needs a model that misses a fair share; an ablation one that gets most right.

**What the role may cost (operator, 2026-10-24)** — list OUTPUT price per M tokens: a **prompt** target below $0.10 (the free tier admits only what `promptpotter/assets/free_tier.yaml` lists), an **agent** target below $0.30, the **optimizer** below $0.60. Read prices off `https://openrouter.ai/api/v1/models`.

## Adding a dataset

A new dataset is a tenant definition (`.promptpotter/projects/{tenant}/datasets/{name}/`), never a package one ([`../../promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md)).

1. **Research the canonical protocol first** — the author-recommended split and evaluation protocol, every claim cited; for a private task the operator names the slice reserved as test.
2. **Record it in `README.md`** — a **Data** section quoting the protocol with citations, sample counts and any sister training set. A multi-turn task is ONE cell with a fixed SEMANTIC step schema, never a turn index ([`../specs/roadmap.md`](../specs/roadmap.md) § Judged and turn-structured scoring; [`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md) § The step schema).
3. **The operator confirms the cut before any wire.** Never invent a split; a canonical test set consumed as an optimization pool is not leaderboard-comparable, and the operator accepts that; any deviation is stated in `README.md`.
4. **Then wire** `dataset.yaml` (WHOLE — [`../../promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md) § A dataset file is a partial; its `card:` is what the listing calls it, exactly a plain-text `title` and `summary`, and any other shape refuses the run), `task.md`, `origin.yaml`, `README.md`. Without `origin.yaml` the origin is empty and the solver is never told the matcher's grammar. A node graded on several fields names them in `config.answer_fields`, scores with `fields_match(predicted, ground_truth)`, and gives each `rows` item an object `ground_truth` ([`../research/structured-output.md`](../research/structured-output.md) § Several graded fields).
