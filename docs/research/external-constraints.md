# External constraints — decisions the outside world has already made

Standards, published findings and review norms that take a design decision away from us. Each entry
names the decision it removes and its source; an entry not marked *read* is unverified and is read at
its source before anything is built on it. A source that came back unreadable (a PDF, a binary) is
fetched again as the text its publisher also serves (arXiv `/html/<id>`, PMC, a raw file) — never
recalled. What the code already enforces is named by its symbol only.

## Ranked — the four that cost most to get wrong

1. **The scorer sits outside anything the loop can see or edit** (L4): the inner cycle's scorer,
   formula and held-out cells are never a `pipeline_param`
   ([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md) owns the invariant). Source:
   [Auditing Harness Tampering](https://arxiv.org/abs/2609.00069) (read) — tampering persisting in
   the *best-scoring* lineage, 18.3% to 84.6% across audited systems.
2. **Early stopping must be defensible under optional stopping** (the preprint). A fixed-α test repeated under
   optional stopping inflates false positives; the field's answer is a confidence sequence from test
   supermartingales, valid at any stopping time ([Hsu & Shekhar](https://arxiv.org/abs/2607.17409),
   read). PoBB's ε is a bar on a posterior reading, not an anytime-valid error rate; the preprint
   reports how the two differ on the same rounds.
3. **Budget-matched comparison is what reviewers ask for first** (the bench, the preprint). Gains shrink or reverse
   at matched rollout budgets ([LEVI](https://arxiv.org/abs/2605.09764), abstract read).
   [HAL](https://arxiv.org/abs/2510.11977) (read) and [AI Agents That Matter](https://arxiv.org/abs/2407.01502)
   set the template: a cost–accuracy Pareto frontier, dollars and tokens as separate axes, a holdout
   never used for tuning. A head-to-head holds the number of calls (rollouts) equal and reports tokens,
   dollars and wall clock beside it.
4. **A skill candidate must conform to the Agent Skills specification** (SKILL;
   [spec](https://agentskills.io/specification), read): frontmatter is a closed set — `name` (≤64
   chars, lowercase, digits, hyphens, equal to the directory), `description` (≤1024), optional
   `license`, `compatibility` (≤500), `metadata`, `allowed-tools`; body under ~5000 tokens, file
   under 500 lines. `connectors/harbor.py::_write_skill` writes the frontmatter with a fixed
   `description`; the body limit and security floor are `layer_state.py::_SKILL_TIERS_TEXT`, a prompt
   directive, not a validator.

## SKILL — the skilled-agent target

- **Library size has a measured failure point**: up to −21% at 202 skills, from wrong-skill selection
  ("skill shadowing"), no safe threshold ([More Skills, Worse Agents?](https://arxiv.org/abs/2605.24050),
  read). Deduplication (SkillEvaluator's tier 2, [`landscape.md`](landscape.md)) is enforced from the
  first skill; a library needs a routing design before a few dozen.
- **Selection sees only the description** (~100 tokens at startup, the body on activation), so
  `description` is the routing surface. With one skill there is nothing to route, so the candidate is
  the body alone (`harbor.py::_write_skill`); the skilled agent makes `description` a search axis and
  binds the frontmatter rules to every candidate. Mutual distinguishability against siblings is a
  measurable objective; retrieval at scale reads bodies too ([SkillRouter](https://arxiv.org/abs/2603.22455)).
- **A skill a model writes for itself, unscored, helps nobody**: curated skills +16.2 pp on average
  but 16 of 84 tasks worse, self-generated zero ([SkillsBench](https://arxiv.org/abs/2602.12670)). A
  skill is admitted only by measured lift on verifier-graded cells; prefer trajectory-grounded writers
  ([Trace2Skill](https://arxiv.org/abs/2603.25158)) over a free rewrite.
- **A security floor**: [SkillSpector](https://github.com/nvidia/skillspector) (71 patterns, risk > 50
  = do not install), [Cisco skill-scanner](https://github.com/cisco-ai-defense/skill-scanner); attacks
  include hidden instructions, invisible Unicode Tag characters and agent-settings injection
  (CVE-2025-59536). Unicode Tag characters are not screened today.
- **Executable skills transfer; prose skills are unproven** ([SkillWeaver](https://arxiv.org/abs/2504.07079):
  +54% WebArena strong → weak as callable APIs). Our target is prose — the open question.

## L4 — self-optimization

- **An overseer that watches the run** ([SICA](https://arxiv.org/abs/2504.15228), read): an async LLM
  overseer steers or cancels a drifting agent — a runtime monitor (our heartbeat and pause), not a
  pre-landing gate, which would be ours.
- **Hardening the environment is cheap and works** ([Reward Hacking Benchmark](https://arxiv.org/abs/2605.02964),
  read): exploit rates 0–13.9%, cut 87.7% relative. Ours: the cells that choose a winner are not the
  cells that certify it.
- **The plug point must not assume L1 → L2 → L3 is the only shape** ([EvoAgentX](https://arxiv.org/abs/2507.03616)
  plugs AFlow, TextGrad and MIPRO in apart from topology).
- **"More agentic" is not better by default**: [AFlow](https://arxiv.org/abs/2410.10762) +5.7% over
  hand-designed workflows; [AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2) did not
  reliably beat templated v1. Fixed L1–L3 vs an optimizer-as-agent is an empirical question.
- **Sample efficiency from selection, not only proposers** ([ShinkaEvolve](https://arxiv.org/abs/2509.19349),
  read): SOTA circle packing in 150 samples via exploration-balanced parent sampling, novelty
  rejection-sampling, a bandit over an LLM-mutator ensemble. Read its sampling policy before designing
  L4 candidate selection.
- **Algorithm configuration already settled how to race configurations** (unverified): one pass per
  cell, repeats only for the noise floor (Birattari; COCO); block on instance, pair in one order with
  one seed, correct only the published verdict (ParamILS, irace, SMAC); retire saturated and flat
  environments, prefer high SNR (metabench); headroom does not predict lift (ρ = 0.047 over 19.8k
  prompt pairs, [2605.26655](https://arxiv.org/abs/2605.26655)); only RRSI gates on a noise floor
  from re-running the unchanged harness (δ 0.004–0.020).
- **Proposer mechanisms with their own ablation row** (unverified): failures alone beat a natural mix,
  53.9 vs 49.6 (2609.32452); a generalization-gate block, 51.6 → 58.4 held-out (2608.01918);
  per-operator win/loss history, 65.88 vs 34.12 (2609.32886); raw traces of earlier candidates, a
  summary no better than scores (2603.28052); a delta applied by deterministic merge, 56.9 → 70.3
  (ACE, 2510.04618); GRACE's paraphrase ablation moved ~0.2 points.
- **Precedent**: [STOP](https://arxiv.org/abs/2310.02304) (an improver improving its own code);
  [ADAS](https://arxiv.org/abs/2408.08435) (an archive of past designs).

## Survey round — many cheap starts, the band near p ≈ 0.5

For [`../specs/survey-round.md`](../specs/survey-round.md): starts are cheap and many, judged small,
then raced (MIPROv2 [2406.11695](https://arxiv.org/html/2406.11695) §3.1, APE
[2211.01910](https://arxiv.org/html/2211.01910) §3.2, CAPO [2504.16005](https://arxiv.org/html/2504.16005),
BOHB [1807.01774](https://arxiv.org/html/1807.01774v1)); whether the start matters depends on the
model (OPRO [2309.03409](https://arxiv.org/html/2309.03409) §5.3, EvoPrompt
[2309.08532](https://arxiv.org/html/2309.08532) Table 6), so the survey stays ablatable; signal lives
near p ≈ 0.5 (SFL [2408.15099](https://arxiv.org/html/2408.15099v3) §4.1, DAPO
[2503.14476](https://arxiv.org/html/2503.14476)). No source measures a mid-accuracy band for prompt
optimization.

## The preprint

- **Clustered and paired SEs, not binomial** ([Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640):
  clustering inflates SEs up to 3×) — `scoring/paired.py`, `shared/statistics.py::holm_adjusted`,
  `ArmContrast.q_value` (BH), `evidence/comparison.py::snr`.
- **Small samples need exact or bootstrap intervals** (["Don't use the CLT…"](https://arxiv.org/abs/2503.01747))
  — below a few hundred datapoints, most of our panels; not served today.
- **Report SNR per benchmark** ([Signal and Noise](https://arxiv.org/abs/2508.13144), 900K runs):
  filter or down-weight low-SNR subtasks before trusting a delta.
- **IRT: the field default is 2PL** (tinyBenchmarks, metabench,
  [Fluid Benchmarking](https://arxiv.org/abs/2509.11106) — validity, variance, saturation, efficiency
  is the table reviewers know). We start at 1PL and graduate per dataset
  ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § The model).
- **The [Agentic Benchmark Checklist](https://arxiv.org/abs/2507.02825)** failed 10 of 10 popular
  agent benchmarks on reporting — fill it, publish as an appendix.
- **Venue and arXiv.** NeurIPS D&B requires Croissant metadata and non-gated hosting (unverified).
  arXiv holds each author responsible for all contents ([moderation](https://info.arxiv.org/help/moderation));
  unchecked AI output earns a one-year ban. Every reference and number is hand-checked, AI use disclosed.

## M15 — embedding

Checked 2026-10-31 through a summarizing fetch; re-read a source before quoting it.

- **The service proposes; the program evaluates, pulling its work.** AlphaEvolve's
  [service](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/reference-guide/api-reference)
  hands a program out under a `lockToken` and takes its evaluation back; Vizier, W&B Sweeps and Agent
  Lightning v0.x pull too ([`landscape.md`](landscape.md)). A lease appears only where a crash must be
  told from a slow answer. **Removes:** whether the hosted box calls a tenant's program — never; the
  program pulls its cells under a lease ([`../specs/roadmap.md`](../specs/roadmap.md) § Embedding
  (M15), R2).
- **A machine client is an OAuth client presenting a JWT.** MCP 2026-07-28 makes the server an OAuth
  2.1 resource server (RFC 9728, PKCE, RFC 8707 `resource`, an audience check); machine clients take
  the [`oauth-client-credentials`](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)
  extension with a JWT-bearer assertion (RFC 7523), which a CI job's OIDC token is. **Removes:** the
  shape of R1's workload mint — one client-credentials grant serves CI and MCP.
- **NVIDIA's free NIM access is for research, not serving** ([terms](https://docs.api.nvidia.com/nim/docs/product):
  "prototyping, research, development and testing purposes only"; production takes an AI Enterprise
  licence). **Removes:** the hosted tier's open-weight host is vLLM or SGLang; NIM serves the
  operator's own experiments.
- **Most of a reported non-text lift is selection**: [OPT2I](https://arxiv.org/abs/2403.17804)'s
  equal-budget random-paraphrase arm and [RAISE](https://arxiv.org/abs/2603.00483)'s best-of-32
  noise sampling each recover most of the gain. **Removes:** a non-text campaign is measured against
  an equal-budget resample arm, read on the mean, never the best found.
- **The judge that selects must not certify, and one axis must not regress**: a verifier and judge
  of one family inflate ([NPC](https://arxiv.org/abs/2512.07702)); a reward model optimized against
  is hacked ([LeSAMP](https://arxiv.org/abs/2607.23488)); [Prompt-A-Video](https://arxiv.org/abs/2412.15156)'s
  aesthetic gain cost motion; [POET](https://arxiv.org/abs/2510.12041)'s rewriter regressed another
  generator. **Removes:** the bench set's scorer is of another family than the in-loop judge, and a
  non-text dataset declares the axis that must not regress.
- **A generator's seed spread is its noise floor** ([CosmosAlign](https://arxiv.org/abs/2608.07693)).
  **Removes:** the seed is a declared identity input or a replicate axis; the floor is read before a
  campaign is funded.
- **Order by evidence and cell price.** Strongest at the cheapest cell: RAG parameters
  ([Agentic AutoRAG](https://arxiv.org/abs/2610.08452), [AutoRAG-HP](https://arxiv.org/abs/2406.19251)),
  VLM prompts ([MPO](https://arxiv.org/abs/2510.09201)), ASR context prompts on deterministic WER
  ([2602.18966](https://arxiv.org/abs/2602.18966)); then text-to-image loops; physics-aware video last
  (minutes per video on an H100). Weakest: TTS style prompts ([2505.17093](https://arxiv.org/abs/2505.17093)),
  embedding instruction prefixes ([2605.22544](https://arxiv.org/abs/2605.22544)), raw CFG and step
  tuning, VLA paraphrases ([LIBERO-PRO](https://arxiv.org/abs/2510.03827)). **Removes:** which
  non-text target comes first.
- **A world model's first target is its upsampler, and its critic is an unscreened judge.**
  [Cosmos 3](https://huggingface.co/nvidia/Cosmos3-Super) wants long structured captions written by
  an upsampler that calls any OpenAI-compatible LLM; [Reason1's video critic](https://docs.nvidia.com/cosmos/2.0.0/reason1/video_critic.html)
  keeps the best of N verdicts with no published agreement with people. **Removes:** the first video
  target is the upsampler's instructions, and Cosmos Reason judges only after the screen every
  invented rubric takes ([`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md)).

## Cost reporting

- **Raw token counts and the price table's date ride every run**, so dollars recompute if prices
  move (HAL and Terminal-Bench price at the provider's public rate at run time). Tokens are on every
  ledger row; the price date is not recorded yet.
- **Amortized lifetime cost is the buyer's number** ([Databricks](https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization),
  read): GEPA-optimized gpt-oss-120b beats un-optimized Claude Opus 4.1 by 2.2 points at ~90× less
  serving cost; GEPA on GPT-4.1 +2.1 vs SFT's +1.9, both together +4.8. Prompt optimization is
  complementary to fine-tuning — "harness before weights" says *before*, never *instead of*. Never a
  headline omitting the optimization spend.

## Direction — where optimization pays, and where it does not

- **An optimized prompt is bound to its model.** Edits that help one benchmark often fail on another
  ([2605.26655](https://arxiv.org/abs/2605.26655)); a scaffold helping GPT-4o hurts GPT-5
  ([Prompting Inversion](https://arxiv.org/abs/2510.22251)); of 49 cross-model prompt transfers on
  chess puzzles 31 improved, 16 worsened ([2610.00416](https://arxiv.org/abs/2610.00416)). A **model
  swap is a new optimization run** (an export names its model); pitch mid-tier and open-weight
  models first.
- **The harness can beat the wording**: deepagents 52.8% → 66.5% on Terminal-Bench 2.0, same model
  ([harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering))
  — move pipeline params, skills and harnesses jointly with the prompt.
- **Against weight updates, reflective search wins on rollout economics**: [GEPA](https://arxiv.org/abs/2507.19457)
  beats GRPO by 6% on average with up to 35× fewer rollouts.
- **Buyers lack evidence and audit trails, not horsepower** (MIT's *GenAI Divide*: 95% of pilots zero
  P&L impact, unverified; vendor case studies are uncontrolled). Lead with a controlled lift with its
  spend on one ledger.

## TOOL and interop — pick one, never invent a third

- **MCP 2026-07-28** ([spec](https://modelcontextprotocol.io/specification/2026-07-28)): a
  long-running call returns a task handle via the Tasks extension (`tasks/get`, `tasks/update`,
  `tasks/cancel`); auth is OAuth 2.1 (§ M15); stateless, no session, a dropped stream loses the call
  in flight. A campaign over MCP uses that shape, no polling or webhook scheme of ours.
- **Environments — Harbor's task directory is the shared format** ([verifiers](https://github.com/PrimeIntellect-ai/verifiers)
  reads Harbor tasksets, [NeMo Gym](https://github.com/NVIDIA-NeMo/Gym) integrates it, the
  [Hugging Face Hub](https://huggingface.co/blog/rl-environments) has no registry; OpenEnv is still
  changing). Environments enter through `connectors/harbor.py`, no seam beside it.
- **Tracing — choose one primary**: OpenTelemetry GenAI (`gen_ai.*` still *Development*; pin a commit)
  vs OpenInference (`openinference.span.kind` on every span). Langfuse and MLflow sit downstream of both.
- **Prompt registries**: MLflow `{{variable}}` placeholders, immutable integer versions, aliases;
  Langfuse labels as the deployment pointer. Write-back maps onto those and invents no branch model
  ([`../specs/mlflow-interop.md`](../specs/mlflow-interop.md)).
- **Not binding, checked**: A2A v1.0; Inspect AI `.eval` logs; AGENTS.md / llms.txt; EU AI Act GPAI
  duties (we modify no weights).
