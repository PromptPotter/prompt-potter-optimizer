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
  <sub><b>10 free optimization runs</b>, up to 10 rounds each, on our key, until <b>3 Dec 2026</b>. Or <code>pip install promptpotter</code> and run it on your machine; or self-host it for your team.</sub>
</p>

<p align="center">
  <b>It sits behind your stack, never in place of it.</b> Your models, your framework, your agent harness and your tracing stay exactly as they are.<br>
  PromptPotter calls your pipeline the way your users do, measures it on your examples, and hands back a better configuration as a file.<br>
  Your code is never edited, forked or redeployed.
</p>

<p align="center">
  <a href="#works-with-what-you-already-run">Works with your stack</a> ·
  <a href="#what-you-get-back">What you get back</a> ·
  <a href="#what-it-does-differently">What it does differently</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#five-entry-points">Entry points</a> ·
  <a href="#how-the-search-works">How the search works</a> ·
  <a href="#how-it-compares-to-gepa-and-alphaevolve">vs GEPA / AlphaEvolve</a> ·
  <a href="https://promptpotter.com/benchmarks">Benchmarks</a> ·
  <a href="#extending-the-engine">Extending the engine</a> ·
  <a href="#documentation">Docs</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png" alt="The PromptPotter control plane: a finished campaign in the chat view, with its pipeline, its rounds, the trend, and the hard-sample ranking" width="100%">
  </picture>
</p>

<p align="center"><sub>
  The control plane on a real, finished campaign — seven rounds on JustLogic. Nothing here is staged: the thread itself reports that round 6 <i>resolved nothing</i>.<br>
  Every figure on this page is an in-campaign reading from a small run, not a benchmark result.
</sub></p>

## Works with what you already run

Whatever you built it with, PromptPotter meets it where it already runs:

| You built it with | How PromptPotter meets it |
|---|---|
| **An agent framework** — LangGraph, LlamaIndex, CrewAI, Microsoft Agent Framework / Semantic Kernel, Google ADK, AWS Strands Agents, the OpenAI Agents SDK, Haystack, Spring AI, or plain FastAPI | Put it behind three endpoints — `POST /matches`, `GET /pipeline`, `GET /status` — and the knobs your `/pipeline` declares become what is tuned ([backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md)) |
| **A managed agent service** — AWS Bedrock Agents, Google Vertex AI Agent Engine, Azure AI Foundry | Your deployed agent already has an endpoint; a thin adapter in front of it answers the same three |
| **A workflow builder** — n8n, Dify, Langflow, Flowise | The same three endpoints in front of the workflow's API; the prompts and node settings it exposes become the knobs |
| **DSPy** | `PromptPotterOpt`, where you would reach for GEPA ([dspy-optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md)) |
| **An agent that works in a sandbox** | Each candidate runs as a [Harbor](https://github.com/laude-institute/harbor) trial, graded by the task's own checker; the agent's trajectory is the evidence for the next mutation |
| **Langfuse / MLflow** | Every round fans out as traces; the winner lands in MLflow's Prompt Registry ([observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md)) |
| **OpenRouter, OpenAI, Anthropic, Groq** | Any of them, for your pipeline and for the optimizer; OpenRouter is the default |

## What you get back

`export.json` — the winning prompt and settings by field name, with the evidence behind them. We write that file and provide a reader; we never load, host, route or hot-swap anything in your production path.

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/compare-dark.png" alt="The compare view: the origin C0 beside two later candidates on ARC-Challenge, each with its fitness and interval, what it cost, its model settings, and the prompt it ran field by field" width="100%">
</p>

<p align="center"><sub>
  The starting prompt C0 beside two candidates the search found. Each column has its fitness with an interval, so you can see how far apart they really are, plus what it cost per call, the model settings it ran on (🔒 means the optimizer may not touch it) and the prompt itself, field by field. Tick any campaigns, from any dataset, to put them on one board.
</sub></p>

## What it does differently

**Prompt optimization that stops paying for losers.** Every measurement costs money, so the whole design is *most fitness per dollar*.

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-replay.svg" alt="Replay of one real round: three candidates measured, the third eliminated after 7 of 34 samples, the first selected." width="880">
</p>

<p align="center"><sub>
  One round of a real campaign, replayed line for line from its <code>readout.log</code>. Candidate 3 is cut after <b>7 of 34</b> samples; the other 27 are never bought — <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md">Posterior-of-Being-Best</a>.
</sub></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg" alt="One round: a parent is mutated into three candidates, each is measured sample by sample, the third is cut after 7 of 34 samples, and the winner becomes the next parent." width="880">
  </picture>
</p>

Four more claims follow, with the screens that back them.

### It moves the pipeline, not just the words

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png" alt="The run card in the chat thread: trend per round, spend, which prompt fields and node parameters the best candidate changed, and how many samples flipped against the origin" width="100%">
  </picture>
</p>

Model, temperature, node thresholds, the output schema — whatever your `pipeline.yaml` declares tunable — mutate and are selected jointly with the prompt. In the round replayed above, two of the three mutations were to a schema field description, not to the prompt. [Reading the run card →](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md)

### The budget goes where candidates separate

Scoring goes to the samples that tell candidates apart; anything already measured is replayed, never re-bought.

<table>
<tr>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png" alt="One measured cell opened: prediction, ground truth, the exact prompt sent to the node, and whether it was replayed from the archive" width="100%">
  </picture>
  <br>
  <b>Every number opens down to the call.</b> One cell = one candidate on one sample: prediction, ground truth, the exact input each node received, tokens, seconds — and whether it was paid for or replayed from the archive.
</td>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png" alt="The candidates chart: every candidate of the campaign with accuracy, ability and overlap, intervals drawn, and a served caveat above it" width="100%">
  </picture>
  <br>
  <b>Every candidate, with its interval.</b> Accuracy, ability θ and the shared-cell overlap for each individual across all generations, with the lineage beneath. The caveat above the chart is served by the backend, not decided in the browser.
</td>
</tr>
</table>

### It does not certify its own winner

Scores are a subset-invariant ability (Rasch θ), and each round says when its lift is noise. [What a winner's number may claim →](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md)

### A campaign is an object on disk

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png" alt="The files view: the cycle's round files on disk, with round 6 open as a scoreboard and its verdict" width="100%">
  </picture>
</p>

Not a script that has to finish. Each round is a JSON file the app renders as a scoreboard; pause, resume, rewind to any round, or fork a sibling and compare. [Persistence and recovery →](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md)

<details>
<summary><b>The whole app in one walk</b> (GIF)</summary>

<br>

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tour-dark.gif" alt="A walk through the app: the chat thread, the run card, the dashboard, the sample leaderboard, one opened cell" width="100%">
</p>

The thread, the run card, the optimizer's own loop, the sample leaderboard, one measured cell opened. 📺 Longer: [Live #2 — a Swiss-invoice campaign, run raw](https://www.youtube.com/watch?v=DLhb26ppX_s).

</details>

## Quickstart

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/potter-loop.jpg" alt="The Potter driving a failing pipeline through the loop into a verified one" width="400">
</p>

Python 3.13+ and an [OpenRouter key](https://openrouter.ai/keys) — the optimizer's default provider.

```bash
pip install "promptpotter[all]"
python -m promptpotter new tickets.csv --set task_description="classify support tickets by urgency"
python -m promptpotter resume
```

Bring a **labeled dataset** — input/output pairs as any delimited text, JSON, JSONL or a spreadsheet (`.xlsx`, `.xls`, `.ods`) — and say what the job is. No key set yet? `new` offers to write one on first run. `resume` picks up wherever the last run stopped: Ctrl+C pauses a campaign rather than losing it.

> [!IMPORTANT]
> **`new` needs a pipeline backend to score against** — the default is [TermNorm](https://github.com/runfish5/TermNorm-excel) on `:8000`, and a campaign refuses to start without one ([setting it up by hand](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/03-first-campaign.md#no-backend)).
>
> **Shortest path:** in [Claude Code](https://claude.com/claude-code), type `/potter-run`. It downloads and starts that backend for you, audits your setup, picks a dataset, runs init, and reads the rounds back to you as they land.

To watch it in the app shown above:

```bash
python -X utf8 -m uvicorn promptpotter.main:app --port 8001    # then open http://localhost:8001/
```

<details>
<summary><b>Already in DSPy?</b> It installs where you would reach for GEPA</summary>

<br>

```python
from promptpotter.presentation.teleprompter import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset)
```

What you keep and what you trade away that way: [`dspy-optimizer.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md).

</details>

Six chapters from install to troubleshooting: [the manual](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md).

## Five entry points

One campaign, driven from wherever the work already happens:

| | |
|---|---|
| **Webapp** | The browser control plane on this page, at the domain root — [app.promptpotter.com](https://app.promptpotter.com) hosted, `http://localhost:8001/` local. Chat-first: talk to the Potter and watch it work inline, with a button whenever a decision is yours. Ships as a reusable chat-app template ([spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/chat-foundation.md)). |
| **`/potter-run`** | Claude Code skill — an agent launches, supervises and diagnoses a full campaign from your editor. |
| **CLI** | `python -m promptpotter new <name>` / `resume`. |
| **REST API** | Every control verb the webapp issues, for when another system drives the campaign. |
| **Embedded in Python** | A host program drives one campaign inside its own event loop; the [DSPy optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) is the simple-case entry. |

A sixth — PromptPotter as a tool another agent calls (MCP) — is open for discussion in [#27](https://github.com/PromptPotter/prompt-potter-optimizer/issues/27).

## How the search works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: a task and a labeled dataset enter an optimizer setup, then generate, score and critique repeat until a stopping rule fires. L2 and L3 escalate lazily on a stall; a measurement archive replays instead of re-measuring." width="760">
  </picture>
</p>

Every round generates, scores and critiques (L1). When L1 stalls, L2 rewrites *how* it searches; when L2 stalls, L3 rewrites the plan. The loop exits on a **stopping rule** — goal, budget or plateau — not a fixed trial count. [The loop →](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg" alt="A search tree: origin, rounds 1 and 2, then a branch through rounds 3 and 4 that is spent. The search rewinds to round 2 and a second branch reaches the winner." width="880">
  </picture>
</p>

**It searches a tree, not a trail.** When a branch is spent, a UCB rule picks the ancestor to re-expand, so the search climbs a different hill instead of stalling — AlphaZero-shaped MCTS over the lineage, with deterministic evaluation in place of rollouts ([the mapping](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md#comparison-to-mcts)).

## How it compares to GEPA and AlphaEvolve

PromptPotter belongs to the **LLM-driven evolution** family — an LLM proposes variants, a scorer ranks them, the winners breed. On the prompt side that family is [GEPA](https://arxiv.org/abs/2507.19457), [MIPROv2](https://arxiv.org/abs/2406.11695), [PromptWizard](https://arxiv.org/abs/2405.18369) and [CAPO](https://arxiv.org/abs/2504.16005); on the code side AlphaEvolve and OpenEvolve. GEPA evolves the instruction text of a DSPy program, AlphaEvolve evolves source code, **PromptPotter evolves a whole declared pipeline jointly with the prompt** — a different target, not a missing feature.

The table is capability, not a benchmark result:

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Tunes the whole pipeline** — model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger | 🔴 | — | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded | 🟡 | 🟡 | 🟢 |
| **Carries knowledge across runs** — parameter impact, sample difficulty and failure patterns survive the campaign | 🟡 | 🟢 | 🟢 |
| **Open and inspectable** — a browser control plane, and a campaign you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |

<sub>🟡 is a partial version of the same capability; AlphaEvolve's are that capability at the code level; `—` is a closed service whose behaviour is not documented. GEPA keeps a candidate pool <i>within</i> a run — its 🟡 on knowledge is about what survives it. One documented exception to the pricing row: a DSPy call replayed from DSPy's own cache is not counted.</sub>

**Where the others are ahead.** GEPA has reach — DSPy's adapters, tracing and audience, which is why PromptPotter also installs as a DSPy optimizer. Opik ships six optimizers behind one API; Langfuse has production telemetry. The full argument: [related work](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md).

### The bench

Peers ship an algorithm; PromptPotter ships an algorithm *and* the interface around it, and is built to run **other people's optimizers inside that interface** — one grader, one budget meter, one exam, one stopping rule, so their numbers may sit in one table ([what is held equal](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#a-menu-is-not-a-bench--the-honest-version)). Manifests for `potter`, `capo`, `levi` and `gepa` are in the tree; no comparison number is published until one peer — CAPO first — has run end to end in a head-to-head whose only difference is the optimizer.

What we measure on, each dataset's starting score and the protocol a published number must pass: [promptpotter.com/benchmarks](https://promptpotter.com/benchmarks).

## Extending the engine

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
pip install -e ".[all,dev]"
python scripts/gate.py          # every check CI runs
```

The optimizer is itself a pipeline of nodes, so it can be pointed at its own optimizer prompts — an outer campaign whose individuals are configurations of the inner optimizer ([self-optimization, L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)). Every optimizer runs end to end at zero spend ([`offline-run.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/offline-run.md)). Start at **[the developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md)** — the engine's four seams on one page — then [`CONTRIBUTING.md`](CONTRIBUTING.md).

## In depth

<details>
<summary><b>Reading a run</b> — what lands on disk, and what a number may claim</summary>

<br>

While a campaign runs, keep **`cycles/{cycle_id}/dashboard.json`** open in an auto-reloading editor beside the terminal: the file is live scalar state (phase, round, candidate, accuracy, in-flight query), the terminal prints HIT/MISS and per-round banners. Drill-down peers sit beside it — `rounds/`, `log.md`, `index.json`, and `readout.log`, the terminal mirrored to a file. Full guide: [reading the output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).

Every cycle writes a per-round digest to `log.md`. Verbatim, from the round replayed above — what the artifact looks like, not a result to read a number off:

```
- improved: **yes**
- samples: 34
- composite_fitness: `0.4237`
- ability θ: `+0.308` (ruler 5cd26f22e62708a4, 68 cells, 1PL)
- overlap: 34 shared cells, +16 measured: C0 41.2%  →  C1.2 44.1%  →  C4.1 52.9%
- verdict: C4.1 won on θ lift +0.216 = +0.072 over the parent + 0.144 parent selection bias (θ +0.293 vs parent +0.221, se 0.196); runner-up C4.2 at +0.149
- cost: **$0.0311** (optimizer $0.0048 c23% ·w4384 · backend $0.0137 c17% · bench $0.0127 c37%)

P(best) trajectory:
  f8356ad77a ▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅   50.0% [winner]
  6f7d643a1e ▇▆▅▅▅▅▅▅▅▄▅▄▅▄▅▅▅▅▅▅▅▅▅▅▅▅▅▅▄   49.7%
  a2169d009a ▂▂   15.8%
```

A percentage in a round banner is an **in-campaign reading, computed on the rows that selected the winner** — not a held-out result, and a level should never be compared across rounds. A round is won on θ, not on the accuracy column, and the verdict line keeps the lift over the parent apart from the parent's selection bias instead of adding them up quietly. Which figures are clean, which are biased upward, and what we do about it: [`benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

</details>

<details>
<summary><b>Common questions</b></summary>

<br>

- **What does L1 actually mutate?** The prompt template's fields (persona, task instruction, …) plus whatever your `pipeline.yaml` declares as tunable. See [`the-loop.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward). It can draw suggestions from a block library of proven personas, thinking styles and answer formats (from PromptWizard and the *Self-Discover* modules it draws on, plus what our own runs turned up), be restricted to that library, or run without it.
- **My scoring formula was wrong — did I lose results?** No. The optimizer rescores on load and replays decisions; on divergence, fork. See [`scoring-and-memory.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md).
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)); stalls escalate L1 → L2 → L3 ([the-loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/05-troubleshooting.md).
- **Why not search at request time instead?** PromptPotter finds a configuration once; from then on it is an ordinary call with no search in the path. [Test-time compute, moved out of the request.](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#test-time-compute-moved-out-of-the-request)
- **Is this a model router?** No. Live model selection per question is a gateway ([OpenRouter](https://openrouter.ai), [OmniRoute](https://github.com/diegosouzapw/OmniRoute)). PromptPotter tunes the thing a gateway ends up calling, so the two compose.
- **Where are the benchmark numbers?** Not published yet, on purpose: DSPy's optimizers and CAPO on BBEH, every method on the same held-out rows, target model and budget held constant. [`benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) · [harness](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/docs/research/bbeh-comparison/)

</details>

## Limitations

- **Parameter-based optimization only.** It optimizes any pipeline that exposes tunable parameters — prompts, thresholds, model settings — but not model weights, neural architectures, or modality-specific representations.
- **Requires a labeled dataset.** Input/output pairs are mandatory.
- **In-campaign figures are not held-out figures.** The winner's own number is read off the rows that selected it, so it overstates deployment performance. A selection-clean partition is [on the roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md); published head-to-head numbers use a reserved split instead.

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Methods & research | 🔧 Contributing |
|---|---|---|---|
| [Three-layer loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md) | [Install & env](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md) | [Related work](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md) | [Developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md) |
| [Scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md) | [Persistence, state, recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md) | [Benchmarks + metrics](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) | [Architecture](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/architecture.md) |
| [Structured output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/structured-output.md) | [Observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md) | [Verdict resolution](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md) | [Conventions](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/conventions.md) |
| [Nodes and pipelines](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/node-standard.md) | [Backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md) | [Candidate elimination](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md) | [Self-healing internals](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md) |

[Roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md) · [docs index](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md) · [stable API](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/stable-api.md)

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```
