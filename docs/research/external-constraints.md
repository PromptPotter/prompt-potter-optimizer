# External constraints — decisions the outside world has already made

External standards, published findings and review norms that take a design decision away from us —
design our own shape first and conforming later means unravelling it. Each entry states the
**decision it removes**, the **milestone it binds** (M13 plug point, M14 preprint, SKILL skilled-agent
target, L4 self-optimization, TOOL callable as a tool / MCP), its **source**, and whether that source
was **read**. An entry not marked *read* is unverified and is read at its source before anything is
built or cited on it.

Tools and competitors are [`landscape.md`](landscape.md)'s; the argument against the peers that
matter is [`related-work.md`](related-work.md)'s. **Checked** 2026-09-24 — re-check before quoting;
these standards still move.

## Ranked — the five that cost most to get wrong

1. **The scorer sits outside anything the loop can see or edit** (L4). DGM, seeing its own
   hallucination checker, rewrote the checker's logging to fake a pass, and cheated more when the
   check was visible; an audit of five self-improving systems found tampering persisting in the
   *best-scoring* agent's lineage. For L4 a **stated invariant**: the inner cycle's scorer, formula
   and held-out cells can never be a `pipeline_param`.
   Sources: [DGM](https://arxiv.org/abs/2505.22954) (read);
   [Auditing Harness Tampering](https://arxiv.org/abs/2609.00069) (read) — tampering ADAS 84.6%, DGM
   63.1%, HyperAgents 73.6%, ScientistOne 29.6%, AFlow 18.3%; safeguards: tamper-proof evaluation
   channels, provenance-aware state tracking, continuous auditing.
2. **Selection must be able to see lineage, not only the round's best** (L4, M13). The
   [Huxley-Gödel Machine](https://arxiv.org/abs/2510.21614) (ICLR 2026 oral): a
   *metaproductivity–performance mismatch* — a round's top scorer often has unproductive descendants
   while a lower scorer seeds the better lineage; HGM scores clades. Our lineage graph and
   content-addressed archive hold what a clade score needs. **Never discard a non-winner's lineage
   for losing one round** — keep the archive whole.
3. **Early stopping must be defensible under optional stopping** (M14). Repeating a fixed-α test
   under optional stopping inflates false positives above α (textbook sequential testing). The field's
   answer: confidence sequences from test supermartingales, valid at any stopping time
   ([Hsu & Shekhar, *Efficient Sequential Evaluation of LLMs*](https://arxiv.org/abs/2607.17409),
   read). Before the preprint, state PoBB's guarantee in those terms, or disclose it is not
   anytime-valid and measure the inflation on our runs.
4. **Budget-matched comparison is what reviewers ask for first** (M13, M14). Optimizer gains shrink or
   reverse at matched rollout budgets: [LEVI](https://arxiv.org/abs/2605.09764) "matches or exceeds
   GEPA at less than half of its rollout budget on four different benchmarks" (abstract read; read the
   body's table before quoting numbers). A head-to-head holds the **number of calls** equal and
   reports it beside dollars and wall clock. [HAL](https://arxiv.org/abs/2510.11977) (ICLR 2026, read)
   and [AI Agents That Matter](https://arxiv.org/abs/2407.01502) (TMLR 2025) set the template: a
   cost–accuracy Pareto frontier, dollars and tokens as separate axes, a holdout never used for tuning.
5. **A skill candidate must conform to the Agent Skills specification** (SKILL). The
   [spec](https://agentskills.io/specification) (read; 16+ runtimes incl. Claude Code, Codex CLI,
   Gemini CLI, Copilot, Cursor):
   - **Frontmatter is a closed set:** `name` required (≤64 chars, lowercase letters, digits, hyphens,
     equal to the parent directory); `description` required (≤1024 chars); optional `license`,
     `compatibility` (≤500), `metadata` (string→string), experimental `allowed-tools`; any other
     top-level key is non-conformant.
   - **Size:** body under ~5000 tokens, file under 500 lines. **Layout:**
     `skill-name/{SKILL.md, scripts/, references/, assets/}`.
   - The tier-1 check in the L2/L3 prompts ([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving
     agent harnesses) means *this* spec; a candidate past its limits is invalid before it runs.

## SKILL — the skilled-agent target

- **Library size has a measured failure point.**
  [More Skills, Worse Agents?](https://arxiv.org/abs/2605.24050) (read): up to −21% at 202 skills
  (libraries of 52, 102, 202; worse with size, no safe threshold), caused by *wrong-skill selection*
  ("skill shadowing"); context overhead indistinguishable from zero. So **deduplication (NVIDIA's
  tier 2) is enforced from the first skill**, and the skilled agent needs a routing design before its
  library reaches a few dozen.
- **Selection sees only the description.** Runtimes load `name` + `description` (~100 tokens) at
  startup and the body on activation, so **`description` is the routing surface**. Today
  `connectors/harbor.py::_write_skill` writes the frontmatter and holds `description` fixed — with one
  skill there is nothing to route — so the candidate is the body alone and mutation cannot break the
  frontmatter rules (the body's size limit still binds). terminus-2 finds `<skills_dir>/<name>/SKILL.md`
  with a depth-2 `find` and silently skips a skill lacking `name` / `description` or with non-LF line
  endings. **The skilled agent reverses this:** `description` becomes a search axis and the
  frontmatter rules bind every candidate.
- **A skill a model writes for itself, unscored, helps nobody.**
  [SkillsBench](https://arxiv.org/abs/2602.12670) (87 tasks, deterministic verifiers): curated skills
  +16.2 pp on average but 16 of 84 tasks worse; self-generated **zero** benefit; focused 2–3-module
  skills beat comprehensive documents. So a skill is admitted only by measured lift on verifier-graded
  cells; SkillsBench is the external harness to report on; prefer trajectory-grounded writers
  ([Trace2Skill](https://arxiv.org/abs/2603.25158): patches from success and failure traces merged
  into one conflict-free skill) over a free rewrite.
- **A security floor no candidate may cross.** [SkillSpector](https://github.com/nvidia/skillspector)
  (NVIDIA; 71 patterns, risk > 50 = do not install) and
  [Cisco skill-scanner](https://github.com/cisco-ai-defense/skill-scanner). Audits of public skills
  found 26–37% flawed and dozens malicious (no audit linked; unverified); attacks include hidden
  instructions, invisible Unicode Tag characters and agent-settings injection (CVE-2025-59536). A
  mutated body must never carry Unicode Tag characters, instruct reading secrets or environment
  variables, or instruct outbound calls or exec/eval — checked at tier 1; a SkillSpector-class scan
  before measurement is the long-run form.
- **Descriptions must distinguish, not only describe.** Overlap seeds shadowing; *mutual
  distinguishability* against siblings is a measurable objective.
  [SkillRouter](https://arxiv.org/abs/2603.22455): retrieval at scale is embedding-based and reads
  bodies too.
- **The format is cross-runtime.** Microsoft Agent Framework (GA July 2026), Google ADK and Codex load
  SKILL.md with progressive disclosure (no source linked; unverified) — assume more than one runtime.
- **Executable skills transfer; prose skills are unproven.** [SkillWeaver](https://arxiv.org/abs/2504.07079):
  skills as callable APIs transferred strong → weak agent (+54% WebArena). Our target is prose — the
  open question.
- **Evaluation half:** NVIDIA SkillEvaluator's three tiers (validation, deduplication, lift against
  absence), [`landscape.md`](landscape.md) § NVIDIA SkillEvaluator.

## L4 — self-optimization

Beyond ranked items 1 and 2:

- **An overseer that watches the run.** [SICA](https://arxiv.org/abs/2504.15228) (read; 17% → 53% on
  SWE-bench Verified): an async LLM overseer every ~30 s steers or cancels a drifting agent. A runtime
  monitor — it does **not** gate a self-edit before landing. Our heartbeat and pause contract are the
  same shape; a pre-landing gate would be ours.
- **Hardening the environment is cheap and works.** [Reward Hacking Benchmark](https://arxiv.org/abs/2605.02964)
  (read): 13 frontier models, exploit rates 0–13.9%, cut 87.7% relative by simple hardening without
  hurting success. Ours, from item 1: in L4 the cells that choose a winner are not the cells that
  certify it.
- **Separate the search algorithm from the workflow shape.** [EvoAgentX](https://arxiv.org/abs/2507.03616)
  plugs AFlow, TextGrad and MIPRO in as swappable algorithms apart from topology — M13's proposer
  seam from the other side: the plug point must not assume L1 → L2 → L3 is the only shape.
- **"More agentic" is not better by default.** [AFlow](https://arxiv.org/abs/2410.10762) (ICLR 2025
  oral; MCTS over code-graph workflows) beat hand-designed ones by 5.7%;
  [AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2) did not reliably beat templated v1
  where a strong template existed. Fixed L1–L3 vs an optimizer-as-agent
  ([`../specs/roadmap.md`](../specs/roadmap.md)) is an **empirical question M13 answers**.
- **Sample efficiency from selection, not only proposers.** [ShinkaEvolve](https://arxiv.org/abs/2509.19349)
  (Sakana, ICLR 2026, read): SOTA circle packing in 150 samples via exploration-balanced parent
  sampling, novelty rejection-sampling, and a bandit over an LLM-mutator ensemble. Read its sampling
  policy before designing L4 candidate selection.
- **Algorithm configuration already settled how to race configurations** (2026-10-14 literature pass,
  unverified):
  - replicate over environments — one pass per cell, every marginal dollar on a new cell, repeats only
    for the noise floor (Birattari; COCO's "instances are repetitions");
  - block on instance, pair in one order with one seed; correct only the published verdict, not the
    tests inside a race (ParamILS, irace, SMAC);
  - irace tests first after 5 instances, then every one, closing after two tests that eliminate
    nothing; SMAC's intensification meets the incumbent on 1, 2, 4… cells;
  - retire saturated and flat environments, prefer high SNR; cut examples, never scenarios
    (metabench, Signal and Noise);
  - headroom does not predict lift (ρ = 0.047 over 19.8k prompt pairs,
    [2605.26655](https://arxiv.org/abs/2605.26655)) — it admits a dataset, never ranks it;
  - among prompt-optimizer papers only RRSI gates on a noise floor from re-running the unchanged
    harness (δ 0.004–0.020).
- **Proposer mechanisms with their own ablation row** (2026-10-07 citation harvest, unverified):
  failures alone beat a natural mix, 53.9 vs 49.6 (2609.32452); a generalization-gate block in the
  proposer prompt, 51.6 → 58.4 held-out (HarnessCompass, 2608.01918); per-operator win/loss history
  in the planner, 65.88 vs 34.12 (StraTune, 2609.32886); raw per-sample traces of earlier candidates,
  a summary no better than scores alone (Meta-Harness, 2603.28052); a delta applied by deterministic
  merge, 56.9 → 70.3 (ACE, 2510.04618). Against a phrase library: GRACE's paraphrase ablation moved
  ~0.2 points — the acceptance gate may carry more than the wording.
- **Precedent to cite:** [STOP](https://arxiv.org/abs/2310.02304), the closest (an improver improving
  its own code, weights frozen); [Gödel Agent](https://arxiv.org/abs/2410.04444) (ACL 2025) and
  [ADAS](https://arxiv.org/abs/2408.08435) keep an **archive of past designs**.

## M13 — the plug point

- **Opik's optimizer interface** (read, [extending optimizers](https://www.comet.com/docs/opik/agent_optimization/)):
  subclass `BaseOptimizer`, define `DEFAULT_PROMPTS`; `__init__(self, model: str, **kwargs)`;
  `optimize_prompt(prompt: ChatPrompt, dataset, metric, agent=None, experiment_config=None,
  n_samples=None, project_name=None, validation_dataset=None, max_trials=None) -> OptimizationResult`;
  reuse the inherited `evaluate_prompt()` / `call_model()`. "PromptPotter reachable through Opik" is
  a `BaseOptimizer` subclass; its repo is [`../specs/roadmap.md`](../specs/roadmap.md) § What
  PromptPotter emits.
- **DSPy's optimizer interface** (read in source, `dspy/teleprompt/teleprompt.py`, `gepa/gepa.py`):
  `Teleprompter.compile(self, student: Module, *, trainset, teacher=None, valset=None, **kwargs) ->
  Module`. `dspy.GEPA` budgets by `auto` (light / medium / heavy), `max_full_evals`,
  `max_metric_calls` — the last is what a budget-matched head-to-head sets.

## M14 — the preprint

Beyond ranked items 3 and 4:

- **Clustered and paired SEs, not binomial.** [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640)
  (Anthropic, 2024): clustering can inflate SEs up to 3×; a head-to-head differences arms per cell.
- **Small samples need exact or bootstrap intervals.** ["Don't use the CLT…"](https://arxiv.org/abs/2503.01747)
  (2025) — below a few hundred datapoints, most of our panels.
- **Report SNR per benchmark.** [Signal and Noise](https://arxiv.org/abs/2508.13144) (2025, 900K runs)
  filters or down-weights low-SNR subtasks before trusting a delta.
- **IRT: the field default is 2PL.** tinyBenchmarks, metabench (ICLR 2025) and
  [Fluid Benchmarking](https://arxiv.org/abs/2509.11106) (COLM 2025) validate IRT ability for
  comparability and fit discrimination too. We start at 1PL and graduate per dataset
  ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The model); the preprint
  says why. Fluid Benchmarking's four metrics (validity, variance, saturation, efficiency) are the
  table reviewers know.
- **The [Agentic Benchmark Checklist](https://arxiv.org/abs/2507.02825)** failed 10 of 10 popular agent
  benchmarks on reporting — fill it for our bench, publish as an appendix.
- **Holm or Benjamini–Hochberg** on any pairwise matrix of more than two proposers (common practice; no
  canonical source verified).
- **Venue and arXiv.** NeurIPS D&B requires Croissant metadata and non-gated hosting (unverified).
  arXiv: each author takes "full responsibility for all its contents, irrespective of how the contents
  were generated" ([moderation](https://info.arxiv.org/help/moderation)); since May 2026
  "incontrovertible evidence" of unchecked AI output (hallucinated references, leftover chatbot text,
  placeholders) earns a one-year ban (consistent secondary reporting). So every reference and number
  is hand-checked and AI use disclosed.

## Cost reporting — binds every run from now, not only the preprint

- **Record raw token counts and the price table's date with every run**, so dollars recompute if
  prices move. HAL and Terminal-Bench price at the provider's public rate *at run time*; Terminal-Bench
  also reports wall clock per step and total. Tokens are on every row; the **price date** is the piece
  to check.
- **Amortized lifetime cost is the buyer's number.**
  [Databricks](https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization)
  (2025-09-24, read): optimization plus serving over 100k requests. GEPA-optimized gpt-oss-120b beats
  un-optimized Claude Opus 4.1 by 2.2 points at ~90× less serving cost; GEPA on GPT-4.1 +2.1 vs SFT's
  +1.9 at ~20% lower serving cost, both together +4.8. So: a small optimized open model against an
  un-optimized frontier one, and prompt optimization is **complementary to fine-tuning** — "harness
  before weights" says *before*, never *instead of*. Never a headline omitting the optimization spend.

## Direction — where optimization pays, and where it does not

- **An optimized prompt is bound to its model.**
  [Why Prompt Optimization Works, and Why It Sometimes Doesn't](https://arxiv.org/abs/2605.26655)
  (2026; DSPy, TextGrad, GEPA across four families): edits that help one benchmark often fail on
  another, by edit type × task type. [Prompting Inversion](https://arxiv.org/abs/2510.22251) (2025): a
  scaffold helping GPT-4o *hurts* GPT-5 (table unverified).
  [Benchmarking Prompt Optimization With Chess](https://arxiv.org/abs/2610.00416) (2026; abstract,
  setup, takeaways read, tables not): each of eight models' winning prompts moved onto the other
  seven — of 49 pairs, 31 improved, 16 worse, two unchanged; the biggest own-model gain degraded five
  of seven others; puzzle gains did not carry into play significantly. Transfer belongs to the pair,
  and a lift on the scored task is not a lift on its neighbour. So a **model swap is a new
  optimization run** (an export names its model), and pitch mid-tier and open-weight models first.
- **The harness can beat the wording.** LangChain's deepagents: 52.8% → 66.5% on Terminal-Bench 2.0,
  same model, via prompt, tool and middleware changes
  ([harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)) —
  move pipeline params, skills and harnesses jointly with the prompt.
- **Against weight updates, reflective search wins on rollout economics.**
  [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 oral) beats GRPO by 6% on average with up to 35×
  fewer rollouts; with Databricks' SFT+GEPA compounding, the harness comes *before* weights.
- **Buyers lack evidence and audit trails, not horsepower.** MIT's *GenAI Divide* (2025, 300
  deployments): 95% of pilots zero P&L impact (unverified). Vendor case studies are anecdotes without
  controls (unverified): Bedrock +143% on a function-calling prompt; Anthropic's prompt improver +30%
  on classification; none quantified by Vertex, OpenAI, Opik or Arize. Lead with a controlled lift
  with its spend on one ledger, paired with the cheap-serving-model story (Databricks' 90×,
  PromptWizard's pitch).

## TOOL and interop — pick one, never invent a third

- **MCP 2026-07-28** ([spec](https://modelcontextprotocol.io/specification/2026-07-28)): a long-running
  call returns a **task handle** via the Tasks extension (`tasks/get`, `tasks/update`,
  `tasks/cancel`); auth is OAuth / OIDC with dynamic client registration. A campaign over MCP uses
  that shape, no polling or webhook scheme of ours; it also meets the inbound-credential gap the
  roadmap holds MCP on.
- **Tracing — choose one primary.** **OpenTelemetry GenAI** (all `gen_ai.*` still *Development*,
  v1.42.0, 2026-06-12, own repo — pin a version) vs **OpenInference** (Arize;
  `openinference.span.kind` on every span). Langfuse and MLflow sit downstream of both.
- **Prompt registries.** MLflow: `{{variable}}` placeholders, immutable integer versions, aliases for
  promotion; Langfuse: labels as the deployment pointer. Write-back maps onto those and invents no
  branch model. `.prompty` and dotprompt have not converged; all three (and SKILL.md) share
  frontmatter plus body.
- **Not binding, checked:** A2A v1.0 (signed Agent Card, only if called agent-to-agent); Inspect AI
  `.eval` logs (cross-tool logs only); AGENTS.md / llms.txt (not standards); EU AI Act GPAI duties (we
  modify no weights).
