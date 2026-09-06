# Related work — where PromptPotter sits

The field, the line behind it, and a capability comparison against the two closest published peers.
The **method** questions this touches have their own owners and are not restated here: why PoBB and
not another bandit family, and how the lineage search compares to MCTS, are both
[`../methods/candidate-elimination.md`](../methods/candidate-elimination.md). What we measure on and
what a number is allowed to claim is [`benchmarks.md`](benchmarks.md).

## The family

PromptPotter belongs to the **LLM-driven evolution** family: an LLM proposes variants, a scorer ranks them, and the winners breed. On the prompt side that is **GEPA**, MIPROv2 and PromptWizard; on the code side AlphaEvolve, OpenEvolve, AlgoTuner and AutoResearch. The code systems mutate source and every prompt system above mutates one artifact, the prompt. **PromptPotter evolves the prompt and the pipeline parameters around it, jointly.**

What separates it from the prompt systems mechanically — it searches a tree rather than a single line, so a spent branch rewinds instead of stalling — is [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) § Comparison to MCTS, which owns the whole four-phase mapping.

The line behind all of it: [APE](https://arxiv.org/abs/2211.01910) and OPRO (2022–23) established that a language model can author its own instruction; [DSPy](https://github.com/stanfordnlp/dspy) (2023–) made the prompt a *compiled artifact*, with [MIPROv2](https://arxiv.org/abs/2406.11695) searching its instructions and demonstrations; [PromptWizard](https://arxiv.org/abs/2405.18369) (2024) added critique-guided refinement; [CAPO](https://arxiv.org/abs/2504.16005) (2025) added racing and an explicit token budget. [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 Oral) is the one most people now compare against — though CAPO outscored it on both tasks of the promptolution paper's own head-to-head, so the reference point is not automatically the best result.

## Test-time compute, moved out of the request

The popular way to spend compute at inference is to search inside the request — sample many answers, verify, pick ([Snell et al.](https://arxiv.org/abs/2408.03314) · [survey](https://arxiv.org/abs/2406.16838)). PromptPotter runs that same search as **tuning infrastructure**: it finds a configuration once, and from then on that configuration is simply used — an ordinary call, no search in the path, nothing learning at request time. The test-time budget is itself tunable (`reasoning_effort`, `max_tokens`, the model), so the inference bill is something it optimizes rather than something it grows. If what you want instead is live model selection per question, that is a gateway ([OpenRouter](https://openrouter.ai), [OmniRoute](https://github.com/diegosouzapw/OmniRoute)) — PromptPotter tunes the thing a gateway ends up calling, so the two compose.

## Against GEPA and AlphaEvolve

**GEPA** ships inside DSPy as `dspy.GEPA` and is the reference point for prompt optimization today; **AlphaEvolve** is the closest published peer on the code side. Same family, same reflective core. GEPA evolves the instruction text of a DSPy program, AlphaEvolve evolves source code, PromptPotter evolves prompt and pipeline jointly — that is scope, a different target rather than a missing feature. The table is capability, not a benchmark result.

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |
| **Tunes the whole pipeline** — a chain of calls, not a single one: model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — the budget goes to the questions that separate candidates ([PoBB](../methods/candidate-elimination.md)); scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded ([internals](../developer/self-healing-internals.md)) | 🟡 | 🟡 | 🟢 |
| **Carries knowledge *across* runs** — parameter impact, query difficulty and failure patterns survive the campaign that found them, plus a library of proven blocks to recombine | 🟡 | 🟢 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger, so a replay reports what the work really cost | 🔴 | — | 🟢 |
| **Open & inspectable** — the code is on GitHub, a browser control plane shows the run live, and the campaign is an object you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |

🟡 is a partial version of the same capability; AlphaEvolve's are that capability at the code level; `—` is a closed service whose behaviour is not documented. GEPA keeps a candidate pool *within* a run — the 🟡 above is about what survives it.

**One documented exception to the pricing row:** a DSPy call replayed from DSPy's own cache is not counted, because DSPy skips recording on a cache hit — [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md).

**Where each is ahead of us:** GEPA on **reach** — it sits inside DSPy, with that ecosystem's adapters, tracing and audience, which is why PromptPotter also installs as a DSPy optimizer rather than asking anyone to leave it: a distribution choice, and what it costs a DSPy user is owned by [`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md) § What you trade away. AlphaEvolve is ahead on **code optimization**, its search reaching into source, which is not what PromptPotter is pointed at.
