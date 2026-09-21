# Related work — the landscape, and where the bench sits

The field is three layers that get collapsed into one, the line behind PromptPotter's own
algorithm, and a capability comparison against the two closest published peers. The **method**
questions this touches have their own owners and are not restated here: why PoBB and not another
bandit family, and how the lineage search compares to MCTS, are both
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md). What we measure on and
what a number is allowed to claim is [`benchmarks.md`](benchmarks.md).

## Three layers, routinely mistaken for one

| Layer | What it ships | Where it stops |
|---|---|---|
| **Telemetry** — [Langfuse](https://langfuse.com), and the tracing half of most LLMOps stacks | Spans, token and latency accounting, prompt storage and versioning, LLM-as-judge scoring over captured traces | Prompt changes are written by a human. Nothing searches. |
| **Eval + an optimizer menu** — [Opik Agent Optimizer](https://www.comet.com/docs/opik/agent_optimization/overview), [agent-opt](https://github.com/future-agi/agent-opt), Promptolution | Several search algorithms behind one interface. Opik ships six — evolutionary, few-shot Bayesian, GEPA, a hierarchical-reflective optimizer, meta-prompt, and a `ParameterOptimizer` over temperature and `top_p` — all through one `optimize_prompt()`, with one optimizer's result usable as another's input. agent-opt ships six of its own: Bayesian search, meta-prompt, ProTeGi, GEPA, random search, PromptWizard | Each algorithm runs *however it wants*. The menu makes them reachable; it does not make their numbers comparable. |
| **A single algorithm, in a paper** — APE, OPRO, MIPROv2, PromptWizard, CAPO, GEPA on the prompt side; SkillOpt, WikiSkill, DarwinX, AutoDesign on the harness side; AlphaEvolve, OpenEvolve, AlgoTuner on the code side | One search method, usually one benchmark suite, usually one budget convention | No interface, and no obligation to be runnable beside anything else. |

**The gap is the third column, not the first two.** Nobody in this table stands on a **stopping
rule** or a **comparability guard** — when to stop buying evidence, and what has to be held equal
before two methods' numbers may sit in the same table. That is where PromptPotter is pointed, and
it is why the direction of travel is a **bench**: a place a third party's search method plugs in as
the round's candidate source and is measured under one model, one budget meter, one grader, one
split and one archive. Status and design of that plug point are
[`../specs/roadmap.md`](../specs/roadmap.md) § The optimizer plug point — **not built yet**, and
this page will not claim it before a peer actually runs inside it.

## The family PromptPotter's own algorithm belongs to

PromptPotter belongs to the **LLM-driven evolution** family: an LLM proposes variants, a scorer
ranks them, and the winners breed. On the prompt side that is **GEPA**, MIPROv2 and PromptWizard;
on the code side AlphaEvolve, OpenEvolve, AlgoTuner and AutoResearch. The code systems mutate source
and every prompt system above mutates one artifact, the prompt. **PromptPotter evolves the prompt
and the pipeline parameters around it, jointly.**

What separates it from the prompt systems mechanically — it searches a tree rather than a single
line, so a spent branch rewinds instead of stalling — is
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) § Comparison to MCTS,
which owns the whole four-phase mapping.

The line behind all of it: [APE](https://arxiv.org/abs/2211.01910) and OPRO (2022–23) established
that a language model can author its own instruction; [DSPy](https://github.com/stanfordnlp/dspy)
(2023–) made the prompt a *compiled artifact*, with [MIPROv2](https://arxiv.org/abs/2406.11695)
searching its instructions and demonstrations; [PromptWizard](https://arxiv.org/abs/2405.18369)
(2024) added critique-guided refinement; [CAPO](https://arxiv.org/abs/2504.16005) (2025) added
racing and an explicit token budget. [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 Oral) is the
one most people now compare against — though CAPO outscored it on both tasks of the promptolution
paper's own head-to-head, so the reference point is not automatically the best result.

## A menu is not a bench — the honest version

**"We can wrap their method" is not on its own a differentiator, and this page will not pretend it
is.** Opik and agent-opt already expose several algorithms behind one API. What they do not serve
is the property that makes two of those algorithms' numbers mean the same thing:

- **One grader.** Both sides of a comparison score through the same compiled formula, rather than
  each method carrying its own metric function that drifts. Our own published head-to-head has
  already been bitten by this — `matchers.py::_exact_match` strips to the last bold span while the
  peer notebooks inline a whole-string compare ([`bbeh-comparison/`](bbeh-comparison/README.md)).
- **One budget meter.** A method that spends internally — GEPA's reflective rollouts, MIPROv2's
  minibatch trials — bills onto the same ledger as everything else, so cost-per-fitness is a
  quantity rather than a claim.
- **One exam, chosen before anybody proposes.** The round's sample subset is selected before
  generation, so no method picks the questions it is graded on.
- **Ability, not accuracy.** Scores are subset-invariant (Rasch θ), so a method that drifted onto
  easier samples cannot out-rank an honest one — [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md).
- **A stopping rule.** Sequential elimination with a posterior (PoBB) rather than a fixed trial
  count — [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

**The underrated one is the archive.** Measurements are content-addressed by configuration and
sample, so two methods that converge on overlapping candidates pay for those cells once. That is
what makes an N-way comparison affordable at all; a stack that re-runs every method end-to-end
prices itself out of the experiment it exists to enable.

## Recursive self-improvement — the adjacent axis

An optimizer pointed at its own optimizer prompts is the same mechanism one level up, and that is a
live research axis rather than a curiosity: Sakana's Darwin Gödel Machine rewrites a coding agent's
own source under evolutionary search, Sakana has since stood up a dedicated [RSI
Lab](https://sakana.ai/rsi-lab/), and the harness-engineering literature now has [its own reading
list](https://github.com/leezythu/Awesome-Harness-Self-Improvement). PromptPotter already recurses —
an outer cycle whose backend is an inner cycle
([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)) — which means the comparability problem
arrives there too, one level harder: in the recursion the optimizer *is* the instrument, so the
thing being varied is also the thing doing the measuring. A bench that holds a harness constant is
the only place that question is answerable.

## Test-time compute, moved out of the request

The popular way to spend compute at inference is to search inside the request — sample many
answers, verify, pick ([Snell et al.](https://arxiv.org/abs/2408.03314) ·
[survey](https://arxiv.org/abs/2406.16838)). PromptPotter runs that same search as **tuning
infrastructure**: it finds a configuration once, and from then on that configuration is simply used
— an ordinary call, no search in the path, nothing learning at request time. The test-time budget is
itself tunable (`reasoning_effort`, `max_tokens`, the model), so the inference bill is something it
optimizes rather than something it grows. If what you want instead is live model selection per
question, that is a gateway ([OpenRouter](https://openrouter.ai),
[OmniRoute](https://github.com/diegosouzapw/OmniRoute)) — PromptPotter tunes the thing a gateway
ends up calling, so the two compose.

## Against GEPA and AlphaEvolve

**GEPA** ships inside DSPy as `dspy.GEPA` and is the reference point for prompt optimization today;
**AlphaEvolve** is the closest published peer on the code side. Same family, same reflective core.
GEPA evolves the instruction text of a DSPy program, AlphaEvolve evolves source code, PromptPotter
evolves prompt and pipeline jointly — that is scope, a different target rather than a missing
feature. The table is capability, not a benchmark result.

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |
| **Tunes the whole pipeline** — a chain of calls, not a single one: model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — the budget goes to the questions that separate candidates ([PoBB](../methods/candidate-elimination.md)); scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded ([internals](../developer/self-healing-internals.md)) | 🟡 | 🟡 | 🟢 |
| **Carries knowledge *across* runs** — parameter impact, query difficulty and failure patterns survive the campaign that found them, plus a library of proven blocks to recombine | 🟡 | 🟢 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger, so a replay reports what the work really cost | 🔴 | — | 🟢 |
| **Open & inspectable** — the code is on GitHub, a browser control plane shows the run live, and the campaign is an object you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |

🟡 is a partial version of the same capability; AlphaEvolve's are that capability at the code level;
`—` is a closed service whose behaviour is not documented. GEPA keeps a candidate pool *within* a
run — the 🟡 above is about what survives it.

**One documented exception to the pricing row:** a DSPy call replayed from DSPy's own cache is not
counted, because DSPy skips recording on a cache hit —
[`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md).

**The row this table does not have yet is the one the bench adds** — *runs somebody else's
optimizer under the same guard*. It is deliberately absent rather than marked 🟡: the seam is
designed and unbuilt, and a capability table that advertises intent is the failure mode this page
exists to argue against. It lands when a peer has actually run.

## Where each is ahead of us

- **GEPA — reach.** It sits inside DSPy, with that ecosystem's adapters, tracing and audience,
  which is why PromptPotter also installs as a DSPy optimizer rather than asking anyone to leave it:
  a distribution choice, and what it costs a DSPy user is owned by
  [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md) § What you trade away.
- **AlphaEvolve — code optimization**, its search reaching into source, which is not what
  PromptPotter is pointed at.
- **Langfuse — production telemetry.** It is built to swallow high-volume live traffic and answer
  questions about system health. Our observability exists to make a *campaign* readable, not a
  production fleet, and the two are not substitutes: the natural pairing is their trace stream as a
  dataset source and our winner written back to their prompt store.
- **Opik — breadth today, and one claim of ours it already reaches.** Six optimization algorithms
  are callable there now, behind an observability stack further along than ours, and its
  `ParameterOptimizer` tunes temperature and `top_p` — so "we also move parameters, not just words"
  is **not** the line between us. Ours is that a whole declared *pipeline* — every node's model,
  thresholds and config — moves jointly with the prompt, and that nothing is held constant across
  their six, which is the argument above. "More algorithms, available sooner" stays a real
  advantage until the plug point ships.
