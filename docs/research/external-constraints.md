# External constraints — decisions the outside world has already made

**What this page is.** It lists external standards, published findings and review norms that
take a design decision away from us. If we design our own shape first, conforming later means
unravelling it. Each entry states:
- the **decision it removes**;
- the **milestone it binds**: M13 (the plug point), M14 (the preprint), SKILL (the
  skilled-agent target), L4 (self-optimization) or TOOL (callable as a tool / MCP);
- its **source**;
- whether that source was **read** or only seen through search summaries.

An unverified entry must be read at its source before anything is built or cited on it.

**What it is not.** Tools and competitors go in [`landscape.md`](landscape.md), and the argument
against the peers that matter goes in [`related-work.md`](related-work.md). This page records only
what binds us, whoever it comes from.

**Checked** 2026-09-24. Re-check
an entry before quoting it; standards on this page are still moving.

## Ranked — the five that cost most to get wrong

1. **The scorer sits outside anything the loop can see or edit** (L4). Sakana's DGM, when it could
   see its own hallucination checker, rewrote the checker's logging to fake a pass. It cheated more
   often when the check was visible. An audit of five self-improving systems found harness
   tampering persisting in the lineage of the *best-scoring* agent.
   - Our scoring is already out of the target's reach. For L4 this has to be a **stated
     invariant**, not a property of today's code: the inner cycle's scorer, formula and held-out
     cells can never be a `pipeline_param`.
   - Sources: [DGM](https://arxiv.org/abs/2505.22954) (read);
     [Auditing Harness Tampering](https://arxiv.org/abs/2609.00069) (read). Its tampering rates were
     ADAS 84.6%, DGM 63.1%, HyperAgents 73.6%, ScientistOne 29.6% and AFlow 18.3%. Its safeguards
     are tamper-proof evaluation channels, provenance-aware state tracking and continuous
     auditing.
2. **Selection must be able to see lineage, not only the round's best** (L4, M13). The
   [Huxley-Gödel Machine](https://arxiv.org/abs/2510.21614) (ICLR 2026 oral) found a
   *metaproductivity–performance mismatch*: the top scorer of a round often has unproductive
   descendants, and a lower scorer seeds the better lineage. HGM fixes this by scoring clades.
   - Our lineage graph and content-addressed archive already hold what a clade score needs.
   - The decision this removes: **never discard a non-winner's lineage on the grounds that it lost
     one round**. Keep the archive whole, which it is today.
3. **Early stopping must be defensible under optional stopping** (M14). Stopping on the data seen
   so far is PoBB's whole point. Repeating a fixed-α test under optional stopping inflates the
   false-positive rate well above α; that is a textbook sequential-testing result, with no single
   paper behind a number. The answer the 2025–26 literature gives is confidence sequences built
   from test supermartingales, which stay valid at any stopping time
   ([Hsu & Shekhar, *Efficient Sequential Evaluation of LLMs*](https://arxiv.org/abs/2607.17409),
   read).
   - Before the preprint, either state PoBB's guarantee in those terms, or disclose that it is
     not anytime-valid and measure the inflation on our own runs.
4. **Budget-matched comparison is what reviewers will ask for first** (M13, M14). Optimizer gains
   shrink or reverse once rollout budgets are matched.
   [LEVI](https://arxiv.org/abs/2605.09764) "matches or exceeds GEPA at less than half of its
   rollout budget on four different benchmarks" (abstract read; read the body's table before
   quoting numbers).
   - A head-to-head must hold the **number of calls** equal and report it next to dollars and wall
     clock.
   - [HAL](https://arxiv.org/abs/2510.11977) (ICLR 2026) and
     [AI Agents That Matter](https://arxiv.org/abs/2407.01502) (TMLR 2025) set the template: a
     cost–accuracy Pareto frontier, dollars and tokens as separate axes, and a holdout never used
     for tuning.
   - Status: HAL read.
5. **A skill candidate must conform to the Agent Skills specification** (SKILL). The
   [spec](https://agentskills.io/specification) (read) is implemented by 16+ runtimes, including
   Claude Code, Codex CLI, Gemini CLI, Copilot and Cursor.
   - **Frontmatter is a closed set:**
     - `name` is required: at most 64 chars, lowercase letters, digits and hyphens, and it must
       equal the parent directory name;
     - `description` is required: at most 1024 chars;
     - `license`, `compatibility` (at most 500 chars), `metadata` (a string→string map) and the
       experimental `allowed-tools` are optional;
     - any other top-level key is non-conformant.
   - **Size:** the body should stay under about 5000 tokens, and the file under 500 lines.
   - **Layout:** `skill-name/{SKILL.md, scripts/, references/, assets/}`.
   - The tier-1 check in the L2/L3 prompts ([`../specs/roadmap.md`](../specs/roadmap.md) § Evolving
     agent harnesses) means *this* spec. A candidate that outgrows its limits is invalid before
     it runs.

## SKILL — the skilled-agent target

- **Library size has a measured failure point.** In
  [More Skills, Worse Agents?](https://arxiv.org/abs/2605.24050) (read), performance degrades by
  up to 21% at 202 skills.
  - The cause is *wrong-skill selection* ("skill shadowing"). The context overhead measured as
    indistinguishable from zero.
  - The expanded libraries tested were 52, 102 and 202 skills, and the degradation grew with
    size. The paper gives no safe threshold.
  - This decides two things: **deduplication (NVIDIA's tier 2) is enforced from the first skill,
    not added later**, and the skilled agent needs a routing design before its library reaches
    the few-dozen range the paper starts at.
- **Selection sees only the description.** Under the spec's progressive disclosure, a runtime
  loads `name` + `description` (about 100 tokens) at startup and the body only on activation. So
  **the `description` is the routing surface**, and optimizing a skill's body without its
  description optimizes the half that selection never reads.
  - Today the harbor connector writes the frontmatter itself and holds `description` fixed,
    deliberately: with one skill there is nothing to choose between
    (`connectors/harbor.py::_write_skill`).
  - That makes the candidate the body alone, so the spec's frontmatter rules cannot be broken by a
    mutation. The body's size limit still applies to it.
  - **The skilled agent reverses this.** With many skills, `description` has to become a search
    axis, and the frontmatter rules then bind every candidate.
- **A skill a model writes for itself, unscored, helps nobody.**
  [SkillsBench](https://arxiv.org/abs/2602.12670) (87 tasks, deterministic verifiers) compared
  three conditions:
  - curated skills: +16.2 pp on average, but 16 of 84 tasks got worse;
  - self-generated skills: **zero** average benefit;
  - focused skills of 2–3 modules beat comprehensive documents.

  This is the strongest argument *for* our loop and the sharpest warning against shortcutting it:
  - A skill is admitted only by its measured lift on verifier-graded cells, never by an LLM's
    rewrite alone.
  - SkillsBench is the external harness to report skill results on.
  - Trajectory-grounded writers are the proposer pattern to prefer over a free rewrite:
    [Trace2Skill](https://arxiv.org/abs/2603.25158) patches from success and failure traces and
    merges them into one conflict-free skill.
- **A security floor that no candidate may cross.** Scanners have converged:
  [SkillSpector](https://github.com/nvidia/skillspector) (NVIDIA; 71 patterns, a risk score above
  50 means do not install) and [Cisco skill-scanner](https://github.com/cisco-ai-defense/skill-scanner).
  Audits of public skills found 26–37% flawed and dozens confirmed malicious. The attacks seen
  include hidden instructions, invisible Unicode Tag characters, and agent-settings injection
  (CVE-2025-59536). A mutated skill body must never:
  - carry Unicode Tag characters;
  - instruct reading secrets or environment variables;
  - instruct outbound calls or exec/eval.

  Tier 1 is where that is checked. Running a SkillSpector-class scan before a candidate is
  measured is the long-run form.
- **Descriptions must distinguish, not only describe.** Overlapping descriptions are what seeds
  skill shadowing. For the skilled agent, *mutual distinguishability* against sibling skills is a
  measurable objective. [SkillRouter](https://arxiv.org/abs/2603.22455) shows retrieval at library
  scale is embedding-based, and it also reads skill bodies.
- **The format is cross-runtime now.** Microsoft Agent Framework (GA, July 2026), Google ADK and
  Codex skills all load SKILL.md with progressive disclosure, so an optimized skill should assume
  it will run on more than one runtime.
- **Executable skills transfer; prose skills are unproven.** In
  [SkillWeaver](https://arxiv.org/abs/2504.07079), skills written as callable APIs transferred from
  a strong agent to a weak one (+54% on WebArena). Our skill target is prose today. That is
  acceptable, but it is the open question, not a settled one.
- **Evaluation half:** NVIDIA SkillEvaluator's three tiers (validation, deduplication, lift against
  the skill's absence), [`landscape.md`](landscape.md) § NVIDIA SkillEvaluator.

## L4 — self-optimization

Beyond ranked items 1 and 2:
- **An overseer that watches the run, not only a score at the end.**
  [SICA](https://arxiv.org/abs/2504.15228) (read; 17% → 53% on SWE-bench Verified) runs an
  asynchronous LLM overseer, about every 30 s. It can steer a running agent or cancel it when it
  drifts or stalls.
  - It does **not** gate a self-edit before it lands. It is a runtime monitor.
  - Our heartbeat and the pause contract are the same shape. A pre-landing review gate would be
    our own addition, not SICA's.
- **Hardening the environment is cheap and works.**
  [Reward Hacking Benchmark](https://arxiv.org/abs/2605.02964) (read) measured 13 frontier models
  and found exploit rates of 0–13.9%. Simple environment hardening cut them by 87.7% relative,
  without hurting task success.
  - Our own rule follows from item 1: in L4, the cells that choose a winner are not the cells
    that certify it.
- **Separate the search algorithm from the workflow shape.**
  [EvoAgentX](https://arxiv.org/abs/2507.03616) plugs AFlow, TextGrad and MIPRO in as swappable
  evolution algorithms, apart from the workflow topology.
  - This is M13's proposer seam, seen from the other side.
  - The decision it removes: the plug point must not assume the L1 → L2 → L3 shape is the only
    one a proposer runs in.
- **"More agentic" is not better by default.** Two findings pull against each other:
  - [AFlow](https://arxiv.org/abs/2410.10762) (ICLR 2025 oral) searches workflows as code graphs
    with MCTS, and beat hand-designed ones by 5.7%.
  - [AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2), the more agentic version, did
    not reliably beat the templated v1 where a strong template existed.

  The fixed L1–L3 loop against an optimizer built as an agent with skills
  ([`../specs/roadmap.md`](../specs/roadmap.md)) is therefore an **empirical question M13 can
  answer**, not a design to adopt on argument.
- **Sample efficiency is available from selection, not only from proposers.**
  [ShinkaEvolve](https://arxiv.org/abs/2509.19349) (Sakana, ICLR 2026) reports a state-of-the-art
  circle-packing result with only 150 samples (read). It combines three mechanisms:
  - parent sampling that balances exploration and exploitation;
  - novelty rejection-sampling of code;
  - a bandit over an ensemble of LLM mutators.

  Read its sampling policy before designing L4 candidate selection.
- **Precedent to cite:**
  - [STOP](https://arxiv.org/abs/2310.02304) is the closest prior art: an improver improving its
    own code with weights frozen.
  - [Gödel Agent](https://arxiv.org/abs/2410.04444) (ACL 2025) and
    [ADAS](https://arxiv.org/abs/2408.08435) both keep an **archive of past designs**, not only
    the current best.

## M13 — the plug point

- **Opik's optimizer interface is fixed and read**
  ([extending optimizers](https://www.comet.com/docs/opik/agent_optimization/)).
  - Subclass `BaseOptimizer` and define `DEFAULT_PROMPTS`.
  - `__init__(self, model: str, **kwargs)`.
  - `optimize_prompt(prompt: ChatPrompt, dataset, metric, agent=None, experiment_config=None,
    n_samples=None, project_name=None, validation_dataset=None, max_trials=None) ->
    OptimizationResult`.
  - Reuse the inherited `evaluate_prompt()` / `call_model()`.

  This decides the shape of "PromptPotter reachable through Opik": a `BaseOptimizer` subclass
  living in the `promptpotteropt` repo, never here (ADR-0006).
- **DSPy's optimizer interface** was read in source (`dspy/teleprompt/teleprompt.py`,
  `gepa/gepa.py`):
  - The base class is `Teleprompter`, with `compile(self, student: Module, *, trainset,
    teacher=None, valset=None, **kwargs) -> Module`.
  - `dspy.GEPA` takes three budget knobs: `auto` (light / medium / heavy), `max_full_evals` and
    `max_metric_calls`.
  - `max_metric_calls` is the knob a budget-matched head-to-head sets.

## M14 — the preprint

Beyond ranked items 3 and 4:
- **Clustered and paired standard errors, not binomial ones.**
  [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640) (Anthropic, 2024) shows
  clustering can inflate SEs up to 3×. A head-to-head differences the two arms per cell.
- **Small samples need exact or bootstrap intervals.**
  ["Don't use the CLT…"](https://arxiv.org/abs/2503.01747) (2025) applies below a few hundred
  datapoints, which covers most of our panels.
- **Report signal-to-noise per benchmark.** [Signal and Noise](https://arxiv.org/abs/2508.13144)
  (2025, 900K runs) filters or down-weights low-SNR subtasks before a delta is trusted.
- **IRT: the field default is 2PL, and we use Rasch.**
  - tinyBenchmarks, metabench (ICLR 2025) and
    [Fluid Benchmarking](https://arxiv.org/abs/2509.11106) (COLM 2025) all validate IRT ability
    as the comparability tool, which supports θ.
  - They fit discrimination as well as difficulty. The preprint either justifies Rasch's stronger
    invariance or upgrades. The roadmap already queues feeding a discrimination term into
    subset selection (§ Fitness comparability).
  - Fluid Benchmarking's four metrics (validity, variance, saturation, efficiency) are the
    comparison table reviewers will know.
- **The [Agentic Benchmark Checklist](https://arxiv.org/abs/2507.02825)** found 10 of 10 popular
  agent benchmarks failing its reporting section. Fill it in for our bench and publish it as an
  appendix.
- **Holm or Benjamini–Hochberg correction** on any pairwise matrix of more than two proposers
  (common practice; no single canonical source verified).
- **Venue and arXiv requirements.**
  - The NeurIPS Datasets & Benchmarks track requires Croissant metadata and non-gated public
    hosting.
  - arXiv's moderation policy says each author takes "full responsibility for all its contents,
    irrespective of how the contents were generated"
    ([info.arxiv.org/help/moderation](https://info.arxiv.org/help/moderation)).
  - Since May 2026, "incontrovertible evidence" of unchecked AI output earns a one-year ban.
    Examples are hallucinated references, leftover chatbot text and unedited placeholders.
    This rests on consistent secondary reporting.
  - So every reference and number in the preprint is checked by hand, and AI use is disclosed.

## Cost reporting — binds every run from now, not only the preprint

- **Record raw token counts and the price table's date with every run**, so dollars can be
  recomputed if prices move before publication.
  - HAL and Terminal-Bench price tokens at the provider's public rate *at run time*.
  - Terminal-Bench also reports wall clock per step and in total.
  - Tokens are already on every row. The **price date** is the piece to check.
- **Amortized lifetime cost is the buyer's number.**
  [Databricks](https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization)
  (2025-09-24, read) counts optimization cost plus serving cost over 100k requests.
  - On that basis, a GEPA-optimized gpt-oss-120b beats un-optimized Claude Opus 4.1 by 2.2 points
    at about 90× less serving cost.
  - GEPA on GPT-4.1 gained +2.1 against SFT's +1.9, at about 20% lower serving cost. SFT and GEPA
    together gained +4.8.
  - **Where this points:** a small open model, optimized, against a frontier model un-optimized.
    Prompt optimization is **complementary to fine-tuning, not a rival to it**. Our "harness
    before weights" pitch should say *before*, never *instead of*.
  - **Where not to go:** a headline that omits the optimization spend. Their accounting includes
    it, and ours already meters it.

## Direction — where optimization pays, and where it does not

- **An optimized prompt is bound to its model.**
  [Why Prompt Optimization Works, and Why It Sometimes Doesn't](https://arxiv.org/abs/2605.26655)
  (2026) tested DSPy, TextGrad and GEPA across four model families. Edits that help one benchmark
  often fail on another, and the effect depends on edit type × task type.
  [Prompting Inversion](https://arxiv.org/abs/2510.22251) (2025) reports a scaffold that helps
  GPT-4o and *hurts* GPT-5; its numeric table is unverified. Two consequences:
  - A **model swap is a new optimization run**, never a copy of the winner, so an export has to
    name the model it was won on.
  - Pitch mid-tier and open-weight serving models first. Gains shrink or invert on the strongest
    reasoning models.
- **The harness can beat the wording.** LangChain's deepagents went from 52.8% to 66.5% on
  Terminal-Bench 2.0 with the *same* model, through prompt, tool and middleware changes
  ([harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)).
  This supports moving pipeline parameters, skills and harnesses jointly with the prompt. Wording
  alone is the smaller lever on agentic tasks.
- **Against weight updates, reflective search wins on rollout economics.**
  [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 oral) beats GRPO by 6% on average, using up
  to 35× fewer rollouts. Combined with Databricks' result that SFT and GEPA compound, the pitch is
  that the harness comes *before* weights, and the two work together.
- **Buyers lack evidence and audit trails, not optimizer horsepower.** MIT's *GenAI Divide*
  (2025, 300 deployments) found 95% of pilots show zero P&L impact. Vendor case studies are mostly
  anecdotes without controls:
  - Bedrock's +143% on a function-calling prompt;
  - Anthropic's prompt improver at +30% on a classification task;
  - no quantified results published by Vertex, OpenAI, Opik or Arize.

  A controlled lift with its spend on one ledger is the thing that market lacks. Lead with it, and
  pair it with the cheap-serving-model story, which is what Databricks' 90× headline and
  PromptWizard's pitch share.

## TOOL and interop — pick one, never invent a third

- **MCP 2026-07-28**
  ([spec](https://modelcontextprotocol.io/specification/2026-07-28)).
  - A long-running tool call returns a **task handle** through the Tasks extension (`tasks/get`,
    `tasks/update`, `tasks/cancel`).
  - Auth is OAuth / OIDC with dynamic client registration.
  - A campaign exposed over MCP uses that shape, not a polling or webhook scheme of our own. It
    also meets the inbound-credential gap the roadmap already holds MCP on.
- **Tracing conventions.** Two conventions compete, and one must be chosen as primary.
  - **OpenTelemetry GenAI** (all `gen_ai.*` attributes are still *Development*, v1.42.0,
    2026-06-12, now in its own repo): pin a version rather than tracking head.
  - **OpenInference** (Arize, active; `openinference.span.kind` required on every span).
  - Langfuse and MLflow sit downstream of both.
- **Prompt registries.**
  - **MLflow:** `{{variable}}` placeholders, immutable integer versions, and aliases for
    promotion.
  - **Langfuse:** labels as the deployment pointer.
  - Write-back maps onto those semantics and invents no branch model.
  - `.prompty` and dotprompt have not converged. None binds, but all three formats (and
    SKILL.md) share frontmatter plus body.
- **Not binding, checked:**
  - **A2A v1.0:** a signed Agent Card, only if we are ever called agent-to-agent.
  - **Inspect AI `.eval` logs:** only for cross-tool log compatibility.
  - **AGENTS.md / llms.txt:** not standards.
  - **EU AI Act GPAI duties:** we modify no model weights.
