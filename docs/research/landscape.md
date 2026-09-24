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

**What.** `pip install promptim`, a CLI over LangSmith: `promptim create task` scaffolds a
`config.json` + `task.py`, `promptim train` runs the search. The config names a LangSmith
`dataset`, an `initial_prompt` (a Prompt Hub identifier, pulled *with* its model), `evaluators`
(an import path to functions `(run, example) -> {key, score, comment}`) and plain-language
`evaluator_descriptions`. The default loop is a minibatch hill-climb — score on train, a metaprompt
rewrites from the scores and comments, keep the rewrite only if dev improves; later versions add
MIPRO, PhaseEvo, debate, few-shot and feedback-guided optimizers. Every evaluation is written back
as a LangSmith **Experiment** on the user's dataset, baseline and final are a tagged test-split
pair, human review goes through Annotation Queues, and the winner is committed to the Prompt Hub.
Announced in [the LangChain blog post](https://www.langchain.com/blog/promptim), Nov 2024; source
[`hinthornw/promptimizer`](https://github.com/hinthornw/promptimizer).

**Verdict — weak.** A single-prompt optimizer with no budget meter, no stopping rule and a
keep-if-dev-improved acceptance test that is exactly the winner's-curse exposure
[`benchmarks.md`](benchmarks.md) rules on. It cannot run without LangSmith. None of the properties
`related-work.md` § A menu is not a bench argues from is present, so there is nothing to argue
against.

**Worth taking — the LangSmith round trip, as a template.** It is the cleanest existing picture
of an optimizer that lives *inside* a team's evaluation tool: their dataset in, their prompt in, every
measurement visible as an Experiment they can compare in their own UI, the winner back in their
Prompt Hub. That is the shape our read-in / write-back lane in
[`../specs/roadmap.md`](../specs/roadmap.md) is aiming at. Smaller: the two-command onboarding
(`create task` → `train`), and `evaluator_descriptions` — metric meaning handed to the proposer as
prose. Its email-triage experiments (`experiments/email_cs*`) are the same task as our
`email-tagging` dataset, graded the same way (exact match on the predicted category), which makes
them a ready-made demo pairing.

**Checked** 2026-09-24 — last commit 2025-04-17 (`v9`, PyPI `0.0.9`), not archived.

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
user's codebase rather than at our own layers. And the UI's layout grammar, as a design reference
rather than code to fork: a left nav grouped by lifecycle (observe → evaluate → engineer →
optimize → production), a list with a split inspector (span tree beside a Pretty/YAML detail
pane), and J/K stepping through rows.

**The door this opens — PromptPotter reached THROUGH Opik.** Opik already sells optimization as
a menu of algorithms behind one API, GEPA among them, and its own pitch against Langfuse is that
seeing a bad answer is not enough — something has to fix it. So an Opik user is already a buyer
of what we do, one API call away from it. The interop shape worth building is the Promptim one
above, pointed at Opik: their dataset and traces in, every measurement visible as an Opik
experiment, the winner back in their prompt library — with PromptPotter either registered as one
more optimizer behind that API (a `BaseOptimizer` subclass,
[`external-constraints.md`](external-constraints.md) § M13) or driving Opik's six through an
adapter that strips their own evaluation, since each is a whole loop rather than a candidate
source (the plug point in [`../specs/roadmap.md`](../specs/roadmap.md) § The optimizer plug point). Either way the pitch is
the bench against the menu: their algorithms, our budget, stopping rule and comparability guard.

**Vendor-stated numbers** (Comet's
[Langfuse vs. Opik](https://www.comet.com/site/products/opik/compare/langfuse-vs-opik/) page,
updated 2026-07-30; rows "from each vendor's public documentation as of August 2026"): GitHub
stars on 2026-08-13 — Langfuse 33,270, Opik 21,433, Arize Phoenix 11,088, W&B Weave 1,118. Free
tier — Opik 10 seats, Langfuse 2; entry paid tier — Opik $19/month, Langfuse $29/seat/month. Opik
claims 21 heuristic and 22 LLM-as-judge metrics, thread-level evaluation and guardrails, and marks
all three "not documented" for Langfuse. A competitor's own table: re-check before quoting.
Comet keeps one such page per rival at the [compare hub](https://www.comet.com/site/products/opik/compare/)
— the fastest way to refresh this whole section.

**Checked** 2026-09-24 — Opik docs list six optimizers (a Comet product page says seven);
Langfuse under ClickHouse since 2026-01-16. Opik `LICENSE` read (Apache-2.0), last commit
2026-09-24; Langfuse's licence not re-read.

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
`mask/backprop.py` UCB1 and [`../specs/parent-selection.md`](../specs/parent-selection.md)), which
is now common ground in the field, not a moat either side holds.

**Worth taking.** (1) **A zero-sample pre-screen**: judge-ranked candidates before any sample is
bought, as a prior for PoBB's arm order or a cut before measurement — the same question as
parent-selection.md § Open "whether a self-reported prediction carries signal", with a published
ρ to beat. (2) The **easy-subset gate** (cheap smoke tasks before the real split). (3) One
acquisition mixing measured rank, predicted rank and a visit penalty — a worked instance of the
single-acquisition direction parent-selection.md argues for. (4) Their cost table (expansion vs
judge vs evaluation per step) is the accounting our budget meter should be able to print.

**Checked** 2026-09-24 — v1 only, no repository linked in the paper.

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

## Promptolution (LMU / Freiburg / TUM)

**What.** "A unified, modular framework for prompt optimization" for researchers
([EACL 2026 demo](https://aclanthology.org/2026.eacl-demo.21/), arXiv 2512.02840). Optimization
stage only: one LLM backend (API, local, vLLM), response caching, token-usage logging, and four
optimizers in `promptolution/optimizers/` — **CAPO**, **EvoPrompt** (DE and GA) and **OPRO**. The
same group publishes CANTANTE and MO-CAPO on top of it. Apache-2.0.

**Verdict — weak (as a product), relevant (as a peer).** It is the closest thing to a bench on
the research side — shared backend, shared cache, token logging — but it has no stopping rule
beyond each optimizer's own, no subset-invariant score and no interface past the Python API. Named
in [`related-work.md`](related-work.md) § Three layers (menu row) and § The family (CAPO beat GEPA
in its head-to-head). The menu row overstates it slightly: four optimizers, all its own lineage,
not a wrapper around third-party ones.

**Worth taking.** CAPO as the first non-DSPy peer to plug in — racing plus an explicit token
budget is the closest published neighbour to PoBB, so a head-to-head there is the sharpest one.

**Checked** 2026-09-24 — repo is [`gepromptet/promptolution`](https://github.com/gepromptet/promptolution)
(the secondhand URL is right; `automl/promptolution` and `finitearth/promptolution` both redirect
there, and the README badges still say `automl`). Last push 2026-09-16, last `main` commit
2026-05-24; 158 stars.

## Promptomatix (Salesforce AI Research)

**What.** [`SalesforceAIResearch/promptomatix`](https://github.com/SalesforceAIResearch/promptomatix)
([arXiv 2507.14241](https://arxiv.org/abs/2507.14241), Apache-2.0). A zero-configuration wrapper:
it reads a raw task description, classifies the task, **generates a synthetic train/test set**,
picks a DSPy module and metric, and optimizes through a DSPy or meta-prompt backend, with a
session log, human feedback, a CLI and a REST API.

**Verdict — weak.** A DSPy front end, not a new search method; no budget meter or stopping rule
was visible in the README.

**Worth taking.** Synthetic data as an on-ramp for a user with no dataset — the gap our `new
<file.csv>` path assumes away.

**Checked** 2026-09-24 — last commit 2026-06-02 (a `SECURITY.md` upload); 977 stars. The
README's install line clones `airesearch-emu/promptomatix`, which 404s.

## Evidently Prompt Optimizer

**What.** A `PromptOptimizer` class in the open-source `evidently` library (Apache-2.0), described
in [How we built open-source automated prompt optimization](https://www.evidentlyai.com/blog/automated-prompt-optimization)
(2026-01-01) and [Evidently 0.7.10](https://www.evidentlyai.com/blog/llm-judge-prompt-optimization).
A loop over three pluggable parts — Strategy, Executor, Scorer — with two strategies ("ask the LLM
to improve" and mistake-driven feedback), a 40/40/20 train/val/test split, and early stopping on
max iterations, minimum score gain or a target score. Aimed first at **LLM-judge prompts**
trained from labelled data.

**Verdict — weak.** One hill-climb with a held-out test split — sounder than keep-if-dev-improved,
but no cost accounting was described.

**Worth taking.** Judge prompts as an optimization target in their own right: our grader node is
a prompt too, and "fit the judge to expert labels" is a campaign we could run on it.

**Checked** 2026-09-24 — blog read; repo `evidentlyai/evidently` last push 2026-09-11.

## agent-opt (Future AGI)

**What.** [`future-agi/agent-opt`](https://github.com/future-agi/agent-opt), `pip install
agent-opt`, Apache-2.0 (confirmed). Six optimizers, confirmed as files under
`src/fi/opt/optimizers/`: random search, Bayesian (Optuna), ProTeGi, meta-prompt, PromptWizard,
GEPA. LiteLLM for models, Future AGI's `ai-evaluation` for 50+ metrics, traces from `traceAI` as
training data, winner deployed through their gateway.

**Verdict — weak.** Named in [`related-work.md`](related-work.md) § Three layers as a menu; the
argument of § A menu is not a bench applies unchanged.

**Worth taking.** Nothing Opik does not already show.

**Checked** 2026-09-24 — last `main` commit 2026-04-27, last push 2026-06-30; 75 stars.

## Promptfoo

**What.** [`promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo) (MIT): a CLI and library
for declarative evaluations (a prompts × providers × test-cases matrix with assertions, a local web
viewer, CI integration) and red-teaming. No optimizer in the README. OpenAI announced its
acquisition on [2026-03-09](https://openai.com/index/openai-to-acquire-promptfoo/) and said the
open-source tools stay maintained.

**Verdict — weak.** An evaluation harness; nothing searches.

**Worth taking.** Its YAML test matrix and assertion vocabulary as a read-in format — a promptfoo
config is a dataset plus graders a team already wrote.

**Checked** 2026-09-24 — last push 2026-09-24, ~25k stars.

## llm-prompt-optimizer (MCP server)

**What.** [`Divyesh-freelance/llm-prompt-optimizer`](https://github.com/Divyesh-freelance/llm-prompt-optimizer)
([glama listing](https://glama.ai/mcp/servers/Divyesh-freelance/llm-prompt-optimizer)), MIT. Not a
prompt optimizer in our sense: deterministic middleware that compresses a coding-agent request and
attaches minimal repo context (intent guard, classification, dependency resolution, a 0.90
similarity floor, drift detection) behind nine MCP tools. No dataset, no scoring loop.

**Verdict — weak.** Name collision only.

**Checked** 2026-09-24 — 3 commits, last 2026-04-28; 0 stars; glama marks it inactive.

## Arize Phoenix and W&B Weave

Both sit in the telemetry row of [`related-work.md`](related-work.md) § Three layers beside
Langfuse, and are compared with it (and Opik) on Comet's page quoted above.

**What — Phoenix.** [`Arize-ai/phoenix`](https://github.com/Arize-ai/phoenix): tracing, evaluations,
datasets and experiments, a prompt store with span replay. Licence is **Elastic License 2.0**, not
OSI open source. Arize's search offering is **Prompt Learning**, a meta-prompt optimizer over
English evaluation feedback, documented under the commercial Arize AX SDK with a Phoenix cookbook
([docs](https://arize.com/docs/ax/develop/prompt-learning)); Arize has benchmarked it against GEPA
on its own blog.

**What — Weave.** [`wandb/weave`](https://github.com/wandb/weave) (Apache-2.0): `weave.op`
tracing and evaluations; the README says the rest of the codebase is paused. No optimizer found.

**Verdict — weak, both.** Telemetry-first; Phoenix's Prompt Learning is one more single algorithm.

**Worth taking.** Span replay — rerun one step of a chain from a captured trace — is the
node-level probe our pipeline search would want against a real trace.

**Checked** 2026-09-24 — both last pushed 2026-09-24; Phoenix ~11.6k stars, Weave ~1.1k. Prompt
Learning read from search summaries, not its source.

## LangSmith

The platform [Promptim](#promptim-langchain) runs on — datasets, Experiments, Annotation Queues,
Prompt Hub; that entry covers it. No first-party optimizer beyond Promptim was checked.

**Verdict — weak.** **Checked** 2026-09-24, via the Promptim entry only.

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

**What.** Inside Foundry, [AIP Evals](https://www.palantir.com/docs/foundry/aip-evals/overview)
tests AIP Logic, chatbot or code functions against evaluation suites (LLM-as-judge, ROUGE, custom
evaluators, intermediate parameters). Its [experiments](https://www.palantir.com/docs/foundry/aip-evals/experiments)
treat prompts and models as hyperparameters and **grid-search** every combination as separate
suite runs, compared by parameter group or up to four runs side by side. Search summaries also
mention a results analyzer that clusters failures and suggests prompt edits — not verified at the
source.

**Verdict — weak.** Exhaustive grid over human-written variants, locked to Foundry. Nothing
proposes, nothing stops early.

**Checked** 2026-09-24 — docs pages, undated.

## NVIDIA SkillEvaluator

**What.** [`NVIDIA/skillevaluator`](https://github.com/NVIDIA/skillevaluator) is an open-source
framework for evaluating agent skills: instructions plus supporting files that an agent loads. It
is the gate for NVIDIA's Verified Skills catalog. It works in three tiers:

1. **Validation.** "Safe & well-formed?" Static and dependency checks, a rubric, an LLM security
   pass, and optional scanners (Semgrep, Gitleaks).
2. **Deduplication.** "Repeated or overlapping guidance?" Embedding-based overlap within a skill,
   and across skills when you pass it a `--catalog`.
3. **Live evaluation.** "Does it help the agent?" Paired trials with and without the skill,
   reported as *Skill Lift*, over a four-bucket synthetic dataset or an existing one. The agents
   run in Docker (Claude Code, Codex, OpenCode).

Paper: *Evaluating Skills, Not Just Agents*, [arXiv 2608.20614](https://arxiv.org/abs/2608.20614).

**Verdict — not a competitor.** It covers a small slice of what we do. It supports **one agent
design**, an agent equipped with skills, and it only **evaluates** that design: nothing proposes a
better skill, and nothing searches or stops. PromptPotter can build that design itself. A skilled
agent is a configuration of our agent toolkit (the harbor connector plus one `SKILL.md` per
episode) and is close to ready. What our toolkit adds is optimizing each skill, which
SkillEvaluator does not do.

**Worth taking.** The three tiers, adopted as the principle for skilled-agent targets
([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving agent harnesses). Specifically:
- the baseline is the skill being *absent*, not the previous version of it;
- checking overlap against the rest of the library is what keeps many skills maintainable.

Its validation tier has a sibling in NVIDIA's own [SkillSpector](https://github.com/nvidia/skillspector)
(71 patterns; a risk score above 50 means do not install), and a scan of that class before a
candidate is measured is the long-run form of our tier-1 security floor
([`external-constraints.md`](external-constraints.md) § SKILL).

**Checked** 2026-09-24 — README only; the four buckets and the exact Skill Lift statistic were not
read at the source.
