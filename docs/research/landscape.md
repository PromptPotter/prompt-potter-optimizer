# Landscape — the watch list

Every tool that could be read as a competitor gets an entry here **before** anyone decides it
matters. The entry is the reading; the verdict is the decision. Only a tool whose verdict is
*relevant* earns a place in the argument of [`related-work.md`](related-work.md) — this page is the
record that the rest were looked at, and why they were set aside.

An entry has four fields, and the dated one is provenance, not status: it records what was true
when the entry was written, so re-check it before quoting it.

- **What** — the tool in one paragraph, from its source where we could read it.
- **Verdict** — *relevant* (argued in `related-work.md`), *weak* (recorded, not argued), or
  *reference* (not a competitor, but a design worth taking from).
- **Worth taking** — what it does better than we do, however small the tool.
- **Checked** — the date of the reading and the last activity seen then.

## Promptim (LangChain)

**What.** A CLI over LangSmith ([`hinthornw/promptimizer`](https://github.com/hinthornw/promptimizer)):
a minibatch hill-climb that keeps a rewrite only if dev improves, every evaluation written back as a
LangSmith Experiment and the winner committed to the Prompt Hub. **Verdict — weak:** no budget
meter, no stopping rule, and keep-if-dev-improved is the winner's-curse exposure
[`benchmarks.md`](benchmarks.md) rules on. **Worth taking:** the round trip inside a team's own
eval tool — the shape of our read-in / write-back lane — and `evaluator_descriptions`, metric
meaning handed to the proposer as prose; its `experiments/email_cs*` pair with our `email-tagging`.
**Checked** 2026-09-24 — last commit 2025-04-17.

## Langfuse and Opik — one overlap, read together

The two are usually compared with each other, and the comparison is worth recording because it
comes out the same way as ours: they overlap on the baseline (traces with spans, tokens and
latency; LLM-as-judge and human scores; dataset runs; a versioned prompt store; the same
LangChain / LlamaIndex / LiteLLM integrations; both open source and self-hostable), and they split
on **whether anything searches**. That split is the first two rows of
[`related-work.md`](related-work.md) § Three layers, routinely mistaken for one.

**What — Langfuse.** Production telemetry first: high-volume live traces, session debugging, cost
and latency accounting, and a prompt store whose prompts a human writes. ClickHouse acquired it on
2026-01-16 and has said it stays open source.
([announcement](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability))

**What — Opik (Comet).** Evaluation-and-improvement first, beside Comet's ML experiment tracking.
On top of the shared baseline: the six-algorithm Agent Optimizer (MetaPrompt, HRPO, few-shot
Bayesian, evolutionary, GEPA, `ParameterOptimizer`), which also optimizes **tool schemas and MCP
tool signatures**, not only prompt text; **Test Suites** of plain-text assertions on agent
behaviour, used as regression tests; and **Ollie**, a coding agent inside the Opik UI that reads
the project's traces and edits the agent's code to fix what it finds.
([Agent Optimizer](https://www.comet.com/docs/opik/agent_optimization/overview) ·
[Ollie](https://www.comet.com/docs/opik/ollie)) Not built on LangChain, which is one integration
among many: a Java backend, a Python backend for evaluation and optimization, and a React SPA
(Vite, TanStack Router/Query/Table, Radix/shadcn + Tailwind, Mermaid for the agent graph) over
ClickHouse. Apache-2.0.

**Verdict — relevant, both.** Argued in [`related-work.md`](related-work.md): Langfuse as the
telemetry layer and a read-in / write-back partner (§ Where each is ahead of us), Opik as the
optimizer-menu layer and the closest thing to a product rival (§ A menu is not a bench).

**Worth taking.** From Langfuse, the prompt store as the place a winner lands: that is the
write-back half of the interop lane in [`../specs/roadmap.md`](../specs/roadmap.md), and our
tracing already writes to it. From Opik, three things: tool definitions as a search surface next
to the prompt; assertions written in plain language as a grader an operator can author without
code; and Ollie's trace → diagnosis → code-edit loop, which is our self-healing idea aimed at the
user's codebase rather than at our own layers.

**The door this opens — PromptPotter reached THROUGH Opik.** Opik already sells optimization as
a menu of algorithms behind one API, GEPA among them, and its own pitch against Langfuse is that
seeing a bad answer is not enough — something has to fix it. So an Opik user is already a buyer
of what we do, one API call away from it. The interop shape worth building is the Promptim one
above, pointed at Opik: their dataset and traces in, every measurement visible as an Opik
experiment, the winner back in their prompt library — with PromptPotter either registered as one
more optimizer behind that API (a `BaseOptimizer` subclass,
[`external-constraints.md`](external-constraints.md) § M13) or driving Opik's six through an
adapter that strips their own evaluation, since each is a whole loop rather than a set of
manifest members (the plug point in [`../specs/roadmap.md`](../specs/roadmap.md) § The bench (M13)). Either way the pitch is
the bench against the menu: their algorithms, our budget, stopping rule and comparability guard.

Vendor-stated comparisons live at Comet's [compare hub](https://www.comet.com/site/products/opik/compare/),
the fastest way to refresh this section; re-check any figure before quoting it.

**Checked** 2026-09-24 — Opik `LICENSE` read (Apache-2.0); Langfuse's licence not re-read.

## SIFT — Self-Improvement via Fast Tree-search (paper)

**What.** Fu, Kulanthaivelu, Yamada, [arXiv 2609.19526](https://arxiv.org/abs/2609.19526)
(2026-09-17; [DAIR digest](https://academy.dair.ai/papers/self-improvement-via-fast-tree-search-2609.19526)
2026-09-19). The Darwin Gödel Machine line: a coding agent patches its own **harness**
(`coding_agent.py`, tool implementations), scored on Polyglot. Its claim is that candidate
evaluation is the bottleneck, and its answer is a cheap signal in front of the paid one: an LLM
judge compares two candidates' full source files pairwise (never sees tasks; Spearman ρ≈0.68 vs
benchmark, 0.40 when shown diffs), win counts feed a regularized Bradley-Terry fit, and parents are
sampled `P(i) ∝ exp(−α·rank_BT − β·rank_acc − η·log(1+visits))`. Only nodes that pass a 4-task
easy gate queue for the 50-task evaluation, ordered by `rank_BT + rank_acc`; unevaluated nodes inherit the
parent's accuracy. "Disaggregated" = expansion and evaluation run concurrently, non-blocking.
Reported 35.1% on Polyglot (o3-mini, gpt-5.4 judge) vs DGM 30.7%, at ~$87–150 API and 2–5 h wall
clock; HGM needed 347 CPU-h for 30.5% on Qwen3-30B. No code linked.

**Verdict — weak (as a competitor), reference (as a design).** A single algorithm in a paper, on
the harness side of [`related-work.md`](related-work.md) § Three layers — no interface, one
benchmark, no stopping rule, no budget meter beyond reporting cost. It optimizes agent source, not
a prompt + pipeline configuration, and has no prompt-optimization baselines (no GEPA, DSPy, MIPRO).
Its tree search overlaps ours in shape (visit-penalised selection over a lineage tree — compare
`promptpotter/application/mask/backprop.py` UCB1 and [`../specs/roadmap.md`](../specs/roadmap.md) § Selector members), which
is now common ground in the field, not a moat either side holds.

**Worth taking.** (1) **A zero-sample pre-screen**: judge-ranked candidates before any sample is
bought, as a prior for PoBB's arm order or a cut before measurement — the same question as
the selector members' gate — whether a self-reported prediction carries signal — with a published
ρ to beat. (2) The **easy-subset gate** (cheap smoke tasks before the real split). (3) One
acquisition mixing measured rank, predicted rank and a visit penalty — a worked instance of the
single-acquisition direction the selector members argue for. (4) Their cost table (expansion vs
judge vs evaluation per step) is the accounting our budget meter should be able to print.
Their method as a PromptPotter preset is [`../specs/roadmap.md`](../specs/roadmap.md) § SIFT
reproduction.

**Checked** 2026-09-24 — v1 only, no repository linked in the paper.

## RRSI — Regularized Recursive Self-Improvement of Agent Harnesses (Google Research)

**What.** Xia et al., [arXiv 2609.24972](https://arxiv.org/abs/2609.24972) (2026-09-21), code at
[`google-research/rrsi`](https://github.com/google-research/rrsi) (Apache-2.0),
[project page](https://regularized-rsi.com/). Same harness-evolution line as SIFT and the Darwin
Gödel Machine: a frozen model, and a loop that proposes and selects edits to the **harness**
(prompts, control flow, tools, memory, sub-agents). Its claim is that the loop overfits the tasks
it evolves on, and its answer is to leave the edit space open and regularize the search instead.
Proposal side: an annealed cap on how many independent edits one candidate may bundle, a proposer
conditioned on the full edit history, and a redirect to untried components when a run stalls.
Selection side: a critic screens each candidate for suite-specific logic **before** it is
evaluated (a regex denylist plus an LLM review), a noise-adjusted floor rejects gains inside
evaluation variance, a cost rule makes added inference tokens pay for themselves in measured gain,
and components that stopped helping are pruned. Three instances — Terminal-Bench 2.1, Harvey LAB,
EngDesign — each evolved on one suite and then run unchanged on held-out benchmarks.

The number worth remembering is the ablation (Table 2, Harvey LAB only), not the headline:

| Variant | Evolve | ID held-out | OOD avg. | Tokens/trial (M) |
|---|---|---|---|---|
| H_0, no evolution | 89.4 | 86.9 | 39.7 | 1.56 |
| Unregularized evolution | 92.8 | 88.9 | 40.3 | 3.80 |
| RRSI | 90.5 | 89.2 | 43.6 | 2.42 |

The unconstrained loop posts the best score of any variant on the tasks it practiced, +0.6 out of
distribution, at 2.4× the tokens. The abstract's headline is up to +6.0 on the evolve split and up
to +4.3 out of distribution.

**Verdict — reference (as a design), not yet decided as a constraint.** It optimizes agent source
on the harness side of [`related-work.md`](related-work.md) § Three layers, with no
prompt-optimization baselines, so it is not a peer for M13. What it measures is the failure an
outer loop of ours would have: a gain on the evolve set that does not transfer. Whether that binds
L4 is a call for [`external-constraints.md`](external-constraints.md) once the paper has been read
through, not one this entry makes.

**Worth taking.** (1) **The noise-adjusted floor**: a per-instance noise band δ, bootstrapped over
trials of the base evaluation (`rrsi/calibrate.py`), that a gain must clear — the same question
PoBB answers, asked as a fixed threshold. (2) **The leakage critic before evaluation**: reject a
candidate for benchmark-specific logic without paying to score it. (3) **The cost rule**: an
accepted edit must buy its added tokens with measured gain. (4) **The annealed edit budget**, so a
measured change stays attributable to one mechanism. (5) The reporting shape: evolve, in-distribution
held-out and out-of-distribution as separate columns beside tokens per trial.

**Checked** 2026-10-11 — README, abstract and Table 2 read from the arXiv HTML; the method and the
other two domains' results not read through, and the operator has not read it. Last push
2026-09-23, ~1.4k stars. The README's results table lists JobBench (OOD) at +4.7, above the
abstract's "up to 4.3" — unreconciled.

## DSPy — GEPA, MIPROv2, BootstrapFewShot

**What.** [`stanfordnlp/dspy`](https://github.com/stanfordnlp/dspy) (MIT): a program of typed
modules whose prompts are compiled by a pluggable optimizer — `BootstrapFewShot` (demonstrations
from traces that pass the metric), `MIPROv2` (Bayesian search over instructions × demos on
minibatches), `dspy.GEPA` (reflective Pareto evolution of instruction text). Each optimizer takes
the user's own metric function and its own trial or rollout budget.

**Verdict — relevant.** Argued in [`related-work.md`](related-work.md) § The family PromptPotter's
own algorithm belongs to and § Against GEPA and AlphaEvolve; what it costs a DSPy user to swap in
ours is [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md). For M13 it is the
obvious first peer: three optimizers already behind one `compile()` call, none sharing a budget
meter or a grader with the others.

**Worth taking.** Already taken where it matters — reach, by shipping as a DSPy optimizer.

**Checked** 2026-09-24 — last push 2026-09-23, ~38k stars, not archived.

**GEPA `optimize_anything`, checked 2026-10-19.** GEPA outside DSPy now optimizes any scored text
artifact (whole agent files, code, rendered SVG and 3D scripts) in three modes, with rendered images
as reflection feedback; presented on the AI Engineer World's Fair main stage. It narrows the scope
line in [`related-work.md`](related-work.md) § Against GEPA and AlphaEvolve, and its ARC-AGI-1
headline (32.5% → 89.5%, Gemini 3 Flash) is measured against its own seed only, below what the same
model reaches with reasoning set to High. Reading, numbers and the experiments it asks for:
[`../../paper/notes/gepa-optimize-anything-aie-2026.md`](../../paper/notes/gepa-optimize-anything-aie-2026.md).

## Promptolution (LMU / Freiburg / TUM)

**What.** A research framework ([`gepromptet/promptolution`](https://github.com/gepromptet/promptolution),
[EACL 2026 demo](https://aclanthology.org/2026.eacl-demo.21/)): one backend, a response cache,
token logging, and its own CAPO, EvoPrompt and OPRO. **Verdict — weak as a product, relevant as a
peer:** the closest research-side bench, but no stopping rule beyond each optimizer's own, no
subset-invariant score, no interface past Python; argued in [`related-work.md`](related-work.md).
**Worth taking:** CAPO as the first non-DSPy peer — racing under a token budget is PoBB's nearest
published neighbour. **Checked** 2026-09-24 — last push 2026-09-16.

## Promptomatix (Salesforce AI Research)

**What.** A zero-configuration DSPy front end ([`SalesforceAIResearch/promptomatix`](https://github.com/SalesforceAIResearch/promptomatix))
that generates a synthetic train/test set from a task description. **Verdict — weak:** no new
search, no budget meter or stopping rule seen. **Worth taking:** synthetic data as an on-ramp for a
user with no dataset. **Checked** 2026-09-24 — last commit 2026-06-02.

## Evidently Prompt Optimizer

**What.** A `PromptOptimizer` in `evidently` ([blog](https://www.evidentlyai.com/blog/automated-prompt-optimization)):
one hill-climb over a 40/40/20 split with early stopping, aimed at LLM-judge prompts. **Verdict —
weak:** a held-out split, but no cost accounting. **Worth taking:** the judge prompt as a target in
its own right — our grader node is a prompt too. **Checked** 2026-09-24.

## agent-opt (Future AGI)

**What.** Six optimizers behind one package ([`future-agi/agent-opt`](https://github.com/future-agi/agent-opt)):
random, Optuna, ProTeGi, meta-prompt, PromptWizard, GEPA. **Verdict — weak:** a menu, which
[`related-work.md`](related-work.md) § A menu is not a bench answers. Nothing to take Opik does not
show. **Checked** 2026-09-24 — last push 2026-06-30.

## Promptfoo

**What.** Declarative evaluations and red-teaming ([`promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo),
acquired by OpenAI 2026-03-09); nothing searches. **Verdict — weak.** **Worth taking:** its YAML
test matrix as a read-in format — a dataset plus graders a team already wrote. **Checked** 2026-09-24.

## llm-prompt-optimizer (MCP server)

Deterministic request-compression middleware behind MCP tools
([`Divyesh-freelance/llm-prompt-optimizer`](https://github.com/Divyesh-freelance/llm-prompt-optimizer)),
no dataset or scoring loop. **Verdict — weak:** name collision only. **Checked** 2026-09-24.

## Arize Phoenix and W&B Weave

**What.** Telemetry-first: [Phoenix](https://github.com/Arize-ai/phoenix) (Elastic License 2.0,
span replay, a commercial meta-prompt optimizer "Prompt Learning") and
[Weave](https://github.com/wandb/weave) (tracing and evaluations; no optimizer). **Verdict — weak,
both.** **Worth taking:** span replay — rerun one step of a chain from a captured trace — as a
node-level probe. **Checked** 2026-09-24; Prompt Learning read from summaries only.

## LangSmith

The platform [Promptim](#promptim-langchain) runs on; that entry covers it. **Verdict — weak.**
**Checked** 2026-09-24, via Promptim only.

## AlphaEvolve (Google DeepMind / Google Cloud)

**What.** An evolutionary coding agent: Gemini proposes edits inside `EVOLVE-BLOCK` markers, a
program database keeps the population, a user evaluator scores. Now a **managed Google Cloud
service** (Gemini Enterprise): generation (prompt sampler, LLM ensemble, program database) runs on
Google's side; the user's `run_controller_loop()` pulls candidates, runs *their* evaluator wherever
they like, and posts scores back. Budget is `MAX_PROGRAMS_GENERATED` / `_EVALUATED`.

**The local checkout is not OpenEvolve.** `AlphaEvolve-from-OpenEvolve-Questionmark/` is a clone of
[`Google-Cloud-AI/alphaevolve-on-googlecloud`](https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud)
(Apache-2.0), Google's client library and examples for the managed service; only its TSP example
cites OpenEvolve, as inspiration. OpenEvolve is the separate open reimplementation,
[`algorithmicsuperintelligence/openevolve`](https://github.com/algorithmicsuperintelligence/openevolve)
(formerly `codelion/openevolve`, Apache-2.0).

**Verdict — relevant.** Argued in [`related-work.md`](related-work.md) § Against GEPA and
AlphaEvolve. The search itself is closed; only the evaluator side is code the user sees.

**Worth taking.** The controller shape — the service proposes, the client's loop evaluates and
posts scores back — is the inverse of our plug point and a direct template for it: an external
proposer that only emits candidates and receives scores.

**Checked** 2026-09-24 — local checkout at 2026-07-10 (`8693985`); OpenEvolve last push
2026-07-18, ~7.4k stars; `google-deepmind/alphaevolve_results` last push 2026-01-05.

## Palantir AIP (AIP Evals)

**What.** [AIP Evals](https://www.palantir.com/docs/foundry/aip-evals/overview) grid-searches
human-written prompt and model variants as separate suite runs inside Foundry. **Verdict — weak:**
nothing proposes, nothing stops early. **Checked** 2026-09-24.

## NVIDIA SkillEvaluator

**What.** [`NVIDIA/skillevaluator`](https://github.com/NVIDIA/skillevaluator)
([arXiv 2608.20614](https://arxiv.org/abs/2608.20614)), the gate for NVIDIA's Verified Skills
catalog, in three tiers: validation (static checks, a rubric, an LLM security pass), deduplication
(embedding overlap within a skill and across a `--catalog`), and live evaluation (paired trials with
and without the skill, reported as *Skill Lift*). **Verdict — not a competitor:** it evaluates one
agent design and nothing proposes, searches or stops; a skilled agent is a configuration of our
harbor connector plus one `SKILL.md`. **Worth taking:** the three tiers, adopted for skilled-agent
targets ([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving agent harnesses) — the baseline is
the skill *absent*, and overlap is checked against the library. Its sibling scanner
[SkillSpector](https://github.com/nvidia/skillspector) is the class
[`external-constraints.md`](external-constraints.md) § SKILL names for our tier-1 security floor.
**Checked** 2026-09-24 — README only.

## SoL-Pi (NVIDIA Labs)

**What.** [SoL-Pi](https://nvlabs.github.io/SoL-Pi/) is a coding-agent harness: four opt-in
mechanisms on top of [Pi](https://github.com/NVlabs/SoL-Pi) (Action Fusion, ObservationPack,
Evidence-Preserving Reducer, Online Context Compact), each switched on in `sol-pi.json`. An
auto-research loop found them, framed as "an open-ended RSI problem". The loop has a batched
proposer, with *Oracle Analysis* that estimates opportunity from existing trajectories before it
spends rollouts. Map-reduce analyzers read each trajectory, and an implementer and a reviewer work
inside a Ralph Loop. Candidates pass two evaluation stages, in-trajectory and then held-out, and
"no agent inside the auto-research loop sees the held-out results." The acceptance gate is
predeclared: every capability metric stays within tolerance, at least one efficiency metric
improves, and the nondominated survivors are kept. 152 directions went in and 4 survived.
EdgeBench: about 94% of Pi's score at 45–49% fewer tokens. Terminal-Bench: 15 solved against Pi's
18, at about 26% less cost. MIT.

**Verdict — reference, on the recursive self-improvement axis.** It is the same family as Sakana's
Darwin Gödel Machine. An agent loop rewrites a coding agent's own harness code, which is the one
axis PromptPotter has not designed. It is not a peer optimizer and not a bench arm. It accepts on a
tolerance gate rather than a lift. The page states no global stopping rule, no budget allocation
across lineages and no measurement archive.

**Worth taking.**
- The acceptance gate as a selector shape: capability held within a declared tolerance, cost as
  the objective.
- Oracle Analysis, which prices an idea against trajectories already paid for before it buys
  rollouts.
- The rule that the proposer never sees held-out results.

**Checked** 2026-10-20: project page and README. The paper, the loop's code and the plug points
were not read.
