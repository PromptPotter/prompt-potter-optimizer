# Datasets — which ones, which model, how to add one

Which datasets we optimize on and why, the rules for the model each runs, and the wiring process. **The measurement roster, the admission bar and the order of use** — owned by [`../research/benchmarks.md`](../research/benchmarks.md) § Every dataset we measured, § The admission bar and § Order of use. A dataset's model pin is its own `{dataset}/dataset.yaml::pipeline`; this page holds the rules that choose it.

## Selection criteria

A focus dataset for L1 optimizer prompt evolution must satisfy:

1. **Origin in band** — `gpt-oss-20b @ low` scores **15–40%** at origin: below is a floor effect, above leaves no headroom over PoBB noise. The top is 40, not 35, for the projection slop benchmarks.md states.
1b. **Origin clears its CONSTANT-ANSWER floor** — the score of a stub emitting the commonest label, per dataset (a 3-class set with a 40% majority scores 40% collapsed). Read it off the `answer_distribution` panel; enforced at measurement by `domain/scoring.py::is_answer_collapsed`, which withholds θ from a collapsed candidate. Criterion 1 alone cannot see it.
2. **Reachable ceiling** of **50–75%** under strong prompt engineering — the gap L1 climbs.
3. **N ≥ 400, preferably 800+**; smaller N needs per-subtask stratification.
4. **Multiple distinct subtask categories** — each is an independent L1 lever.
5. **Deterministic per-sample grading** (exact match, MC, F1, regex). An LLM judge is admissible only through `promptpotter/application/scoring/judges/`: beside an episodic cell its cost is rounding error, but it adds model-dependent measurement error, so wire one only where no matcher can grade, validate it against the published grader before quoting a number, and never compare a judged dataset with a deterministic one until that check is done.
6. **No mode-collapse on one gold class** — a modal answer over 40% of gold, or a 3-class task read as 2-class, coasts into an inflated origin; stratify by `prop` / `category` / `subtask` before committing.
7. **HF-loadable** — a custom scraper is real plumbing cost.
8. **Contamination-resistant** — 2024+ or synthetic.
9. **Anchored by measurement, not projection** — a 25-sample recon on `gpt-oss-20b @ low` is the verdict.

**Toy and demo datasets are picked on deliberation length, not difficulty.** Prefer one-pass tasks (recognition, extraction, ordering a given set); read `reasoning_tokens` against `output_tokens` in `dashboard.json::spend.by_kind.backend` on the first round. A toy that has grown a deliberation step has stopped being one.

**Why `gpt-oss-20b @ low`:** leading open model at its scale, on price and `:nitro` throughput, at `low` so the optimizer climbs from the floor; higher rungs stay L1-reachable. The pin holds because these datasets leave `model` out of `optimizer.param_keys`; `provider` cannot be opened at all (`PARAM_FORBIDDEN_KEYS`).

**A verdict is an ENVIRONMENT's** — the dataset under the solver named — and "do not re-investigate a rejected row" holds for that environment. The L4 pool screens a second solver, `meta-llama/llama-3.1-8b-instruct:nitro`, where the canon's published origins sit; a dataset rejected at the bar seats there only through `seed-screen <dataset> --model <solver> --provider openrouter`.

### Rejected without trial

Projections, so read each through the upward bias benchmarks.md states. Not to be re-investigated:

- **GPQA Diamond / Main** — top of band, ~10pp ceiling-room, N=198.
- **MMLU-Pro**, **AGIEval-EN**, **StrategyQA** — above band.
- **HLE** — 4.2% origin but a ~17% ceiling: a floor in disguise. **ZebraLogic**, **ARC-AGI-2**, **FrontierMath** — hard floors.
- **SimpleBench** (private test set), **LiveBench** (monthly refreshes break cross-cycle comparison).
- **DROP** — the F1 tokenization bug tanks small-model signal; broken scoring, not a reasoning gap.
- **ReClor** — bimodal, no usable middle. **ANLI R3** — single axis, strictly worse than FOLIO.
- **LogiQA 2.0** (translation artifacts), **HumanEval / CRUX** (single axis), **IFEval** (superseded by IFBench).
- **τ-bench** — needs tool-call infrastructure outside the connector boundary.
- **AA-LCR** — a per-sample judge; re-read criterion 5 before re-rejecting it.
- **GDPval-AA** — pairwise Elo breaks PoBB and its artifact outputs exceed `llm_only`.

**JustLogic:** depth cuts do not compare — owned by [`../../promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md) § L4 — `promptpotter-self`. Its hedge to `Uncertain` does not yield to being told off — anti-hedge wording, procedures and personas all measure worse than the plain origin; attack the chain, not the conclusion.

### What a cell costs

**A containerized agent episode costs 14–48× an `llm_only` call**, so a harbor dataset needs its own budget arithmetic. Latency is `domain/scoring.py::MeasuredCell.cost_s`, which survives a cache replay. **The tail is the operational fact, not the median**, and it is the host's more than the model's: **a wall-clock claim pins `route_order` and names the host.** An episode's length is the prompt's to move (bimodal between "knew what to do" and "flailed"); its spend is bounded per send (`connectors/harbor.py::_sent_spend_bound`) and its clock by `Connector.cell_envelope_s`.

### Deferred research

Candidates pending a recon at the bar before wiring:

- **AA-Omniscience** (`ArtificialAnalysis/AA-Omniscience-Public`) — asymmetric scoring (+correct / −hallucinated / 0 abstain) gives a second knob; floor risk. 50-sample recon first.
- **The Popper task families** (Cropper & Morel, [arXiv 2005.02259](https://arxiv.org/abs/2005.02259)) — buttons, robots, list transformations; size-parametrized, so each setting is its own cut. Two cuts, two datasets: label a held-out example (binary gold — criteria 1b and 6), or emit the program and grade by Prolog entailment (needs SWI-Prolog).
- **Nemotron-PII** ([`nvidia/Nemotron-PII`](https://huggingface.co/datasets/nvidia/Nemotron-PII)) — no published table scores prompted frontier LLMs on it, so a raw-vs-optimized table would be the first. Needs a span-F1 matcher (strict and partial, micro over labels); label noise caps the ceiling; read the parquet before cutting, since sources disagree on its size.

**Update protocol.** A trialed dataset outside its band is recorded rejected in benchmarks.md with the empirical reason, and **its tenant definition and recipe are deleted** — the roster row and the seed-screen record are the evidence. One that works stays; several can serve as L4 inner cells at once.

## The model a dataset runs

**`max_tokens` is never a numeric default in a `{dataset}/dataset.yaml::pipeline` node config** — the provider ceiling applies; check the overlay rather than assuming. **A Harbor agent node is the exception**: `max_tokens`, `max_input_tokens` and `max_turns` are the only bound on its cell's spend (`connectors/harbor.py::_sent_spend_bound`).

**The default for a new dataset** is `openai/gpt-oss-20b:nitro` via OpenRouter with no `reasoning_effort`.

**"Model floor" is a rung the MODEL decides, never the file.** An unset `reasoning_effort` resolves to the lowest rung the running model offers — `low` on `openai/gpt-oss-*`, which refuses `none`. A rung spelled in the file is a pin and follows no model a campaign swaps in.

**What a dataset DECLARES is not what the axis SEARCHES** — `PipelineSchema.param_options` replaces the declared values with the model's own answer, while a campaign narrowing intersects (owned by [`../../promptpotter/infrastructure/llm/CLAUDE.md`](../../promptpotter/infrastructure/llm/CLAUDE.md) § LLM client). **A rung is a setting, not a dial** — no measured model orders its ladder monotonically.

**What each endpoint refuses is `registry._MODEL_PROFILES`**, a row earned with `probe-reasoning <model>`; re-probe rather than trust a remembered reading. An indistinct ladder is not narrowed — it is served as a caveat.

### The Groq output ceiling

Groq caps output per model (~2048 tokens on `gpt-oss-20b`) and charges reasoning against it: `medium` on a hard BBEH puzzle exhausts it before any content, `classify_result()` stamps `llm_only:reasoning_budget_exhausted`, and the retry trips the same trap.

### Reading a model A/B

**Read it with `evidence --metric latency`, which names which factors are ALIASED** — cutting the roster into identical groups, so an `agent.model` split that `dataset` aliases is a slow bank read as a slow model. **Aliasing is what a hand-assembled roster earns**; a panel declaring `axes:` (`application/runner/inner/tasks.py::InnerTasks`) generates the full product, which cannot alias. Rank in the operator's order: **speed, then cost, then quality**, and remember a ranking is tied to the dataset's I/O shape. A 10-row draw is a different bank from the 40-row panel, not a prefix. **`:nitro` changes PRICE, not only speed** — price an arm off the wire `cost_usd`, never the listing. `GLM-5.2` is excluded by operator decision.

### The agent model on a Harbor dataset

On Harbor the candidate prompt reaches the agent only as an injected `SKILL.md`. Operator decisions:

- **Two delivery modes; a result names its mode.** *Advanced* (default): the agent must open the skill, so read `skill_opened` before the score. *Primitive*: the skill injected into context, for the one-to-one literature comparison.
- **Rank by speed** (`step_phases.agent_execution`, never total cell time), **then headroom** (9–10/10 at origin leaves nothing), **then cost** (ceiling 3× `gpt-oss-20b`).
- **Banned on cost from every pipeline:** `google/gemini-3.5-flash`, `qwen/qwen3.6-27b`.
- **Pin one host per model via `route_order`, and probe hosts first** — one model's hosts differed tenfold in throughput.
- **Cheap, fast models do not open a skill they must go and read** — only an opening model can be optimized in advanced mode; SealQA at `max_turns: 4` is a primitive-mode dataset.
- **litellm drops `reasoning_effort` for `:nitro` and unknown models**, so the harbor connector sends `extra_body.reasoning` (`connectors/harbor.py::_REASONING_CHANNEL`).
- **A ten-cell cut is used up in two promotions** — a further campaign needs a larger cut.

### Pick the model for what the run does

**Why the model is cheap decides which runs it suits** — a prompt-cache discount holds only while many calls share one prompt on one host (dies on every new candidate or ablation arm); near-free input suits a changing prompt; a flat input price rewards shortening it. **A listed price is the cheapest host's** — a cost is known only for a pinned host or from the bill. On an executed-query dataset (`reactome-typeql-42`) the re-sent prompt dominates, so turn reasoning off where the model allows.

**Accuracy room decides direction:** a search needs a model that misses a fair share; an ablation needs one that gets most right, so a removal shows as a miss; a reported comparison runs on the published rows' model or the operator's pick.

**What the role may cost (operator, 2026-10-24)** — list OUTPUT price per M tokens: a **prompt** target below $0.10, an **agent** target below $0.30, the **optimizer** below $0.60. Read prices off `https://openrouter.ai/api/v1/models`.

## Adding a dataset

A new dataset is a tenant definition (`.promptpotter/projects/{tenant}/datasets/{name}/`), never a package one ([`../../promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md)). **Research before code.**

**1. Research the canonical protocol first.** Find the author-recommended split and evaluation protocol — dataset card, repo README, the paper's evaluation section, leaderboard methodology. For a private task ask the operator *"What slice have you reserved as test, or do you want to cut one now?"* If it is not obvious in five minutes, delegate to a fresh agent with no project context:

> Research the canonical train/test split and evaluation protocol used in the published literature for *&lt;DATASET&gt;* (HuggingFace: *&lt;path&gt;*, paper: *&lt;arxiv URL&gt;*, repo: *&lt;github URL&gt;*). What split do the authors recommend? What split do published papers score against? Is there a sister training dataset, at what size? Cite every claim (URL + section). Report findings only, no code, under 400 words.

**2. Record it in `README.md`** — a **Data** section quoting the protocol verbatim with citations, sample counts and any sister training set.

**2b. A TURN-STRUCTURED task decides its step schema now.** A multi-turn task is ONE cell ([`../specs/roadmap.md`](../specs/roadmap.md) § Judged and turn-structured scoring): name a fixed SEMANTIC step schema, never a turn index, and bank each step as its own named term for the `scoring` formula to collapse — the one part that cannot be added later. Adopt the schema for the task's shape (`retrieve → ground → answer` for search-augmented, `open → adhere` for an agent harness); **a schema that does not fit is worse than a new one** ([`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md) § The step schema).

**3. The operator confirms the cut before any wire.** **Never invent a split. Never consume a canonical test set as an optimization pool** without the operator accepting the number is not leaderboard-comparable; any deviation is stated in `README.md`.

**4. Then wire** the `source:` recipe, the scorer and the directory (`dataset.yaml`, `task.md`, `origin.yaml`, `README.md`). **A tenant `dataset.yaml` is WHOLE** — owned by [`../../promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md) § A dataset file is a partial. **`origin.yaml` is not optional for a benchmark**: without it `resolve_origin_opt_search_point` falls to an empty origin and the solver is never told the matcher's grammar (`matchers.py::MATCHERS` `note`); its `answer_format` commits the answer in that grammar.
