# External constraints — what the outside world fixed, and what our setting forces

Two kinds of entry, and no third:

- **An outside fact we cannot derive**: a standard, a spec, a licence, a vendor's API, a venue's rule.
  Each names its source.
- **A rule our own setting forces**: each carries its derivation in a line.

A paper is never the reason for a rule here. A specialized result is either derivable from the few
principles the rule names, or the model does not need it. A paper is cited only where it defines what
we reproduce (an optimizer manifest, a dataset, a rubric), beside that reproduction. What the code
already enforces is named by its symbol only.

## Ranked — the four that cost most to get wrong

1. **The scorer sits outside anything the loop can see or edit** (L4): the inner cycle's scorer,
   formula and held-out cells are never a `pipeline_param`
   ([`../concepts/l4.md`](../concepts/l4.md) owns the invariant). Why: a loop rewarded by a score it
   can reach finds editing the score the cheapest lift, and selection keeps the lineage that did.
2. **Early stopping must be defensible under optional stopping** (the preprint). A fixed-α test read
   after every round, stopping at the first pass, rejects with probability above α: each look is
   another chance to cross. A reading valid at any stopping time is a confidence sequence. PoBB's ε is
   a bar on a posterior reading, not that; the preprint reports how the two differ on the same rounds.
3. **A comparison holds spend equal** (the bench, the preprint). An optimizer given more calls finds
   more by search alone, so a comparison at unequal spend measures spend. Tokens, dollars and wall
   clock are reported beside the matched axis, on a holdout never used for tuning: the set that chose
   a winner overstates it.
4. **A skill candidate must conform to the Agent Skills specification** (SKILL;
   [spec](https://agentskills.io/specification), read): frontmatter is a closed set — `name` (≤64
   chars, lowercase, digits, hyphens, equal to the directory), `description` (≤1024), optional
   `license`, `compatibility` (≤500), `metadata`, `allowed-tools`; body under ~5000 tokens, file
   under 500 lines. The frontmatter is written with a fixed `description`; the body limit and
   security floor are a prompt directive, not a validator.

## SKILL — the skilled-agent target

- **Selection sees only the description** (the spec loads it at startup, the body on activation), so
  `description` is the routing surface. With one skill there is nothing to route, so the candidate is
  the body alone; the skilled agent makes `description` a search axis and binds the frontmatter rules
  to every candidate.
- **A library degrades as it grows**: each added skill is one more wrong pick for the router.
  Deduplication is enforced from the first skill, mutual distinguishability is a measurable
  objective, and a library needs a routing design before it is large.
- **A skill is admitted only by measured lift** on verifier-graded cells, as every candidate is; an
  unscored skill a model writes for itself has shown nothing.
- **A security floor**: [SkillSpector](https://github.com/nvidia/skillspector) (71 patterns, risk > 50
  = do not install), [Cisco skill-scanner](https://github.com/cisco-ai-defense/skill-scanner); attacks
  include hidden instructions, invisible Unicode Tag characters and agent-settings injection
  (CVE-2025-59536). Unicode Tag characters are not screened today.

## L4 — self-optimization

- **The cells that choose a winner are not the cells that certify it**: the best of several noisy
  readings overstates its own level, so certification reads cells the choice never saw.
- **Arms are compared paired**: on the same samples, in one order, under one seed, so each sample's
  difficulty cancels in the difference. A repeat of an unchanged arm measures the instrument's noise,
  never the arm, so repeats serve only the noise floor.
- **Headroom admits a dataset, never ranks it**: distance to the ceiling bounds the lift possible,
  not whether a proposer can find it. A saturated or floor-pinned dataset leaves no discordant cells,
  so it separates nothing and is retired.

## Survey round — many cheap starts, the band near p ≈ 0.5

For [`../specs/roadmap.md`](../specs/roadmap.md) § Survey round. A paired comparison learns only from
discordant cells, which are likeliest where the arms' success sits near 0.5 (a Bernoulli's variance
p(1 − p) peaks there); near 0 or 1 most cells are concordant and separate nothing. So starts are cheap
and many, judged small, then raced where they separate. Whether the start matters is measured per
model, so the survey stays an ablatable round kind.

## The preprint

- **Paired and clustered SEs, never binomial**: cells sharing a sample or a cluster are correlated,
  so a binomial SE that treats them as independent is too narrow — paired readings, Holm across
  pairs, BH `q_value`, SNR.
- **Small samples take exact or bootstrap intervals**: below a few hundred cells, and near 0 or 1, a
  normal interval leaves [0, 1] and misses its coverage. Most of our panels are that small; not
  served today.
- **A delta is read against its dataset's noise**: on a dataset whose run-to-run spread rivals the
  delta, the delta says nothing.
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
- **A best-of-N rises with N on noise alone**, so a non-text campaign is measured against an
  equal-budget resample arm, read on the mean, never the best found.
- **The judge that selects must not certify, and one axis must not regress**: a judge optimized
  against becomes the target, and a judge of the selector's family shares its blind spots; a search
  on one axis trades the others freely. So the bench set's scorer is of another family than the
  in-loop judge, and a non-text dataset declares the axis that must not regress.
- **A generator's seed spread is its noise floor**: the seed is a declared identity input or a
  replicate axis, and the floor is read before a campaign is funded.
- **The cheapest deterministic cell comes first**: a cell that is cheap and graded without a judge
  buys the most decisions per dollar, so a non-text target with a deterministic metric precedes one
  needing a judge or minutes per output.
- **A world model's first target is its upsampler, and its critic is an unscreened judge.**
  [Cosmos 3](https://huggingface.co/nvidia/Cosmos3-Super) wants long structured captions written by
  an upsampler that calls any OpenAI-compatible LLM; [Reason1's video critic](https://docs.nvidia.com/cosmos/2.0.0/reason1/video_critic.html)
  keeps the best of N verdicts with no published agreement with people. **Removes:** the first video
  target is the upsampler's instructions, and Cosmos Reason judges only after the screen every
  invented rubric takes ([`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md)).

## Cost reporting

- **Raw token counts and the price table's date ride every run**: prices move, so dollars recompute
  only from tokens and a dated table. Tokens are on every ledger row; the price date is not recorded
  yet.
- **A headline never omits the optimization spend**: a lift is worth what it saves over the calls it
  serves, less what finding it cost, so the buyer's number is the amortized lifetime cost.

## Direction — where optimization pays

- **An optimized prompt is bound to its model**: a lift is measured on one model, and another model
  is an unmeasured condition. A model swap is a new optimization run, and an export names its model.

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
  ([`../specs/roadmap.md`](../specs/roadmap.md) § MLflow interop).
- **Not binding, checked**: A2A v1.0; Inspect AI `.eval` logs; AGENTS.md / llms.txt; EU AI Act GPAI
  duties (we modify no weights).
