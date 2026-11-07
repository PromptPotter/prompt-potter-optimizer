<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/potter.jpg" alt="The Potter" width="64">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/wordmark.png" alt="PromptPotter" width="300">
</p>

<h1 align="center">An LLM-driven prompt optimizer for the whole pipeline</h1>

<p align="center">
  Hand it a labeled dataset and a pipeline. It evolves the prompt <b>and</b> the pipeline's parameters together,<br>
  cuts a losing candidate the moment the evidence says it will not win, and shows you every measurement behind the winner.
</p>

<p align="center">
  <a href="https://github.com/PromptPotter/prompt-potter-optimizer/actions/workflows/ci.yml"><img src="https://github.com/PromptPotter/prompt-potter-optimizer/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/v/promptpotter" alt="PyPI"></a>
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/dm/promptpotter" alt="Downloads"></a>
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/pyversions/promptpotter" alt="Python"></a>
  <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT--RG-blue" alt="License: MIT-RG"></a>
</p>

<p align="center">
  <a href="https://app.promptpotter.com"><b>Open the app →</b></a> &nbsp;·&nbsp; nothing to install, sign in and bring your examples<br>
  <sub>Or <code>pip install promptpotter</code> and run it on your machine; or self-host it for your team.</sub>
</p>

<p align="center">
  <b>It sits behind your stack, never in place of it.</b> PromptPotter calls your pipeline the way your users do, measures it on your examples, and hands back a better configuration as a file.
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png" alt="The PromptPotter control plane on a finished campaign" width="100%">
  </picture>
</p>

<p align="center"><sub>A real, finished campaign — seven rounds on JustLogic. Every figure on this page is an in-campaign reading from a small run, not a benchmark result.</sub></p>

## Works with what you already run

| You built it with | How PromptPotter meets it |
|---|---|
| **An agent framework, a managed agent service or a workflow builder** — LangGraph, CrewAI, the OpenAI Agents SDK, Bedrock Agents, n8n, Dify, or plain FastAPI | A `Connector` declaration; an HTTP backend's default wire is three endpoints — `POST /matches`, `GET /pipeline`, `GET /status` — and the knobs your pipeline declares become what is tuned ([backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md)) |
| **DSPy** | `PromptPotterOpt`, where you would reach for GEPA ([dspy-optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md)) |
| **An agent in a sandbox** | Each candidate runs as a [Harbor](https://github.com/laude-institute/harbor) trial, graded by the task's own checker |
| **Langfuse / MLflow** | Every round fans out as traces; the winner lands in MLflow's Prompt Registry ([observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md)) |
| **OpenRouter, OpenAI, Anthropic, Groq** | Any of them, for your pipeline and for the optimizer |

**What you get back:** `export.json` — the winning prompt and settings by field name, with the evidence behind them. We never load, host, route or hot-swap anything in your production path.

## What it does differently

**Prompt optimization that stops paying for losers.** Every measurement costs money, so the whole design is *most fitness per dollar*.

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-replay.svg" alt="Replay of one real round: three candidates measured, the third eliminated after 7 of 34 samples" width="880">
</p>

<p align="center"><sub>One real round, replayed from its <code>readout.log</code>: candidate 3 is cut after <b>7 of 34</b> samples; the other 27 are never bought — <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md">Posterior-of-Being-Best</a>.</sub></p>

- **It moves the pipeline, not just the words.** Model, temperature, node thresholds, the output schema — whatever your `pipeline.yaml` declares tunable — mutate and are selected jointly with the prompt.
- **The budget goes where candidates separate.** Scoring goes to the samples that tell candidates apart; anything already measured is replayed, never re-bought. Every number opens down to the call: one candidate on one sample, with prediction, ground truth, the exact input each node received, and whether it was paid for or replayed.
- **It does not certify its own winner.** Scores are a subset-invariant ability (Rasch θ), and each round says when its lift is noise ([what a winner's number may claim](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md)).
- **A campaign is an object on disk.** Each round is a JSON file the app renders as a scoreboard; pause, resume, rewind to any round, or fork a sibling and compare ([persistence and recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md)).

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tour-dark.gif" alt="A walk through the app: the chat thread, the run card, the dashboard, the sample leaderboard, one opened cell" width="100%">
</p>

<p align="center"><sub>📺 Longer: <a href="https://www.youtube.com/watch?v=DLhb26ppX_s">Live #2 — a Swiss-invoice campaign, run raw</a>.</sub></p>

## Quickstart

Python 3.13+ and an [OpenRouter key](https://openrouter.ai/keys) — the optimizer's default provider.

```bash
pip install "promptpotter[all]"
python -m promptpotter new tickets.csv --set task_description="classify support tickets by urgency"
python -m promptpotter resume
```

Bring a **labeled dataset** — input/output pairs as any delimited text, JSON, JSONL or a spreadsheet — and say what the job is. `resume` picks up wherever the last run stopped: Ctrl+C pauses a campaign rather than losing it. The DSPy and Harbor connectors are opt-in extras (`[dspy]`, `[harbor]`).

> [!IMPORTANT]
> **`new` needs a pipeline backend to score against** — the default is [TermNorm](https://github.com/runfish5/TermNorm-excel) on `:8000` ([setting it up by hand](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/03-first-campaign.md#what-if-i-dont-have-a-backend)).
> **Shortest path:** in [Claude Code](https://claude.com/claude-code), type `/potter-run` — it starts that backend, audits your setup, and reads the rounds back to you as they land.

To watch it in the app shown above:

```bash
python -X utf8 -m uvicorn promptpotter.main:app --port 8001    # then open http://localhost:8001/
```

Already in DSPy? `from promptpotter.presentation.teleprompter import PromptPotterOpt` installs where you would reach for GEPA ([`dspy-optimizer.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md)). Six chapters from install to troubleshooting: [the manual](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md).

## Five entry points

| | |
|---|---|
| **Webapp** | The chat-first browser control plane — [app.promptpotter.com](https://app.promptpotter.com) hosted, `http://localhost:8001/` local ([spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/host-assistant.md)). |
| **`/potter-run`** | Claude Code skill — an agent launches, supervises and diagnoses a campaign from your editor. |
| **CLI** | `python -m promptpotter new <name>` / `resume`. |
| **REST API** | Every control verb the webapp issues, for when another system drives the campaign. |
| **Embedded in Python** | A host program drives one campaign inside its own event loop; the DSPy optimizer is the simple-case entry. |

## How the search works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: generate, score and critique repeat until a stopping rule fires; L2 and L3 escalate lazily on a stall" width="760">
  </picture>
</p>

Every round generates, scores and critiques (L1). When L1 stalls, L2 rewrites *how* it searches; when L2 stalls, L3 rewrites the plan. The loop exits on a **stopping rule** — goal, budget or plateau — not a fixed trial count ([the loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)). **It searches a tree, not a trail**: when a branch is spent, a UCB rule picks the ancestor to re-expand — AlphaZero-shaped MCTS over the lineage, with deterministic evaluation in place of rollouts ([the mapping](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/landscape.md#comparison-to-mcts)).

PromptPotter belongs to the **LLM-driven evolution** family beside [GEPA](https://arxiv.org/abs/2507.19457), [MIPROv2](https://arxiv.org/abs/2406.11695), [CAPO](https://arxiv.org/abs/2504.16005) and AlphaEvolve; **it evolves a whole declared pipeline jointly with the prompt**, and it is a bench built to run other people's optimizers under one grader, one budget meter and one stopping rule. The capability comparison: [the landscape](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/landscape.md#against-gepa-and-alphaevolve); what we measure on: [promptpotter.com/benchmarks](https://promptpotter.com/benchmarks).

## Extending the engine

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
python scripts/gate.py          # every check CI runs; the first run builds .venv from uv.lock
```

The optimizer is itself a pipeline of nodes, so it can be pointed at its own optimizer prompts ([self-optimization, L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)), and every optimizer runs end to end at zero spend ([`offline-run.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/offline-run.md)). Start at **[the developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md)**, then [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Limitations

- **Parameter-based optimization only** — prompts, thresholds, model settings; not model weights or architectures.
- **Needs a known answer or a verifier** — the example's expected output, or the backend's own checker (Harbor).
- **In-campaign figures are not held-out figures.** The headline is the winner's score on a held-out bench set no optimizer sees: `python -m promptpotter bench`.

## Documentation

[The manual](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md) · [docs index](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md) · [architecture](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/architecture.md) · [roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md)

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```
