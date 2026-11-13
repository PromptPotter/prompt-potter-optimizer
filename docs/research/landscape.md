# Landscape — peers, and where PromptPotter sits

Every tool that could be read as a competitor gets a row here before anyone decides it matters: what
it is, the verdict — *relevant* (argued below), *weak* (set aside) or *reference* (a design worth
taking) — what is worth taking, and when it was checked. The stop rule is
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)'s; what a number may
claim is [`benchmarks.md`](benchmarks.md)'s; this page claims no head-to-head before that page does.

## Three layers, routinely mistaken for one

| Layer | What it ships | Where it stops |
|---|---|---|
| **Telemetry** — Langfuse, Phoenix, Weave, LangSmith | Spans, tokens, latency, a prompt store, LLM-as-judge over traces | A human writes the prompt. Nothing searches. |
| **Eval + an optimizer menu** — Opik, agent-opt, Promptolution, MLflow, NeMo | Several search algorithms behind one interface | Each runs *however it wants*: reachable, not comparable. |
| **A single algorithm, in a paper** — APE, OPRO, MIPROv2, PromptWizard, CAPO, GEPA, LEVI (prompt); SkillOpt, DarwinX, AutoDesign, SIFT, RRSI, SoL-Pi (harness); AlphaEvolve, OpenEvolve, AlgoTuner (code) | One method, one benchmark suite, one budget convention | No interface, no obligation to run beside anything else. |

**The gap is the third column.** No one serves a **stopping rule** across methods — none, ours
included, anytime-valid under optional stopping ([`external-constraints.md`](external-constraints.md)
§ Ranked, item 2) — nor a **comparability guard**. Hence a bench: a third party's method plugs in as
an optimizer manifest, measured under one model, budget meter, grader, split and archive
([`../specs/roadmap.md`](../specs/roadmap.md) § The bench).

**PromptPotter's own family is LLM-driven evolution**: an LLM proposes variants, a scorer ranks them,
winners breed. The line: [APE](https://arxiv.org/abs/2211.01910) and OPRO (an LLM authors its own
instruction); [DSPy](https://github.com/stanfordnlp/dspy) and [MIPROv2](https://arxiv.org/abs/2406.11695)
(the prompt a compiled artifact, instructions × demos); [PromptWizard](https://arxiv.org/abs/2405.18369)
(critique-guided refinement); [CAPO](https://arxiv.org/abs/2504.16005) (racing under a token budget);
[GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 oral), the reference point, though CAPO outscored
it in the promptolution head-to-head and its gains shrink at matched budgets
([LEVI](https://arxiv.org/abs/2605.09764)). **PromptPotter moves a whole declared pipeline jointly
with the prompt and searches a tree, not a line** (§ Comparison to MCTS).

## Peers

| Peer | What | Verdict · worth taking | Checked |
|---|---|---|---|
| **DSPy** — `BootstrapFewShot`, `MIPROv2`, `dspy.GEPA` ([`stanfordnlp/dspy`](https://github.com/stanfordnlp/dspy), MIT) | Typed modules whose prompts an optimizer compiles: `Teleprompter.compile(student, *, trainset, teacher=None, valset=None, **kwargs) -> Module`; GEPA budgets by `auto`, `max_full_evals`, `max_metric_calls` (what a budget-matched head-to-head sets). | **Relevant** — the bench's first peer: three optimizers behind one `compile()`, no shared budget meter or grader. Reach, taken by shipping as a DSPy optimizer (`presentation/teleprompter.py`; [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md) § What you trade away). A call replayed from DSPy's own cache is not counted. | 2026-09-24 |
| **GEPA `optimize_anything`** | GEPA outside DSPy optimizes any scored text artifact, rendered images as feedback; its ARC-AGI-1 headline (32.5% → 89.5%) is against its own seed, below the same model at reasoning High. | Narrows our scope line (§ Against GEPA and AlphaEvolve); [`../../paper/notes/gepa-optimize-anything-aie-2026.md`](../../paper/notes/gepa-optimize-anything-aie-2026.md). | 2026-10-19 |
| **Promptolution** ([`gepromptet/promptolution`](https://github.com/gepromptet/promptolution), [EACL 2026 demo](https://aclanthology.org/2026.eacl-demo.21/)) | One backend, response cache, token logging; its own CAPO, EvoPrompt, OPRO. | **Relevant as a peer**: closest research-side bench, no stopping rule beyond each optimizer's, no subset-invariant score. CAPO — racing under a token budget — is PoBB's nearest published neighbour. | 2026-09-24 |
| **LEVI** ([2605.09764](https://arxiv.org/abs/2605.09764)) | Matches or exceeds GEPA at under half its rollout budget (abstract read). | **Relevant** — the budget-matched baseline; mapped in [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md). | 2026-09-24 |
| **Opik** (Comet; Apache-2.0; [docs](https://www.comet.com/docs/opik/agent_optimization/overview)) | Six algorithms (MetaPrompt, HRPO, few-shot Bayesian, evolutionary, GEPA, `ParameterOptimizer` over temperature and `top_p`) through one `optimize_prompt()`, also over tool and MCP schemas; plain-text assertion Test Suites; Ollie, a coding agent that reads traces and edits the agent's code. Plug point: subclass `BaseOptimizer`, `optimize_prompt(prompt, dataset, metric, …, max_trials=None) -> OptimizationResult`. | **Relevant** — the menu layer and closest product rival; "we also move parameters" is not our line. Its user is our buyer: PromptPotter as a `BaseOptimizer` subclass, or its six run through the plug point under our budget, stopping rule and guard ([`../specs/roadmap.md`](../specs/roadmap.md) § What PromptPotter emits). Tool definitions as a search surface; assertions as a code-free grader. | 2026-09-24, `LICENSE` read |
| **Langfuse** | Production telemetry and a human-written prompt store; acquired by ClickHouse 2026-01-16, to stay open source. | **Relevant** as read-in / write-back partner: their traces as a dataset source, our winner into their prompt store. | 2026-09-24 |
| **Promptim** (LangChain; [`hinthornw/promptimizer`](https://github.com/hinthornw/promptimizer)) | CLI over LangSmith: minibatch hill-climb keeping a rewrite only if dev improves, the winner committed to the Prompt Hub. | **Weak**: no budget meter or stopping rule; keep-if-dev-improved is the winner's-curse exposure. `evaluator_descriptions` — metric meaning handed to the proposer as prose. | 2026-09-24; last commit 2025-04-17 |
| **agent-opt** ([`future-agi/agent-opt`](https://github.com/future-agi/agent-opt)) · **Promptomatix** ([`SalesforceAIResearch/promptomatix`](https://github.com/SalesforceAIResearch/promptomatix)) · **Evidently `PromptOptimizer`** ([blog](https://www.evidentlyai.com/blog/automated-prompt-optimization)) | Six optimizers in one package (random, Optuna, ProTeGi, meta-prompt, PromptWizard, GEPA) · a zero-config DSPy front end with synthetic train/test sets · one hill-climb over a 40/40/20 split for LLM-judge prompts. | **Weak**: a menu, an on-ramp, no cost accounting. Synthetic data as an on-ramp; the judge prompt as a target — our grader node is a prompt too. | 2026-09-24 |
| **MLflow** ([docs](https://mlflow.org/docs/latest/genai/prompt-registry/optimize-prompts/)) | Tracking server and Prompt Registry; `mlflow.genai.optimize_prompts` (3.5.0+) with `predict_fn`, `train_data`, `prompt_uris`, `scorers` and an `optimizer` — `GepaPromptOptimizer`, `MetaPromptOptimizer`, or a `BasePromptOptimizer` subclass. | **Reference**: a viewer we write to, and `BasePromptOptimizer` the seam PromptPotter runs inside ([`../specs/roadmap.md`](../specs/roadmap.md) § MLflow interop). | 2026-10-28 |
| **Arize Phoenix** (Elastic 2.0) · **W&B Weave** · **LangSmith** · **Promptfoo** (acquired by OpenAI 2026-03-09) · **llm-prompt-optimizer** (MCP request compression) | Telemetry and evals; Phoenix's Prompt Learning is a meta-prompt rewrite on LLM-written feedback with the caller's evaluators in-process. | **Weak**: nothing searches. Span replay as a node-level probe; Promptfoo's YAML matrix as a read-in format; the last is a name collision. | 2026-10-31 |
| **Palantir AIP Evals** ([docs](https://www.palantir.com/docs/foundry/aip-evals/overview)) | Grid-searches human-written prompt and model variants. | **Weak**: nothing proposes or stops early. | 2026-09-24 |
| **Vertex AI Prompt Optimizer** ([docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/prompt-optimizer)) · **Bedrock Advanced Prompt Optimization** ([how it works](https://docs.aws.amazon.com/bedrock/latest/userguide/advanced-prompt-optimization-how.html)) | Instruction and/or demonstrations over one vendor's catalogue; the customer's scorer (Cloud Run / Lambda) called by the service; Bedrock's `advpo:optimize` / `advpo:exclude` tags scope what may be rewritten. | **Weak**: template text alone, no stopping rule or comparability. Bedrock's tags — a lock on a span inside one field, finer than our per-field lock; the service-called scorer is the push shape R2 avoids. | 2026-10-31, summarizing fetch |
| **AlphaEvolve** (Google DeepMind; a managed Gemini Enterprise service) · **OpenEvolve** ([`algorithmicsuperintelligence/openevolve`](https://github.com/algorithmicsuperintelligence/openevolve), Apache-2.0) | Gemini proposes edits inside `EVOLVE-BLOCK` markers, a program database keeps the population; the user's `run_controller_loop()` pulls candidates, runs *their* evaluator, posts scores back. The local `AlphaEvolve-from-OpenEvolve-Questionmark/` checkout is the managed service's client, not OpenEvolve. | **Relevant** (§ Against GEPA and AlphaEvolve); the search is closed. The controller shape — service proposes, client evaluates — is the inverse of our plug point. | 2026-09-24 |
| **Agent Lightning** ([`microsoft/agent-lightning`](https://github.com/microsoft/agent-lightning), MIT) | v0.x: a server enqueues rollouts, runners dequeue over REST and report OpenTelemetry spans that double as heartbeats; silence past a timeout requeues; no auth. APO is textual gradients plus beam search over one template; v1.0 ([2608.17528](https://arxiv.org/abs/2608.17528)) is RL-only. | **Reference** for the pull shape ([`../specs/roadmap.md`](../specs/roadmap.md) § Embedding (M15), R2); weak as an optimizer. Spans as liveness; a caller-chosen rollout id. Its requeue is a retry, which [`../architecture.md`](../architecture.md) § Errors heal upward refuses. | 2026-10-31 |
| **OSS Vizier** · **Optuna** 5.0 · **W&B Sweeps** | Client-evaluated services: Vizier `SuggestTrials` / `CompleteTrial` with a `client_id` that returns a pending trial; Optuna `ask()` / `tell()`; W&B agents polling a controller whose heartbeat replies `run`, `resume`, `stop` or `exit`. None optimizes prompts. | **Reference**: `client_id` as a lease idempotent per program; the stop riding the poll reply, so the one stop ([`../decisions.md`](../decisions.md) § 0010) needs no second channel. | 2026-10-31 |
| **NeMo Agent Toolkit** → **NeMo Helix** (NVIDIA) | `nat optimize`: Optuna over numeric knobs, a genetic algorithm over prompts steered by a `prompt_purpose`, `reps_per_param_set` repeats; deprecated October 2026 for Helix (0.x). | **Weak**: not a standard, its maker retiring it. `prompt_purpose` — the operator's intent handed to the mutator. | 2026-10-31 |
| **NVIDIA SkillEvaluator** ([`NVIDIA/skillevaluator`](https://github.com/NVIDIA/skillevaluator), [2608.20614](https://arxiv.org/abs/2608.20614)) | Three tiers: validation (static checks, rubric, LLM security pass), deduplication (embedding overlap within and across a `--catalog`), paired trials with and without the skill (*Skill Lift*). | **Not a competitor**. The tiers, adopted for skilled-agent targets ([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving agent harnesses); SkillSpector is the tier-1 floor ([`external-constraints.md`](external-constraints.md) § SKILL). | 2026-09-24, README |
| **SIFT** ([2609.19526](https://arxiv.org/abs/2609.19526)) | DGM line: a coding agent patches its own harness. An LLM judge ranks candidates' source pairwise without seeing tasks (ρ ≈ 0.68 vs benchmark) into a Bradley-Terry fit; parents sampled by measured rank, predicted rank and a visit penalty; a 4-task easy gate precedes the 50-task evaluation. 35.1% Polyglot vs DGM 30.7%. No code. | **Reference**: visit-penalised selection matches ours (`application/mask/backprop.py` UCB1). A zero-sample judge pre-screen as a prior for PoBB's arm order; the easy-subset gate; a per-step cost table. Preset: [`../specs/roadmap.md`](../specs/roadmap.md) § SIFT reproduction. | 2026-09-24 |
| **RRSI** ([2609.24972](https://arxiv.org/abs/2609.24972), [`google-research/rrsi`](https://github.com/google-research/rrsi), Apache-2.0) | Frozen model, evolving harness, regularized: an annealed edit cap, a proposer conditioned on edit history, a pre-evaluation leakage critic, a noise-adjusted floor, a cost rule. Table 2: unregularized posts the best evolve score at 2.4× the tokens; RRSI +3.9 OOD over H_0. | **Reference**: it measures the failure an outer loop of ours would have — an evolve-set gain that does not transfer. The floor (`rrsi/calibrate.py`), the critic, the cost rule, evolve / ID / OOD columns beside tokens. | 2026-10-11, README, Table 2 |
| **SoL-Pi** (NVIDIA Labs; [site](https://nvlabs.github.io/SoL-Pi/), MIT) | A coding-agent harness improved by an auto-research loop: *Oracle Analysis* prices an idea on trajectories already paid for; no agent inside the loop sees held-out results; a predeclared tolerance gate. 152 directions in, 4 survived. | **Reference**: the tolerance gate as a selector shape (cost the objective); Oracle Analysis; a proposer blind to held-out results. No stopping rule, cross-lineage budget or archive. | 2026-10-20 |
| **SkillOpt** · **DarwinX** · **AutoDesign** · Sakana's [RSI Lab](https://sakana.ai/rsi-lab/) · a [reading list](https://github.com/leezythu/Awesome-Harness-Self-Improvement) | Evolve a harness around a frozen model on agent environments (ALFWorld, SpreadsheetBench, Terminal-Bench, WebArena) the `harbor` connector measures too; DarwinX makes the "single-lineage search is path-dependent" critique. | **Relevant** as head-to-head arms: wiring plus a per-cell spend ceiling. None runs sequential elimination, a cost-per-fitness model or a subset-invariant ability scale. | 2026-09-24 |

## Why PoBB's family and not another

Mid-round abortion is **best-arm identification**; three choices place PoBB:

- **Every prior, not only the leader** — LUCB (Kalyanakrishnan 2012), Bayes-UCB (Kaufmann 2012) and
  Hoeffding Races (Maron & Moore 1993) test against the leader alone, with frequentist bounds.
- **Bayesian over frequentist** — `P(c is best)` is one operator-readable number.
- **Fixed-confidence (ε) over fixed-budget** — broken candidates stop at the evidence floor;
  Successive Rejects (Audibert 2010) and Sequential Halving (Karnin 2013) run them to the phase
  boundary. ε is a bar on a posterior reading, not an anytime-valid error rate
  ([`external-constraints.md`](external-constraints.md) § Ranked, item 2).

Holm is a reporting correction, not the stop rule (`shared/statistics.py::holm_adjusted`). OCBA (Chen
2000) allocates where this stops; PoBB keeps Top-Two Thompson Sampling's stop rule (Russo 2016, COLT)
and drops its allocation half. Prior art: Stockfish's fishtest (patch vs master, SPRT) — its
unbalanced opening book is our seed-misses-first order, and Elo is Bradley–Terry, Rasch's family; not
theirs: an automated proposer, N-arm identification, measurement reuse across a changed instrument.

## Comparison to MCTS

PromptPotter **is** AlphaZero-shaped MCTS over the lineage tree: **selection** — L2/L3 emits
`fork_proposal: {reason}` (*whether* to rewind); *where* is UCB1 over the backpropagated tree
(`application/mask/backprop.py::select_branch_round`), the layer never seeing ancestors' fitness;
**expansion** — a round, L1 proposing a population from the selected node; **simulation** — a
deterministic forward pass, PoBB pruning siblings on shared samples; **backpropagation** — each
round's θ rolls up to every ancestor as visit count + value (`accumulate_node_stats`), read on ONE fit;
a caveated round is a visit with no value. **Value is θ, never accuracy, and a fork's inherited prefix
is not a fresh visit** — both naive forms fail silently. A rollout is a full round of LLM calls, so
exploration is closer to PUCT than UCT.

## A menu is not a bench

"We can wrap their method" is not a differentiator — Opik and agent-opt already expose several
algorithms behind one API. What makes two algorithms' numbers mean the same thing: **one grader** (one
compiled formula for both sides — our own head-to-head was bitten when `matchers.py::_label_match`
stripped to the last bold span while the peer notebooks compared whole strings); **one budget meter**
(GEPA's reflective rollouts and MIPROv2's minibatch trials on one ledger, so cost-per-fitness is a
quantity); **one exam, chosen before anybody proposes**; **ability, not accuracy** (subset-invariant
Rasch θ, [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md), so drifting onto
easier samples cannot out-rank an honest method); **a stopping rule** (PoBB, not a fixed trial count);
**the archive** (measurements content-addressed by configuration and sample, so methods converging on
overlapping candidates pay for those cells once). Campaigns side by side serve that list first — what
was held equal, what stays confounded — then the numbers.

## Against GEPA and AlphaEvolve

Same family, same reflective core: GEPA evolves a DSPy program's instruction text, AlphaEvolve source
code, PromptPotter prompt and pipeline jointly — a different target, not a missing feature. The table
is capability, not a benchmark result.

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Runs in your editor** — a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |
| **Tunes the whole pipeline** — model, temperature and node thresholds evolve with the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — budget to the questions that separate candidates (PoBB), scores on one scale | 🟡 | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is taught back to the layer that owns its fix (L1's own next call; a refused L2 layout fires L3), not discarded ([internals](../architecture.md#errors-heal-upward-tolerantly)) | 🟡 | 🟡 | 🟢 |
| **Carries knowledge across runs** — parameter impact, query difficulty, failure patterns and a block library survive the campaign | 🟡 | 🟢 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger | 🔴 | — | 🟢 |
| **Open & inspectable** — code on GitHub, a browser control plane, a campaign you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |

🟡 = a partial version; `—` = closed, undocumented. **The row the bench adds — runs somebody else's
optimizer under the same guard — is deliberately absent**: it lands with the first published
head-to-head. **Where each is ahead of us:** GEPA — reach (DSPy's ecosystem); AlphaEvolve — code
optimization; Langfuse — production telemetry; Opik — breadth, "more algorithms, sooner", until its
methods run inside the plug point.

## Agent-harness evolution

Evolving a harness around a frozen model is occupied (SkillOpt, DarwinX, AutoDesign, SIFT, RRSI,
SoL-Pi, above). PromptPotter already recurses — an outer cycle whose backend is an inner cycle
([`../concepts/l4.md`](../concepts/l4.md)) — where the optimizer *is* the instrument,
so the thing varied also does the measuring; only a bench that holds a harness constant answers that.
The distinguishing claim: sequential elimination, a cost-per-fitness model, a subset-invariant ability
scale, joint prompt and pipeline-parameter search. Precedent and measured danger:
[`external-constraints.md`](external-constraints.md) § Ranked, item 1, and § L4.

## Test-time compute, moved out of the request

Searching inside the request — sample, verify, pick ([Snell et al.](https://arxiv.org/abs/2408.03314)
· [survey](https://arxiv.org/abs/2406.16838)) — is what PromptPotter runs as tuning infrastructure: a
configuration found once, then an ordinary call with no search in the path, the test-time budget
(`reasoning_effort`, `max_tokens`, the model) itself tunable. Live per-question model selection is a
gateway's job ([OpenRouter](https://openrouter.ai)); PromptPotter tunes what the gateway calls.
