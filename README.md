<p align="center">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/wizard.jpg" alt="The Potter" width="100">
  <img src="https://github.com/PromptPotter/prompt-potter-optimizer/raw/main/docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

<p align="center">
  <a href="https://promptpotter.com"><b>promptpotter.com</b></a> — <b>10 free optimization runs</b> on your own data, up to 10 rounds each, in the browser and on my key. Offer stands until <b>3 Dec 2026</b>; bring-your-own-key is in the works.
</p>

# PromptPotter: LLM-Driven Evolution of Prompts and Pipelines

[![PyPI](https://img.shields.io/pypi/v/promptpotter)](https://pypi.org/project/promptpotter/)
[![Python](https://img.shields.io/pypi/pyversions/promptpotter)](https://pypi.org/project/promptpotter/)
[![License](https://img.shields.io/badge/license-MIT-blue)](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE)

**PromptPotter evolves better prompts.** Most prompt engineering is manual. PromptPotter automates the generate → score → critique cycle: it tries many prompt and pipeline variations together, keeps memory across runs, and recovers on its own when a generated prompt produces broken output. Weak candidates are eliminated early on statistical confidence (*Posterior-of-Being-Best — [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)*), so the budget stops going to losers. Built for RAG pipelines, LLM agents and multi-step LLM workflows — drop in via CLI, Python, the `/potter-run` Claude Code skill, or as a [**DSPy optimizer**](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) where you would reach for GEPA.

## Quickstart

Python 3.13+ and an [OpenRouter key](https://openrouter.ai/keys) — the optimizer's default provider.

```bash
pip install "promptpotter[all]"
```

Bring a **labeled dataset**: input/output pairs as CSV, TSV, JSON, JSONL, or `.xlsx`. Point the optimizer at it and describe the job:

```bash
python -m promptpotter new tickets.csv --set task_description="classify support tickets by urgency"
python -m promptpotter resume
```

No key set yet? `new` offers to write one on first run. `resume` picks up wherever the last run stopped — Ctrl+C pauses a campaign rather than losing it.

Working in [Claude Code](https://claude.com/claude-code)? Type `/potter-run` instead: the bundled skill audits your setup, picks a dataset, runs init, and reads the rounds back to you as they land.

## 📺 Watch it work

- **[Live #2 — a Swiss-invoice campaign, run raw](https://www.youtube.com/watch?v=DLhb26ppX_s)** — small, unedited, cut with an opencode-go friction test.

## How to Optimize LLM Prompts in 3 Steps

Describe your 1️⃣ **task**, drop in a labeled 2️⃣ **dataset**, and 3️⃣ **run the loop**. The task is the goal you want the AI to hit; the dataset is examples of hitting it. Each round, PromptPotter generates variations 🧪, scores them ⚖️, and keeps the winners 🏆. It stops when results plateau. ✨ **Prompt optimized.**

> [!IMPORTANT]
> **New here?** Start with [`docs/manual/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md) — six chapters covering install → first run → reading output → troubleshooting.

## ⭐ Features

Every measurement costs money, so the whole design is **most fitness per dollar**: the budget goes to the comparisons that actually separate candidates, and nothing is measured twice.

The capabilities PromptPotter shares with the rest of the field are in the [comparison table](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#against-gepa-and-alphaevolve); these are the ones it doesn't:

- **💬 Chat-first** — talk to the Potter and watch it work inline, Perplexity-style: the searches, the tool calls, each round as it lands, and a button whenever a decision is yours. Ships as a reusable **chat-app template** — keep the chat core, delete the optimizer panes. [spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/chat-foundation.md)
- **Searches a tree, not a trail** — every round's result flows back to each ancestor it descends from, so a spent branch rewinds to whichever ancestor the evidence favours and ***climbs a different hill*** instead of stalling. An ancestor's score reflects what re-expanding from it actually yielded, including in branches it never ran itself.
- **Hard-sample leaderboard** — score preferentially on the samples that actually separate variants; the ones everyone aces or fails are noise.
- **🛡️ Guards against self-validation** — an optimizer that picks its own winner is the easiest thing in this field to fake. Three independent guards: scores are **subset-invariant ability** (Rasch θ), so a candidate that drifted onto easier samples cannot out-rank an honest one; **degenerate candidates** — a constant answer, a shape that games the scorer — are caught before they count; and the layer that **validates** a fix is never the layer that proposed it. What a winner's number may claim: [`verdict-resolution.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md).
- **Optimizes itself** — point the optimizer at its own optimizer prompts. [L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)
- **Pick your block library mode** — proven personas, thinking styles and answer formats (from PromptWizard and the *Self-Discover* modules it draws on, plus what our own runs turned up). Suggest from the library, restrict to it, or switch it off.
- **A campaign is an object on disk, not a script that has to finish** — pause it, resume it, rewind to any round, or fork a sibling and compare. Every artifact stamps the exact ledger position it is of, so any past moment is reconstructible. [How](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/stable-api.md#the-cut)

## Five ways to run it

1. **WebApp** — the browser control plane, at the domain root.
2. **`/potter-run` Claude Code skill** — drive a full campaign from your editor.
3. **CLI** — `python -m promptpotter new <name>` / `resume`.
4. **REST API**
5. **Embedded in Python** — a host program drives one campaign inside its own event loop. The [DSPy optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) is the simple-case entry into this one; it installs like any other DSPy optimizer and hands you the search, but not the operator surfaces.

A sixth — PromptPotter as a tool another agent calls (MCP) — is open for discussion in [#27](https://github.com/PromptPotter/prompt-potter-optimizer/issues/27). Opinions welcome.

## Reading a run

While a campaign runs, the cleanest setup is **`cycles/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**: the file is live scalar state (phase, round, candidate, accuracy, in-flight query), and the terminal prints HIT/MISS and per-round banners as they happen. Drill-down peers sit beside it — `rounds/`, `log.md`, `index.json`. Alternatives: the `/potter-run` skill, a notebook, or the webapp at `http://localhost:8001/`. Full guide: [reading the output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).

<details>
<summary><b>What a round actually looks like</b> (click to expand)</summary>

Every cycle writes a per-round digest to `log.md`. This is a verbatim excerpt from a real JustLogic run — what the artifact looks like, not a result to read a number off:

```
### Round 2 — Added systematic deduction step to thinking_style to force
exhaustive derivation before defaulting to Uncertain, targeting the premature
'no direct info' pattern seen in #82, #37, #0. (75.0%)

- improved: **yes**
- samples: 28
- composite_fitness: `0.7500`

> Fix: thinking_style: Adopt a formal logical deduction method: translate each
> premise into symbolic formulas, then derive the claim's truth value using
> entailment rules. This counters the pattern of defaulting to Uncertain when
> logical entailment exists, as seen in the 'When X is true' misinterpretation.
> Axes: thinking_style, instruction, problem_description, answer_format
> Failures:
>   Logical misinterpretation (~5/14 misses): model fails to recognize that a
>   premise phrased 'When X is true, it follows Y' asserts X as true, leading
>   to over-hedging with Uncertain. Predicted: Uncertain, GT: TRUE. (Query #82)

P(best) trajectory:
  5536f04bc9 ▆▇▇▇▇▇▇████████▇▇▇█████   87.6% [winner]
  c2ac162dea ▇▆▆▆▆▆▆▅▄▅▅▅▄▄▅▅▄▅▄▅▅▅▄   50.0%
```

Those trajectory bars are [PoBB](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md) at work — one tick per scored sample, and the trailing arm's posterior never recovers.

A percentage in a round banner is an **in-campaign reading, computed on the rows that selected the winner** — so it is not a held-out result and a level should never be compared across rounds. Which figures are clean, which are biased upward, and what we do about it: [`benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

</details>

## Common questions

- **What does L1 actually mutate?** The prompt template's fields (persona, task instruction, …) plus whatever your `pipeline.yaml` declares as tunable. See [`the-loop.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward).
- **My scoring formula was wrong — did I lose results?** No. Traces are facts; scores are policy. The optimizer rescores on load and replays decisions; on divergence, fork. See [`scoring-and-memory.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md).
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)); stalls escalate L1 → L2 → L3 ([the-loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/05-troubleshooting.md).

## Limitations

- **Parameter-based optimization only.** It optimizes any pipeline that exposes tunable parameters — prompts, thresholds, model settings — but not model weights, neural architectures, or modality-specific representations.
- **Requires a labeled dataset.** Input/output pairs are mandatory.
- **In-campaign figures are not held-out figures.** The winner's own number is read off the rows that selected it, so it overstates deployment performance. A selection-clean partition is [on the roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md); published head-to-head numbers use a reserved split instead.

---

## Where it sits

PromptPotter belongs to the **LLM-driven evolution** family — an LLM proposes variants, a scorer ranks them, the winners breed. Every prompt system in that family mutates one artifact, the prompt; the code-side systems mutate source. **PromptPotter evolves the prompt and the pipeline parameters around it, jointly.**

Who else is in the family, the line behind it, why test-time compute belongs in tuning rather than in the request, and a capability table against GEPA and AlphaEvolve: [`docs/research/related-work.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md).
## Benchmarks

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

A head-to-head against DSPy's optimizers (**GEPA**, MIPROv2, BootstrapFewShot) and CAPO on *BIG-Bench Extra Hard (BBEH)*, held to a standard this literature mostly does not hold itself to: every method scored on the same held-out rows, one published split seed, one export schema for every method, and no cross-paper number mixing. Split, seed, metric and export schema are pinned in [`docs/research/bbeh-comparison/`](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/docs/research/bbeh-comparison/) — Colab notebooks for the peers, local for PromptPotter — and **numbers publish once** the target model and the optimization budget are held constant across every method too. What we measure on, what we refuse to measure on, and why: [`docs/research/benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Methods & research |
|---|---|---|
| [Three-layer loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md) | [Install & env](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md) | [Benchmarks + metrics](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) |
| [Scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md) | [Persistence, state, recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md) | [Verdict resolution](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md) |
| [Structured output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/structured-output.md) | [Observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md) | [Candidate elimination](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md) |
| [Nodes and pipelines](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/node-standard.md) | [Backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md) | [Self-healing internals](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md) |

Status and the full forward plan: [`docs/specs/roadmap.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md). Documentation index: [`docs/README.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md). The stable programmatic surface is [`stable-api.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/stable-api.md); contributor rules are [`conventions.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/conventions.md).

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```
