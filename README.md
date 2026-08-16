<p align="center">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

# PromptPotter: LLM-Driven Evolution of Prompts and Pipelines

**PromptPotter evolves better prompts.** Most prompt engineering is manual. PromptPotter automates the generate → score → critique cycle. It tries multiple prompt and pipeline variations together, keeps memory across runs, and recovers automatically when a generated prompt produces broken output. Weak candidates get eliminated early on statistical confidence (*Posterior-of-Being-Best — [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)*) so you don't burn LLM budget on losers. Built for RAG pipelines, LLM agents, and multi-step LLM workflows — drop in via CLI, Python SDK, or the `/potter-run` Claude Code skill.

## How to Optimize LLM Prompts in 3 Steps

Describe your 1️⃣ **task**, drop in a labeled 2️⃣ **dataset**, and 3️⃣ **run the loop**. The task is the goal you want the AI to hit; the dataset is examples of hitting it. Each round, PromptPotter generates variations 🧪, scores them ⚖️, and keeps the winners 🏆. It stops when results plateau. ✨ **Prompt optimized.**

> [!IMPORTANT]
> **New here?** Start with [`docs/manual/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md) — six chapters covering install → first run → reading output → troubleshooting.

## ⭐ Features

Every measurement costs money, so the whole design is **most fitness per dollar**. The capabilities PromptPotter shares with the rest of the field are in the [comparison table](#scientific-framing) below; these are the ones it doesn't:

- **💬 Chat-first** — talk to the Potter and watch it work inline, Perplexity-style: the searches, the tool calls, each round as it lands, and a button whenever a decision is yours. Ships as a reusable **chat-app template** — keep the chat core, delete the optimizer panes. [spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/chat-foundation.md)
- **Climbs the hill, escapes dead ends** — each round steps uphill on your metric; when a branch is spent the search rewinds to a better ancestor and climbs a different ridge instead of stalling. Most evolutionary search is one-armed: it only ever expands the latest winner.
- **Hard-sample leaderboard** — score preferentially on the samples that actually separate variants; the ones everyone aces or fails are noise.
- **Guards against self-validation** — the loop can't grade itself into a false win: scores are ability-based and subset-invariant (Rasch θ), constant-answer and other degenerate candidates are caught before they count, and the layer that *validates* a fix is never the one that proposed it.
- **Optimizes itself** — point the optimizer at its own optimizer prompts. [L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/optimizer-of-the-optimizer.md)
- **Pick your block library mode** — proven personas, thinking styles and answer formats (from PromptWizard and the *Self-Discover* modules it draws on, plus what our own runs turned up). Let the optimizer suggest from the library, restrict it to the library, or switch it off.

## Five ways to run it

1. **`/potter-run` Claude Code skill** — drive a full campaign from your editor.
2. **CLI** — `python -m promptpotter new <name>` / `resume`.
3. **Python / Jupyter notebook**.
4. **REST API**.
5. **WebApp** — read-only dashboard at `http://localhost:8001/`.

**Direction — the sixth way: a tool another agent calls.** The aim is parity as a first-class **agent-callable tool** (MCP), so an *operating agent* — yours, or an ML-research agent like NVIDIA's AutoResearch — can invoke PromptPotter as its *try-harness-first* move before reaching for fine-tuning. Why + a same-dataset head-to-head: [related-work § PromptPotter × NVIDIA AutoResearch](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md); tracked as [roadmap § Agent-tool parity](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md) (C5, MCP server mode).

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

- **Code evolution** — [AlphaEvolve](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [OpenEvolve](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [AlgoTuner](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [AutoResearch](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella)
- **Prompt evolution** — [PromptWizard](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [MIPROv2](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · [GEPA](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#systems-under-the-umbrella) · **PromptPotter**

The code-evolution systems mutate source; the prompt-evolution systems mutate a prompt. PromptPotter evolves **both the prompt and the pipeline parameters around it**, jointly.

*Background: Asankhaya Sharma on [OpenEvolve](https://www.youtube.com/watch?v=mWBT-szUutI) (talk).*

## Scientific framing

PromptPotter is a **tree search over prompt programs** — precisely, **AlphaZero-shaped MCTS over the lineage**: [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#best-arm-identification--sequential-testing) prunes losers *within* a round, each round's ability backpropagates to its ancestors, and a UCB rule picks the ancestor to re-expand once a branch is spent. Rollouts give way to deterministic evaluation on your dataset, as in AlphaZero. [Comparison](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#comparison-to-mcts).

### The loop

Each round is **generate → score → critique**: L1 proposes candidates, they're scored against your dataset, and the critique steers the next round. When the inner layer stalls, an outer layer (L2, then L3) redirects it; when the branch itself is spent, the search rewinds. Full mechanics: [the-loop.md](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md).

**AlphaEvolve** is the closest published peer — same loop, same memory across runs; read row one as scope (a different target, not a missing feature), the rest as capability.

| | AlphaEvolve | PromptPotter |
|---|---|---|
| **What it evolves** | Source code | Prompts **and** pipeline parameters, jointly |

| Capability | AlphaEvolve | PromptPotter |
|---|:--:|:--:|
| **Open & inspectable** — the code is on GitHub, and the statistical model (PoBB) is documented and yours to tune; AlphaEvolve is a closed hosted service | 🔴 | 🟢 |
| **Evolutionary search** — a population breeds, the weak die | 🟢 | 🟢 |
| **Automatic scoring** — define the formula once; it wires itself into every eval path, no glue code | 🟢 | 🟢 |
| **Statistical pruning** — drop losers after a handful of queries, not the full budget ([PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)) | 🟢 | 🟢 |
| **Memory across runs** — parameter impact, query difficulty and failure patterns carry into the next run | 🟢 | 🟢 |
| **A library of building blocks** — proven personas, thinking styles and answer formats, reused and recombined | 🟢 | 🟢 |
| **Multi-step pipelines** — tune a chain of calls, not a single one | 🟡 | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught, not just discarded ([internals](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)) | 🟡 | 🟢 |
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🟢 |

The one place AlphaEvolve is unambiguously stronger is **code optimization** — its search reaches into source, which is not what PromptPotter is pointed at. The 🟡s are partial versions of the same capability. Full grading, including the prompt-tooling neighbours: [`related-work.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#feature-highlights).

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

Head-to-head comparison on the *BIG-Bench Extra Hard (BBEH)* benchmark against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring, no cross-paper number mixing. See [`docs/research/benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/docs/research/bbeh-comparison/) for reproducible Colab notebooks.

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Three-layer loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md) | [Install & env](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md) | [Benchmarks](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) |
| [State record](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward) | [Backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md) | [Metrics (HC, SE, R₉₀)](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/metrics.md) |
| [Self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md) | [Persistence, state, recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md) | [Related work](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md) |
| [Scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md) | [Observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md) | |
| [Campaign tree](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/campaign-tree.md) | [Whitelabel — run it under your own name](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/whitelabel.md) *(draft)* | |
| [Nodes and pipelines](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/node-standard.md) | [Use it as a DSPy optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) *(draft)* | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md). Statistical foundations under [`docs/methods/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/README.md).

## Watching a run

While `python -m promptpotter resume` is running, the cleanest setup is **`campaigns/{campaign_id}/cycles/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `rounds/`, `log.md`, `index.json`. Internal resume + audit state lives under `.runtime/` (hidden by convention). Alternatives: `/potter-run` Claude Code skill, the notebook, or the read-only webapp at `http://localhost:8001/`. Full guide in [`docs/manual/04-reading-the-output.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).

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
