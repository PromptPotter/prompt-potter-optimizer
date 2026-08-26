<p align="center">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

<p align="center">
  <a href="https://promptpotter.com"><b>promptpotter.com</b></a> — <b>10 free optimization runs</b> on your own data, up to 10 rounds each, in the browser and on my key. Offer stands until <b>15 Sept 2026</b>; bring-your-own-key is in the works.
</p>

# PromptPotter: LLM-Driven Evolution of Prompts and Pipelines

**PromptPotter evolves better prompts.** Most prompt engineering is manual. PromptPotter automates the generate → score → critique cycle. It tries multiple prompt and pipeline variations together, keeps memory across runs, and recovers automatically when a generated prompt produces broken output. Weak candidates get eliminated early on statistical confidence (*Posterior-of-Being-Best — [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)*) so you don't burn LLM budget on losers. Built for RAG pipelines, LLM agents, and multi-step LLM workflows — drop in via CLI, Python SDK, the `/potter-run` Claude Code skill, or as a [**DSPy optimizer**](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) where you would reach for GEPA.

## How to Optimize LLM Prompts in 3 Steps

Describe your 1️⃣ **task**, drop in a labeled 2️⃣ **dataset**, and 3️⃣ **run the loop**. The task is the goal you want the AI to hit; the dataset is examples of hitting it. Each round, PromptPotter generates variations 🧪, scores them ⚖️, and keeps the winners 🏆. It stops when results plateau. ✨ **Prompt optimized.**

> [!IMPORTANT]
> **New here?** Start with [`docs/manual/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md) — six chapters covering install → first run → reading output → troubleshooting.

## ⭐ Features

Every measurement costs money, so the whole design is **most fitness per dollar**. What that buys in practice: a real run on a Swiss invoice account-coding set moved exact-match accuracy from **5% to 55%** — and stopped because it hit its own spend cap, not because it ran out of ideas. (5 rounds, n=20; a measured lift, not a significance claim.)

The capabilities PromptPotter shares with the rest of the field are in the [comparison table](#scientific-framing) below; these are the ones it doesn't:

- **💬 Chat-first** — talk to the Potter and watch it work inline, Perplexity-style: the searches, the tool calls, each round as it lands, and a button whenever a decision is yours. Ships as a reusable **chat-app template** — keep the chat core, delete the optimizer panes. [spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/chat-foundation.md)
- **Searches a tree, not a trail** — every round's result flows back to each ancestor it descends from, so a spent branch rewinds to whichever ancestor the evidence favours and ***climbs a different hill*** instead of stalling. Branches are kept and compared rather than discarded, and an ancestor's score reflects what re-expanding from it actually yielded — including in branches it never ran itself.
- **Hard-sample leaderboard** — score preferentially on the samples that actually separate variants; the ones everyone aces or fails are noise.
- **🛡️ Guards against self-validation** — an optimizer that picks its own winner is the easiest thing in this field to fake, and the loop is built so it can't grade itself into a false win. Three independent guards: scores are **subset-invariant ability** (Rasch θ), so a candidate that drifted onto easier samples cannot out-rank an honest one; **degenerate candidates** — a constant answer, a shape that games the scorer — are caught before they count; and the layer that **validates** a fix is never the layer that proposed it. What a winner's number is allowed to claim: [`verdict-resolution.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md).
- **Optimizes itself** — point the optimizer at its own optimizer prompts. [L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)
- **Pick your block library mode** — proven personas, thinking styles and answer formats (from PromptWizard and the *Self-Discover* modules it draws on, plus what our own runs turned up). Let the optimizer suggest from the library, restrict it to the library, or switch it off.

## Six ways to run it

1. **WebApp** — Full UI setup
2. **`/potter-run` Claude Code skill** — drive a full campaign from your editor.
3. **CLI** — `python -m promptpotter new <name>` / `resume`.
4. **REST API**
5. **Python / Jupyter notebook** — Promptpotter is written in Python. The *DSPy setting* is only recommended for simple cases, due to DSPy not permitting the mechanics of promptpotter, i.e. ["", ""] . 

**Direction — the sixth way: a tool another agent calls.** Parity as a first-class **agent-callable tool** (MCP), so an *operating agent* — yours, or an ML-research agent like NVIDIA's AutoResearch — reaches for PromptPotter as its *try-harness-first* move before spending on fine-tuning. [Why, plus a same-dataset head-to-head](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md) · [roadmap § Agent-tool parity](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md).

## Common questions

- **What does L1 actually mutate?** The prompt template's fields (persona, task instruction, …) plus whatever your `pipeline.yaml` declares as tunable. See [`the-loop.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward).
- **Where do I get a starting prompt?** Bring one with your dataset (`datasets/{name}/prompts/{node}.yaml`). Walkthrough: [manual ch. 03](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/03-first-campaign.md).
- **How do I watch a run?** Open `dashboard.json` in an auto-reload editor + watch the CLI terminal. Full guide: [Watching a run](#watching-a-run).
- **My scoring formula was wrong — did I lose results?** No. Traces are facts; scores are policy. The optimizer rescores on load and replays decisions; on divergence, fork. See [`scoring-and-memory.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md).
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)); stalls escalate L1 → L2 → L3 ([the-loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/05-troubleshooting.md).

## Limitations

- **Parameter-based optimization only.** PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset.** Input/output pairs are mandatory.

## Roadmap

Status and the full forward plan live in one place: [`docs/specs/roadmap.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md). Documentation index: [`docs/README.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md).

## Peer systems

PromptPotter belongs to the **LLM-driven evolution** family: an LLM proposes variants, an evaluator scores them, and the winners breed. The peer systems — full comparison + benchmark notes in [`docs/research/related-work.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella):

- **Prompt evolution** — [**GEPA**](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [MIPROv2](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [PromptWizard](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · **PromptPotter**
- **Code evolution** — [AlphaEvolve](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [OpenEvolve](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [AlgoTuner](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [AutoResearch](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella)

The Python line behind them: [APE](https://arxiv.org/abs/2211.01910) and OPRO (2022–23) established that a language model can author its own instruction; [DSPy](https://github.com/stanfordnlp/dspy) (2023–) made the prompt a *compiled artifact*, [MIPROv2](https://arxiv.org/abs/2406.11695) searching its instructions and demonstrations; [PromptWizard](https://arxiv.org/abs/2405.18369) (2024) added critique-guided refinement; [CAPO](https://arxiv.org/abs/2504.16005) (2025) added racing and an explicit token budget. [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 Oral) is the one most people now compare against — though CAPO outscored it on both tasks of the promptolution paper's own head-to-head, so the reference point is not automatically the best result.

The code-evolution systems mutate source; every prompt-evolution system above mutates one artifact, the prompt. PromptPotter evolves **both the prompt and the pipeline parameters around it**, jointly.

*Background: Asankhaya Sharma on [OpenEvolve](https://www.youtube.com/watch?v=mWBT-szUutI) (talk).*

## Scientific framing

PromptPotter is a **tree search over prompt programs** — precisely, **AlphaZero-shaped MCTS over the lineage**: [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#best-arm-identification--sequential-testing) prunes losers *within* a round, each round's ability backpropagates to its ancestors, and a UCB rule picks the ancestor to re-expand once a branch is spent. Rollouts give way to deterministic evaluation on your dataset, as in AlphaZero. [Comparison](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#comparison-to-mcts).

**Test-time compute, moved out of the request.** The popular way to spend compute at inference is to search inside the request — sample many answers, verify, pick ([Snell et al.](https://arxiv.org/abs/2408.03314) · [survey](https://arxiv.org/abs/2406.16838)). PromptPotter is that same search run as **tuning infrastructure instead**: you run it, it finds a configuration, and from then on that configuration is simply *used on demand* — an ordinary call, with no search in the path and nothing learning at request time. The test-time budget is itself one of the things it can tune (`reasoning_effort`, `max_tokens`, the model), so the inference bill is something it optimizes rather than something it grows.

If what you actually want is "pick the best *currently available* model for whatever I ask next" — one user, an arbitrary next question, live selection across whatever keys/subscriptions/local models you have — that's a gateway, not PromptPotter: [OpenRouter](https://openrouter.ai) (hosted) or [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (self-hosted, stitches your own providers into one local endpoint) are built for exactly that. PromptPotter runs the opposite axis — one fixed task, hit repeatedly, tuned once *offline* against your real eval data until a prompt+model+params combo wins, then deployed and reused — so it's what tunes the thing a gateway ends up calling, not a replacement for the gateway itself. The two compose rather than compete.

### The loop

Each round is **generate → score → critique**: L1 proposes candidates, they're scored against your dataset, and the critique steers the next round. When the inner layer stalls, an outer layer (L2, then L3) redirects it; when the branch itself is spent, the search rewinds. Full mechanics: [the-loop.md](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md).

### Compared to GEPA, and to AlphaEvolve

**GEPA** ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457), ICLR 2026 Oral) is the reference point for prompt optimization today, and ships inside DSPy as `dspy.GEPA`; **AlphaEvolve** is the closest published peer on the code-evolution side. Same family and the same reflective core — a critique of what failed drives the next proposal, a population breeds and the weak die, and the scoring formula is declared once and wired into every eval path. GEPA evolves the instruction text of a DSPy program, AlphaEvolve evolves source code, PromptPotter evolves prompt and pipeline jointly. That is scope — a different target, not a missing feature; the table below is capability.

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |
| **Tunes the whole pipeline** — a chain of calls, not a single one: model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — the budget goes to the questions that separate candidates, and one is abandoned the moment the evidence says it loses ([PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)); scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded ([internals](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)) | 🟡 | 🟡 | 🟢 |
| **Carries knowledge forward** — parameter impact, query difficulty and failure patterns across runs, plus a library of proven personas, thinking styles and answer formats to recombine | 🔴 | 🟢 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger, so a replay reports what the work really cost | 🔴 | — | 🟢 |
| **Open & inspectable** — the code is on GitHub and a browser control plane shows the run as it happens; it is an object on disk you pause, resume, rewind or fork, not a script that has to finish | 🟡 | 🔴 | 🟢 |

🟡 is a partial version of the same capability — GEPA gates on a minibatch (one fixed slice, checked once) where PromptPotter runs a sequential test, evolves a chain's instruction text but not the parameters around it, discards a bad proposal instead of teaching another layer, and keeps its checkpointing in the standalone `gepa` package. AlphaEvolve's are that capability at the code level. A `—` is a closed service whose behaviour is not documented.

**Where each is ahead of us:** GEPA on **reach** — it sits inside DSPy, with that ecosystem's adapters, tracing and audience, which is why PromptPotter ships as a DSPy optimizer rather than asking anyone to leave it; AlphaEvolve on **code optimization**, its search reaching into source, which is not what PromptPotter is pointed at. Full grading: [`related-work.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#feature-highlights) — and DSPy's own engine, read from its source, in [§ What a DSPy source study settles](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#what-a-dspy-source-study-settles).

### Use it as a DSPy optimizer

`pip install promptpotter[dspy]`, then — where you would write `dspy.GEPA(…)`:

```python
from promptpotter.presentation.teleprompter import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset)
```

Your metric stays the scorer, and `compile()` returns a copy of your program with the winning prompt applied. It mints a real campaign on disk, so PoBB pruning, Rasch scoring, the block library, the measurement cache, the spend ceiling and the CLI verbs (`pause`, `resume --from N`, fork) all come along — what you trade away is the operator surfaces, not the search. [Full guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md).

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```

## Benchmarks

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

A head-to-head against DSPy's optimizers (**GEPA**, MIPROv2, BootstrapFewShot) and CAPO on *BIG-Bench Extra Hard (BBEH)*, held to a standard this literature mostly does not hold itself to: every method scored on the same held-out rows, one published split seed, one export schema for every method, and no cross-paper number mixing. Split, seed, metric and export schema are pinned in [`docs/research/bbeh-comparison/`](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/docs/research/bbeh-comparison/) — Colab notebooks for the peers, local for PromptPotter — and numbers publish once the target model and the optimization budget are held constant across every method too. What we measure on, what we refuse to measure on, and why: [`docs/research/benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Three-layer loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md) | [Install & env](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md) | [Benchmarks](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) |
| [State record](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward) | [Backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md) | [Metrics (HC, SE, R₉₀)](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/metrics.md) |
| [Self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md) | [Persistence, state, recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md) | [Related work](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md) |
| [Scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md) | [Observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md) | |
| [Campaign tree](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/campaign-tree.md) | [Whitelabel — run it under your own name](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/whitelabel.md) *(draft)* | |
| [Nodes and pipelines](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/node-standard.md) | [Use it as a DSPy optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) *(draft)* | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md). Statistical foundations under [`docs/methods/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md).

## Watching a run

While `python -m promptpotter resume` is running, the cleanest setup is **`campaigns/{campaign_id}/cycles/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `rounds/`, `log.md`, `index.json`. Internal resume + audit state lives under `.runtime/` (hidden by convention). Alternatives: `/potter-run` Claude Code skill, the notebook, or the webapp control plane at `http://localhost:8001/`. Full guide in [`docs/manual/04-reading-the-output.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).

> [!TIP]
> <details>
> <summary><b>TODO: What a round actually looks like</b> (click to expand)</summary>
>
> Every cycle writes a per-round digest to `log.md`. This is an excerpt from a real JustLogic run — the round that took it from 65.0% to 75.0%:
>
> ```
> ### Round 2 — Added systematic deduction step to thinking_style to force
> exhaustive derivation before defaulting to Uncertain, targeting the premature
> 'no direct info' pattern seen in #82, #37, #0. (75.0%)
>
> - improved: **yes**
> - samples: 28
> - composite_fitness: `0.7500`
>
> > Fix: thinking_style: Adopt a formal logical deduction method: translate each
> > premise into symbolic formulas, then derive the claim's truth value using
> > entailment rules. This counters the pattern of defaulting to Uncertain when
> > logical entailment exists, as seen in the 'When X is true' misinterpretation.
> > Axes: thinking_style, instruction, problem_description, answer_format
> > Failures:
> >   Logical misinterpretation (~5/14 misses): model fails to recognize that a
> >   premise phrased 'When X is true, it follows Y' asserts X as true, leading
> >   to over-hedging with Uncertain. Predicted: Uncertain, GT: TRUE. (Query #82)
>
> P(best) trajectory:
>   5536f04bc9 ▆▇▇▇▇▇▇████████▇▇▇█████   87.6% [winner]
>   c2ac162dea ▇▆▆▆▆▆▆▅▄▅▅▅▄▄▅▅▄▅▄▅▅▅▄   50.0%
> ```
>
> Those trajectory bars are [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md) at work — one tick per scored sample, and the trailing arm's posterior never recovers. What every stream a run produces means: [reading the output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).
>
> </details>
