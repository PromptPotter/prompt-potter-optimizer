# Landscape — the watch list

Every tool that could be read as a competitor gets an entry here **before** anyone decides it
matters. Only a *relevant* verdict earns a place in [`related-work.md`](related-work.md)'s argument;
this page records that the rest were looked at, and why they were set aside.

Each entry: **What** (from its source where readable) · **Verdict** — *relevant* (argued in
`related-work.md`), *weak* (recorded, not argued) or *reference* (a design worth taking from) ·
**Worth taking** (what it does better, however small) · **Checked** (date and last activity seen —
provenance, not status; re-check before quoting).

## Promptim (LangChain)

**What.** A CLI over LangSmith ([`hinthornw/promptimizer`](https://github.com/hinthornw/promptimizer)):
minibatch hill-climb keeping a rewrite only if dev improves, each evaluation a LangSmith Experiment,
the winner committed to the Prompt Hub. **Verdict — weak:** no budget meter, no stopping rule, and
keep-if-dev-improved is the winner's-curse exposure [`benchmarks.md`](benchmarks.md) rules on.
**Worth taking:** the round trip inside a team's own eval tool (our read-in / write-back lane) and
`evaluator_descriptions` (metric meaning handed to the proposer as prose); its `experiments/email_cs*`
pair with our `email-tagging`. **Checked** 2026-09-24 — last commit 2025-04-17.

## Langfuse and Opik — one overlap, read together

They overlap on the baseline (traces with spans, tokens, latency; LLM-as-judge and human scores;
dataset runs; a versioned prompt store; LangChain / LlamaIndex / LiteLLM integrations; open source,
self-hostable) and split on **whether anything searches** — the first two rows of
[`related-work.md`](related-work.md) § Three layers, routinely mistaken for one.

**What — Langfuse.** Production telemetry first: live traces, session debugging, cost and latency,
a human-written prompt store. Acquired by ClickHouse 2026-01-16, stated to stay open source
([announcement](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability)).

**What — Opik (Comet).** Evaluation-and-improvement first. Adds the six-algorithm Agent Optimizer
(MetaPrompt, HRPO, few-shot Bayesian, evolutionary, GEPA, `ParameterOptimizer`), which also optimizes
**tool schemas and MCP tool signatures**; **Test Suites** of plain-text assertions as regression tests;
and **Ollie**, a coding agent in the UI that reads traces and edits the agent's code
([Agent Optimizer](https://www.comet.com/docs/opik/agent_optimization/overview) ·
[automatic prompt optimization](https://www.comet.com/site/products/opik/features/automatic-prompt-optimization/) ·
[Ollie](https://www.comet.com/docs/opik/ollie)). Java + Python backends, React SPA over ClickHouse;
not LangChain-based. Apache-2.0.

**Verdict — relevant, both** ([`related-work.md`](related-work.md)): Langfuse as telemetry and a
read-in / write-back partner (§ Where each is ahead of us), Opik as the optimizer-menu layer and the
closest product rival (§ A menu is not a bench).

**Worth taking.** Langfuse: the prompt store as where a winner lands (the write-back half of the
interop lane, [`../specs/roadmap.md`](../specs/roadmap.md)). Opik: tool definitions as a search
surface; plain-language assertions as a code-free grader; Ollie's trace → diagnosis → code-edit loop,
our self-healing aimed at the user's codebase.

**The door this opens — PromptPotter reached THROUGH Opik.** Opik sells optimization as a menu behind
one API and pitches that seeing a bad answer is not enough, so its user is already our buyer. Build
the Promptim shape pointed at Opik — their dataset and traces in, every measurement an Opik
experiment, the winner back in their library — registering PromptPotter as one more optimizer (a
`BaseOptimizer` subclass, [`external-constraints.md`](external-constraints.md) § M13) or driving
Opik's six through an adapter that strips their own evaluation, each being a whole loop rather than
manifest members ([`../specs/roadmap.md`](../specs/roadmap.md) § The bench (M13)). Either way: their
algorithms, our budget, stopping rule and comparability guard. Vendor comparisons:
Comet's [compare hub](https://www.comet.com/site/products/opik/compare/) (re-check figures).

**Checked** 2026-09-24 — Opik `LICENSE` read (Apache-2.0); Langfuse's licence not re-read.

## SIFT — Self-Improvement via Fast Tree-search (paper)

**What.** Fu, Kulanthaivelu, Yamada, [arXiv 2609.19526](https://arxiv.org/abs/2609.19526)
(2026-09-17; [DAIR digest](https://academy.dair.ai/papers/self-improvement-via-fast-tree-search-2609.19526)).
DGM line: a coding agent patches its own **harness**, scored on Polyglot. Evaluation is the
bottleneck, so a cheap signal precedes the paid one: an LLM judge compares two candidates' full
source pairwise (never sees tasks; Spearman ρ≈0.68 vs benchmark, 0.40 shown diffs), wins feed a
regularized Bradley-Terry fit, parents sampled `P(i) ∝ exp(−α·rank_BT − β·rank_acc − η·log(1+visits))`.
Only nodes passing a 4-task easy gate queue for the 50-task evaluation (ordered by `rank_BT +
rank_acc`); unevaluated nodes inherit the parent's accuracy; expansion and evaluation run
concurrently. 35.1% on Polyglot (o3-mini, gpt-5.4 judge) vs DGM 30.7%, ~$87–150 and 2–5 h; HGM needed
347 CPU-h for 30.5% on Qwen3-30B. No code.

**Verdict — weak as a competitor, reference as a design.** One algorithm on the harness side of
[`related-work.md`](related-work.md) § Three layers: no interface, one benchmark, no stopping rule,
no prompt-optimization baselines. Its visit-penalised lineage selection matches ours
(`promptpotter/application/mask/backprop.py` UCB1, [`../specs/roadmap.md`](../specs/roadmap.md)
§ Selector members) — common ground, no moat.

**Worth taking.** (1) **A zero-sample pre-screen** — judge-ranked candidates as a prior for PoBB's arm
order or a pre-measurement cut, with a published ρ to beat. (2) The **easy-subset gate**. (3) One
acquisition mixing measured rank, predicted rank and a visit penalty. (4) Their per-step cost table
(expansion vs judge vs evaluation), which our budget meter should print. As a preset:
[`../specs/roadmap.md`](../specs/roadmap.md) § SIFT reproduction.

**Checked** 2026-09-24 — v1 only, no repository.

## RRSI — Regularized Recursive Self-Improvement of Agent Harnesses (Google Research)

**What.** Xia et al., [arXiv 2609.24972](https://arxiv.org/abs/2609.24972) (2026-09-21),
[`google-research/rrsi`](https://github.com/google-research/rrsi) (Apache-2.0),
[project page](https://regularized-rsi.com/). Frozen model, evolving **harness** (prompts, control
flow, tools, memory, sub-agents). Claim: the loop overfits its evolve tasks; answer: keep the edit
space open and regularize the search. Proposal: an annealed cap on bundled edits, a proposer
conditioned on the full edit history, a redirect to untried components on stall. Selection: a
pre-evaluation critic for suite-specific logic (regex denylist + LLM review), a noise-adjusted floor,
a cost rule (added tokens must pay in gain), pruning of components that stopped helping. Evolved on
Terminal-Bench 2.1, Harvey LAB, EngDesign; run unchanged on held-out benchmarks.

The ablation (Table 2, Harvey LAB only) is the number to remember:

| Variant | Evolve | ID held-out | OOD avg. | Tokens/trial (M) |
|---|---|---|---|---|
| H_0, no evolution | 89.4 | 86.9 | 39.7 | 1.56 |
| Unregularized evolution | 92.8 | 88.9 | 40.3 | 3.80 |
| RRSI | 90.5 | 89.2 | 43.6 | 2.42 |

Unconstrained posts the best practiced score, +0.6 OOD, at 2.4× the tokens. Abstract: up to +6.0
evolve, up to +4.3 OOD.

**Verdict — reference, not yet decided as a constraint.** Harness side, no prompt-optimization
baselines, so no M13 peer. It measures the failure an outer loop of ours would have — an evolve-set
gain that does not transfer; whether that binds L4 is [`external-constraints.md`](external-constraints.md)'s
call once the paper is read through.

**Worth taking.** (1) **The noise-adjusted floor**: a per-instance band δ bootstrapped over base
trials (`rrsi/calibrate.py`) a gain must clear — PoBB's question as a fixed threshold. (2) **The
leakage critic before evaluation.** (3) **The cost rule.** (4) **The annealed edit budget**, keeping a
change attributable. (5) Reporting evolve, ID held-out and OOD as separate columns beside tokens/trial.

**Checked** 2026-10-11 — README, abstract and Table 2 (arXiv HTML); method and other domains not read;
operator has not read it. Last push 2026-09-23, ~1.4k stars. README lists JobBench (OOD) +4.7, above
the abstract's "up to 4.3" — unreconciled.

## DSPy — GEPA, MIPROv2, BootstrapFewShot

**What.** [`stanfordnlp/dspy`](https://github.com/stanfordnlp/dspy) (MIT): typed modules whose prompts a
pluggable optimizer compiles — `BootstrapFewShot` (demos from passing traces), `MIPROv2` (Bayesian
search over instructions × demos), `dspy.GEPA` (reflective Pareto evolution) — each with the user's
metric and its own budget.

**Verdict — relevant.** [`related-work.md`](related-work.md) § The family PromptPotter's own algorithm
belongs to and § Against GEPA and AlphaEvolve; the swap cost is
[`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md). The obvious first M13 peer: three
optimizers behind one `compile()`, sharing no budget meter or grader. **Worth taking:** reach, already
taken by shipping as a DSPy optimizer. **Checked** 2026-09-24 — last push 2026-09-23, ~38k stars.

**GEPA `optimize_anything`, checked 2026-10-19.** GEPA outside DSPy optimizes any scored text artifact
(agent files, code, rendered SVG and 3D scripts) in three modes, with rendered images as feedback. It
narrows the scope line in [`related-work.md`](related-work.md) § Against GEPA and AlphaEvolve; its
ARC-AGI-1 headline (32.5% → 89.5%, Gemini 3 Flash) is against its own seed only, below the same model
at reasoning High. Detail:
[`../../paper/notes/gepa-optimize-anything-aie-2026.md`](../../paper/notes/gepa-optimize-anything-aie-2026.md).

## Promptolution (LMU / Freiburg / TUM)

**What.** Research framework ([`gepromptet/promptolution`](https://github.com/gepromptet/promptolution),
[EACL 2026 demo](https://aclanthology.org/2026.eacl-demo.21/)): one backend, response cache, token
logging, its own CAPO, EvoPrompt, OPRO. **Verdict — weak as a product, relevant as a peer:** closest
research-side bench, but no stopping rule beyond each optimizer's, no subset-invariant score, Python
only. **Worth taking:** CAPO as the first non-DSPy peer — racing under a token budget is PoBB's
nearest published neighbour. **Checked** 2026-09-24 — last push 2026-09-16.

## Promptomatix (Salesforce AI Research)

Zero-config DSPy front end ([`SalesforceAIResearch/promptomatix`](https://github.com/SalesforceAIResearch/promptomatix))
generating synthetic train/test sets from a task description. **Verdict — weak:** no new search,
meter or stopping rule. **Worth taking:** synthetic data as an on-ramp. **Checked** 2026-09-24 — last
commit 2026-06-02.

## Evidently Prompt Optimizer

`PromptOptimizer` in `evidently` ([blog](https://www.evidentlyai.com/blog/automated-prompt-optimization)):
one hill-climb over a 40/40/20 split with early stopping, for LLM-judge prompts. **Verdict — weak:**
held-out split, no cost accounting. **Worth taking:** the judge prompt as a target — our grader node is
a prompt too. **Checked** 2026-09-24.

## agent-opt (Future AGI)

Six optimizers in one package ([`future-agi/agent-opt`](https://github.com/future-agi/agent-opt)):
random, Optuna, ProTeGi, meta-prompt, PromptWizard, GEPA. **Verdict — weak:** a menu
([`related-work.md`](related-work.md) § A menu is not a bench). **Checked** 2026-09-24 — last push 2026-06-30.

## Promptfoo

Declarative evaluations and red-teaming ([`promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo),
acquired by OpenAI 2026-03-09); nothing searches. **Verdict — weak.** **Worth taking:** its YAML test
matrix as a read-in format. **Checked** 2026-09-24.

## llm-prompt-optimizer (MCP server)

Deterministic request-compression middleware behind MCP
([`Divyesh-freelance/llm-prompt-optimizer`](https://github.com/Divyesh-freelance/llm-prompt-optimizer)),
no dataset or scoring. **Verdict — weak:** name collision only. **Checked** 2026-09-24.

## Arize Phoenix and W&B Weave

Telemetry-first: [Phoenix](https://github.com/Arize-ai/phoenix) (Elastic License 2.0, span replay, a
commercial meta-prompt optimizer "Prompt Learning") and [Weave](https://github.com/wandb/weave)
(tracing, evaluations). **Verdict — weak.** **Worth taking:** span replay as a node-level probe.
**Checked** 2026-09-24; Prompt Learning from summaries only. Its SDK, checked 2026-10-31:
`PromptLearningOptimizer(...).optimize(dataset, output_column, feedback_columns)` runs the caller's
evaluators in-process, an LLM writes text feedback and a meta-prompt rewrites; the README states
Elastic 2.0 where the docs say fully open source — unreconciled.

## LangSmith

The platform [Promptim](#promptim-langchain) runs on. **Verdict — weak.** **Checked** 2026-09-24, via
Promptim only.

## MLflow

A tracking server and Prompt Registry, plus `mlflow.genai.optimize_prompts` (MLflow 3.5.0+) over
registered prompt versions: `predict_fn`, `train_data`, `prompt_uris`, `scorers` and an `optimizer` —
`GepaPromptOptimizer` (reflection under a `max_metric_calls` budget), `MetaPromptOptimizer` (zero- or
few-shot metaprompting) or a `BasePromptOptimizer` subclass
([docs](https://mlflow.org/docs/latest/genai/prompt-registry/optimize-prompts/)). **Verdict —
reference:** the registry and tracking server are a viewer we write to
([`../specs/mlflow-interop.md`](../specs/mlflow-interop.md)). **Worth taking:** the
`BasePromptOptimizer` seam, through which PromptPotter runs inside `optimize_prompts`. **Checked**
2026-10-28, the `optimize-prompts` page only; the MCP server not read.

## AlphaEvolve (Google DeepMind / Google Cloud)

**What.** Gemini proposes edits inside `EVOLVE-BLOCK` markers, a program database keeps the
population, a user evaluator scores. Now a **managed Google Cloud service** (Gemini Enterprise):
generation runs on Google's side; the user's `run_controller_loop()` pulls candidates, runs *their*
evaluator, posts scores back. Budget: `MAX_PROGRAMS_GENERATED` / `_EVALUATED`.

**The local checkout is not OpenEvolve.** `AlphaEvolve-from-OpenEvolve-Questionmark/` clones
[`Google-Cloud-AI/alphaevolve-on-googlecloud`](https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud)
(Apache-2.0), the managed service's client and examples. OpenEvolve is the separate open
reimplementation, [`algorithmicsuperintelligence/openevolve`](https://github.com/algorithmicsuperintelligence/openevolve)
(formerly `codelion/openevolve`, Apache-2.0).

**Verdict — relevant** ([`related-work.md`](related-work.md) § Against GEPA and AlphaEvolve); the
search is closed. **Worth taking:** the controller shape — service proposes, client evaluates and
posts back — the inverse of our plug point and a template for an external proposer.

**Checked** 2026-09-24 — local checkout 2026-07-10 (`8693985`); OpenEvolve last push 2026-07-18,
~7.4k stars; `google-deepmind/alphaevolve_results` last push 2026-01-05.

## Palantir AIP (AIP Evals)

[AIP Evals](https://www.palantir.com/docs/foundry/aip-evals/overview) grid-searches human-written
prompt and model variants inside Foundry. **Verdict — weak:** nothing proposes or stops early.
**Checked** 2026-09-24.

## NVIDIA SkillEvaluator

**What.** [`NVIDIA/skillevaluator`](https://github.com/NVIDIA/skillevaluator)
([arXiv 2608.20614](https://arxiv.org/abs/2608.20614)), the gate for NVIDIA's Verified Skills catalog:
validation (static checks, rubric, LLM security pass), deduplication (embedding overlap within a skill
and across a `--catalog`), live evaluation (paired trials with and without the skill: *Skill Lift*).
**Verdict — not a competitor:** nothing proposes, searches or stops. **Worth taking:** the three
tiers, adopted for skilled-agent targets ([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving
agent harnesses) — baseline the skill *absent*, check overlap against the library. Its sibling
[SkillSpector](https://github.com/nvidia/skillspector) is the class
[`external-constraints.md`](external-constraints.md) § SKILL names for our tier-1 floor.
**Checked** 2026-09-24 — README only.

## SoL-Pi (NVIDIA Labs)

**What.** [SoL-Pi](https://nvlabs.github.io/SoL-Pi/), a coding-agent harness: four opt-in mechanisms
on [Pi](https://github.com/NVlabs/SoL-Pi) (Action Fusion, ObservationPack, Evidence-Preserving Reducer,
Online Context Compact) in `sol-pi.json`, found by an auto-research loop ("an open-ended RSI
problem"): a batched proposer with *Oracle Analysis* estimating opportunity from existing trajectories
before buying rollouts, map-reduce trajectory analyzers, implementer + reviewer in a Ralph Loop, two
evaluation stages (in-trajectory, then held-out — "no agent inside the auto-research loop sees the
held-out results"). Predeclared gate: capability metrics within tolerance, at least one efficiency
metric improves, nondominated survivors kept. 152 directions in, 4 survived. EdgeBench ~94% of Pi's
score at 45–49% fewer tokens; Terminal-Bench 15 solved vs Pi's 18 at ~26% less cost. MIT.

**Verdict — reference, on the recursive self-improvement axis** (DGM family: an agent rewrites a
coding agent's harness code, the one axis PromptPotter has not designed). Not a peer or bench arm;
accepts on a tolerance gate rather than a lift; no global stopping rule, no cross-lineage budget, no
measurement archive stated.

**Worth taking.** The acceptance gate as a selector shape (capability within tolerance, cost the
objective); Oracle Analysis (price an idea on trajectories already paid for); the proposer never sees
held-out results.

**Checked** 2026-10-20 — project page and README; paper, code and plug points not read.

## NeMo Helix and NeMo Agent Toolkit (NVIDIA)

**What.** NeMo Agent Toolkit (NAT, `nvidia-nat`, Apache-2.0): YAML agent workflows with `nat eval`,
a profiler and `nat optimize` — Optuna over numeric knobs, and a genetic algorithm over prompts (LLM
mutation steered by a `prompt_purpose`, LLM recombination, elitism) scored by weighted evaluator
metrics with `reps_per_param_set` repeats. Its README deprecates it as of October 2026, end-of-life
by the year's end, and sends its users to three successors: NeMo Relay, NeMo Helix and NeMo Fabric.
Helix (`nemo-helix`, 0.x, its repository created 2026-05) is an agent control plane whose `optimize` runs NAT's
two phases, a model-pair router or a single rewrite; a third-party strategy registers in its
`nemo.jobs` entry-point group.

**Verdict — weak:** not a standard. Its maker is retiring NAT, and Helix, a 0.x successor, has
little developer uptake yet, so nothing here builds toward either; as an optimizer, each strategy
runs its own way with its own repeats ([`related-work.md`](related-work.md) § A menu is not a bench).

**Worth taking.** `prompt_purpose`, the operator's intent handed to the mutator.

**Checked** 2026-10-31 — NAT's deprecation notice in its README, read directly; uptake by
`gh search repos` (Helix 132 stars, OpenShell 20.8k); the optimizer and Helix pages through a
summarizing fetch.

## Agent Lightning (Microsoft)

**What.** [`microsoft/agent-lightning`](https://github.com/microsoft/agent-lightning) (MIT, 1.0.2 of
2026-09-29). Its v0.x: an algorithm server enqueues rollouts, runners anywhere dequeue them over REST
and report OpenTelemetry spans that double as heartbeats, and silence past a timeout requeues the
rollout as a new attempt; no auth. APO there is textual gradients plus a beam search over ONE prompt
template, the proposer server-side and the rollouts client-side. v1.0 (August 2026,
[2608.17528](https://arxiv.org/abs/2608.17528)) is an RL-only rewrite whose controller pushes
rollouts; APO stays on the v0.x branch.

**Verdict — reference** for the pull shape ([`../specs/roadmap.md`](../specs/roadmap.md) § Embedding (M15), R2);
**weak** as an optimizer: one template, no budget meter, no stopping rule.

**Worth taking.** Spans as liveness; a caller-chosen rollout id that makes a resubmission idempotent.
Its requeue on timeout is a retry, which
[`../architecture.md`](../architecture.md) § Errors heal upward refuses.

**Checked** 2026-10-31 through a summarizing fetch — README, the v0.x store source, the APO page,
the v1 docs.

## Client-evaluated optimization services — OSS Vizier, Optuna, W&B Sweeps

**What.** OSS Vizier (Apache-2.0, `google-vizier` 0.1.24): gRPC `SuggestTrials`, the client
evaluates, `CompleteTrial`; a repeated `client_id` gets its pending trial back; no auth.
[Optuna](https://github.com/optuna/optuna) 5.0.0 (MIT): `ask()` / `tell()`, many workers on one database or an experimental gRPC proxy, no auth. W&B
Sweeps: a hosted controller and agents anywhere polling it, the heartbeat replying `run`, `resume`,
`stop` or `exit`; API keys.

**Verdict — reference:** the shape a service takes when the client evaluates
([`../specs/roadmap.md`](../specs/roadmap.md) § Embedding (M15), R2); none optimizes prompts.

**Worth taking.** Vizier's `client_id` — a lease idempotent per program; W&B's stop riding the poll
reply, so the one stop ([`../decisions.md`](../decisions.md) § 0010) reaches a pulled program with no
second channel.

**Checked** 2026-10-31 through a summarizing fetch — Vizier's service proto and client, Optuna's
docs, the wandb agent source.

## Vertex AI Prompt Optimizer and Bedrock Advanced Prompt Optimization

**What.** Vertex AI's data-driven optimizer, now documented under Gemini Enterprise Agent Platform
([docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/prompt-optimizer)):
the instruction, the demonstrations or both, Google target models only, rows in GCS, standard metrics
plus at most one custom metric — a Cloud Run function the service calls. Bedrock's Advanced Prompt
Optimization (2026-05-14; [how it works](https://docs.aws.amazon.com/bedrock/latest/userguide/advanced-prompt-optimization-how.html),
[launch post](https://aws.amazon.com/blogs/aws/amazon-bedrock-introduces-new-advanced-prompt-optimization-and-migration-tool/)):
a job over S3 JSONL, up to 10 templates of up to 100 samples each and up to five Bedrock target
models, images and PDFs as inputs, scored by a judge rubric, plain-language steering criteria or the
customer's Lambda, which the service invokes; it ends on "proprietary internal optimization
parameters"; `advpo:optimize` and `advpo:exclude` tags scope what may be rewritten, and an option
shortens the prompt.

**Verdict — weak:** template text alone, one vendor's catalogue, no stopping rule or comparability.

**Worth taking.** Bedrock's tags — a lock on a span inside one field, finer than our per-field lock;
its shortening objective, which a composite fitness already prices. The customer-hosted scorer each
calls is the push shape R2 avoids.

**Checked** 2026-10-31 — Bedrock's how-it-works page read as served; Vertex's data-driven optimizer
page and Bedrock's evaluation and `CreateAdvancedPromptOptimizationJob` pages through a summarizing
fetch.
