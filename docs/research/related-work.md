# Related work — the landscape, and where the bench sits

Three layers that get collapsed into one, the line behind PromptPotter's own algorithm, and a
capability comparison against the two closest published peers. The stop rule itself is
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)'s; what a number may claim
is [`benchmarks.md`](benchmarks.md)'s; tools set aside are [`landscape.md`](landscape.md)'s.

## Three layers, routinely mistaken for one

| Layer | What it ships | Where it stops |
|---|---|---|
| **Telemetry** — [Langfuse](https://langfuse.com), and the tracing half of most LLMOps stacks | Spans, token and latency accounting, prompt storage and versioning, LLM-as-judge scoring over captured traces | Prompt changes are written by a human. Nothing searches. |
| **Eval + an optimizer menu** — [Opik Agent Optimizer](https://www.comet.com/docs/opik/agent_optimization/overview), [agent-opt](https://github.com/future-agi/agent-opt), Promptolution | Several search algorithms behind one interface (Opik's six, incl. a `ParameterOptimizer` over temperature and `top_p`, through one `optimize_prompt()`; rosters in [`landscape.md`](landscape.md)) | Each algorithm runs *however it wants*. The menu makes them reachable, not comparable. |
| **A single algorithm, in a paper** — APE, OPRO, MIPROv2, PromptWizard, CAPO, GEPA on the prompt side, plus [Promptim](landscape.md#promptim-langchain) as the LangSmith-bound CLI form; SkillOpt, WikiSkill, DarwinX, AutoDesign on the harness side; AlphaEvolve, OpenEvolve, AlgoTuner on the code side | One search method, usually one benchmark suite and budget convention | No interface, no obligation to run beside anything else. |

**The gap is the third column.** Some rows race inside their own algorithm (CAPO), but no one serves
a **stopping rule** across methods — and none, ours included, is anytime-valid under optional stopping
([`external-constraints.md`](external-constraints.md) § Ranked, item 3, names the form that would be).
Nobody serves a **comparability guard** — what must be held equal before two methods' numbers share a
table. Hence a **bench**: a third party's search method plugs in as an optimizer manifest and is
measured under one model, budget meter, grader, split and archive
([`../specs/roadmap.md`](../specs/roadmap.md) § The bench (M13)). This page claims no head-to-head
before [`benchmarks.md`](benchmarks.md) publishes one.

## The family PromptPotter's own algorithm belongs to

**LLM-driven evolution**: an LLM proposes variants, a scorer ranks them, winners breed. Prompt side
**GEPA**, MIPROv2, PromptWizard; code side AlphaEvolve, OpenEvolve, AlgoTuner, AutoResearch. Code
systems mutate source, most prompt systems the prompt alone (Opik also sampling parameters and tool
schemas). **PromptPotter moves a whole declared pipeline jointly with the prompt**, and searches a tree
rather than a line (§ Comparison to MCTS).

The line: [APE](https://arxiv.org/abs/2211.01910) and OPRO (2022–23) showed an LLM can author its own
instruction; [DSPy](https://github.com/stanfordnlp/dspy) (2023–) made the prompt a *compiled artifact*,
[MIPROv2](https://arxiv.org/abs/2406.11695) searching instructions and demos;
[PromptWizard](https://arxiv.org/abs/2405.18369) (2024) added critique-guided refinement;
[CAPO](https://arxiv.org/abs/2504.16005) (2025) racing and an explicit token budget.
[GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 Oral) is the common reference point — though CAPO
outscored it on both tasks of the promptolution paper's head-to-head. Its gains shrink at matched
rollout budgets ([LEVI](https://arxiv.org/abs/2605.09764);
[`external-constraints.md`](external-constraints.md) § Ranked, item 4), while against weight updates
the economics run its way and SFT and GEPA compound — prompt search comes *before* fine-tuning, not
instead of it (that page's § Direction and § Cost reporting carry the numbers).

## Why PoBB's family and not another

Mid-round abortion is **best-arm identification**; three choices place PoBB:

- **Every prior, not only the leader** — LUCB (Kalyanakrishnan 2012), Bayes-UCB (Kaufmann 2012) and Hoeffding Races (Maron & Moore 1993) are pairwise too but test against the leader alone, with frequentist bounds.
- **Bayesian over frequentist** — `P(c is best)` is one operator-readable number.
- **Fixed-confidence (ε) over fixed-budget** — broken candidates stop at the evidence floor; Successive Rejects (Audibert 2010) and Sequential Halving (Karnin 2013) run them to the phase boundary. "Fixed-confidence" is loose: ε is a bar on a posterior reading, not an anytime-valid error rate, so under optional stopping it carries no frequentist guarantee ([`external-constraints.md`](external-constraints.md) § Ranked, item 3). The anytime-valid form — a confidence sequence from test supermartingales ([Hsu & Shekhar, *Efficient Sequential Evaluation of LLMs*](https://arxiv.org/abs/2607.17409)) — is what the guarantee must take before it is claimed.

**Wilcoxon signed-rank + Holm-Bonferroni is not the stop rule** (pairwise, variance-agnostic); Holm is a *reporting* correction only (`shared/statistics.py::holm_adjusted`). **OCBA** (Chen 2000) is the closest classical relative but allocates where this stops; PoBB keeps Top-Two Thompson Sampling's stop rule and drops its allocation half.

**Prior art — Stockfish's fishtest** runs this loop (patch vs master, SPRT). Two correspondences to know before claiming novelty: their unbalanced opening book is our seed-misses-first order, and Elo is Bradley–Terry, Rasch's logistic family. Not theirs: an automated proposer over structure and text, N-arm best-arm identification, and measurement reuse across a changed instrument.

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; set aside for testing against the leader alone.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; set aside for not adapting within-round.
- **Chen, C.-H. (2000).** *Optimal Computing Budget Allocation.* Operations Research. — allocation rather than a stop rule.

## Comparison to MCTS

PromptPotter **is** AlphaZero-shaped MCTS over the lineage tree:

- **Selection.** L2/L3 emits `fork_proposal: {reason}` — *whether* to rewind; *where* is UCB1 over the backpropagated tree (`application/mask/backprop.py::select_rewind_round`). The layer does not name the round — it never sees the ancestors' fitness.
- **Expansion.** A round: L1 proposes a population from the selected node.
- **Simulation.** A deterministic forward pass, not a random rollout (AlphaZero's precedent); within a round PoBB prunes siblings on shared samples.
- **Backpropagation.** Each round's θ rolls up to every ancestor as visit count + value (`accumulate_node_stats`), every cycle read on ONE fit (`DifficultyView.level_series`); a caveated round is a visit with no value, and a rewind finding no valued ancestor is dropped as a `layer_rebase_dropped` warning.

**Value is θ, never accuracy, and a fork's inherited prefix is not a fresh visit** (`application/mask/backprop.py`) — both naive forms fail silently. A rollout is a full round of LLM calls, so exploration is closer to PUCT than vanilla UCT; it buys **recovery from dead-end branches**.

## A menu is not a bench — the honest version

**"We can wrap their method" is not on its own a differentiator.** Opik and agent-opt already expose
several algorithms behind one API. What they do not serve is what makes two algorithms' numbers mean
the same thing:

- **One grader** — one compiled formula for both sides. Our own head-to-head was bitten:
  `matchers.py::_label_match` strips to the last bold span while the peer notebooks compare whole
  strings ([`bbeh-comparison/`](../../paper/bbeh-comparison/README.md)).
- **One budget meter** — GEPA's reflective rollouts and MIPROv2's minibatch trials bill onto one ledger,
  so cost-per-fitness is a quantity, not a claim.
- **One exam, chosen before anybody proposes** — no method picks the questions it is graded on.
- **Ability, not accuracy** — subset-invariant Rasch θ
  ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md); the IRT sources are
  [`external-constraints.md`](external-constraints.md) § M14), so drifting onto easier samples cannot
  out-rank an honest method.
- **A stopping rule** — PoBB rather than a fixed trial count
  ([`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-dark.png" alt="The compare view's verdict: what was held equal across the compared campaigns, and what stays confounded" width="100%">
  </picture>
</p>

Campaigns side by side serve that list first — what was held equal, what stays confounded — then the
numbers.

**The underrated one is the archive.** Measurements are content-addressed by configuration and sample,
so methods converging on overlapping candidates pay for those cells once — what makes an N-way
comparison affordable at all.

## Recursive self-improvement — the adjacent axis

An optimizer pointed at its own optimizer prompts is the same mechanism one level up, and a live
research axis: Sakana's [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) rewrites a coding
agent's source under evolutionary search; NVIDIA's [SoL-Pi](https://nvlabs.github.io/SoL-Pi/) a
coding-agent harness under a held-out gate ([`landscape.md`](landscape.md) § SoL-Pi); Sakana's
[RSI Lab](https://sakana.ai/rsi-lab/); a [reading list](https://github.com/leezythu/Awesome-Harness-Self-Improvement).
PromptPotter already recurses — an outer cycle whose backend is an inner cycle
([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)) — where the optimizer *is* the instrument,
so the thing varied also does the measuring. Only a bench that holds a harness constant answers that.

### Agent-harness evolution

Evolving a harness around a frozen model is occupied: SkillOpt, DarwinX and AutoDesign do it, and
DarwinX's abstract makes the "single-lineage search is path-dependent" critique. All score on agent
and tool environments (ALFWorld, SpreadsheetBench, Terminal-Bench, WebArena) the `harbor` connector
measures too, so a head-to-head is wiring plus a per-cell spend ceiling. None runs sequential
elimination, a cost-per-fitness model or a subset-invariant ability scale — that, with joint prompt
and pipeline-parameter search, is the distinguishing claim.

**Precedent and measured danger** are [`external-constraints.md`](external-constraints.md) § Ranked,
items 1–2, and § L4's; cite STOP, Gödel Agent, ADAS, the Huxley-Gödel Machine, ShinkaEvolve, SICA and
the tampering audits from there.

## Test-time compute, moved out of the request

The popular way to spend inference compute is to search inside the request — sample, verify, pick
([Snell et al.](https://arxiv.org/abs/2408.03314) · [survey](https://arxiv.org/abs/2406.16838)).
PromptPotter runs that search as **tuning infrastructure**: it finds a configuration once, and from
then on it is an ordinary call with no search in the path. The test-time budget is itself tunable
(`reasoning_effort`, `max_tokens`, the model), so the inference bill is optimized rather than grown.
Live per-question model selection is a gateway's job ([OpenRouter](https://openrouter.ai),
[OmniRoute](https://github.com/diegosouzapw/OmniRoute)); PromptPotter tunes what the gateway calls, so
the two compose.

## Against GEPA and AlphaEvolve

**GEPA** (`dspy.GEPA`) is the prompt-optimization reference point; **AlphaEvolve** the closest code-side
peer. Same family, same reflective core: GEPA evolves a DSPy program's instruction text, AlphaEvolve
source code, PromptPotter prompt and pipeline jointly — a different target, not a missing feature. The
table is capability, not a benchmark result.

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |
| **Tunes the whole pipeline** — a chain of calls, not a single one: model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — the budget goes to the questions that separate candidates ([PoBB](../methods/candidate-elimination.md)); scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded ([internals](../developer/self-healing-internals.md)) | 🟡 | 🟡 | 🟢 |
| **Carries knowledge *across* runs** — parameter impact, query difficulty and failure patterns survive the campaign that found them, plus a library of proven blocks to recombine | 🟡 | 🟢 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger, so a replay reports what the work really cost | 🔴 | — | 🟢 |
| **Open & inspectable** — the code is on GitHub, a browser control plane shows the run live, and the campaign is an object you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |

🟡 = a partial version (AlphaEvolve's at the code level); `—` = a closed service, undocumented. GEPA
keeps a candidate pool *within* a run — the 🟡 is about what survives it. Opik is a menu, not an
algorithm: § A menu is not a bench and § Where each is ahead of us. **One exception to the pricing
row:** a call replayed from DSPy's own cache is not counted, since DSPy skips recording on a hit
([`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md)).

**The row the bench adds — *runs somebody else's optimizer under the same guard* — is deliberately
absent**, not 🟡: a capability table that advertises intent is the failure this page argues against.
It lands with the first published head-to-head.

## Where each is ahead of us

- **GEPA — reach.** It sits inside DSPy's ecosystem, which is why PromptPotter also installs as a DSPy
  optimizer; the cost is [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md) § What you
  trade away.
- **AlphaEvolve — code optimization**, search reaching into source, which PromptPotter is not pointed at.
- **Langfuse — production telemetry** for high-volume live traffic and system health. Our observability
  makes a *campaign* readable; the pairing is their trace stream as a dataset source and our winner
  written back to their prompt store.
- **Opik — breadth, and one claim of ours it already reaches.** Six algorithms callable now, a further-
  along observability stack, `ParameterOptimizer` over temperature and `top_p`, and tool and MCP schema
  rewriting — so "we also move parameters" is **not** the line. Ours: a whole declared *pipeline* (every
  node's model, thresholds, config) moves with the prompt, and nothing is held constant across their
  six. "More algorithms, sooner" stays their advantage until their methods run inside the plug point.
