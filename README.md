<h1 align="center">An LLM-driven prompt optimizer for the whole pipeline</h1>

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/potter.jpg" alt="The Potter" width="73">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/wordmark.png" alt="PromptPotter" width="364">
</p>

<p align="center">
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/v/promptpotter" alt="PyPI"></a>
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/pyversions/promptpotter" alt="Python"></a>
  <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT--RG-blue" alt="License: MIT-RG"></a>
</p>

<p align="center">
  Give it examples where you know the right answer. It evolves your prompt <i>and</i> your pipeline's settings,<br>
  and hands back the winner with every measurement behind it.
</p>

<p align="center">
  <a href="https://app.promptpotter.com"><b>Try it in the browser</b></a>
  &nbsp;·&nbsp; <a href="#run-it-where-your-data-lives">Install</a>
  &nbsp;·&nbsp; <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md">Docs</a>
  &nbsp;·&nbsp; <a href="https://www.youtube.com/watch?v=DLhb26ppX_s">Watch a live run</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/what-it-does-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/what-it-does-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/what-it-does-light.png" alt="Your examples go into PromptPotter; your AI goes in hand-patched and comes out tuned" width="560">
  </picture>
</p>

## Fits your stack if it answers two calls

```
┌──────────────────────┐                       ┌──────────────────────┐
│  Your Backend        │  GET  /pipeline   ──► │  PromptPotter        │
│  (any pipeline)      │                       │  Optimizer           │
│                      │  POST /matches    ◄── │                      │
│  runs the task       │   {prompt, params}    │  generates candidates│
│                      │                       │  scores + critiques  │
│                      │  → predictions    ──► │  iterates            │
└──────────────────────┘                       └──────────────────────┘
```

LangGraph, CrewAI, n8n, Dify or plain FastAPI: put it behind these two endpoints and it can be tuned ([how](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md)). In the browser app there is nothing to set up. You get back one file, `export.json`, with the winning settings.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/universal-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/universal-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/universal-light.png" alt="Not just a prompt optimizer, a universal one: the prompt sits inside the model, the model inside the settings, the settings inside any program (pipelines, agents and skills, loops); freeze any part" width="480">
  </picture>
</p>

<p align="center"><sub>Whatever your service exposes, it can tune. Whatever you lock, it leaves alone.</sub></p>

## Why PromptPotter

- 💸 **Stops paying for losers.** A version that cannot win is cut after a handful of examples.
- 🎯 **Won't let you fool yourself.** Each round says whether its gain is noise; one command grades the winner on held-out examples.
- 🔍 **Every number opens down to the call.** The exact input, the answer, the expected answer, paid or replayed.
- 🌳 **Searches a tree, not a line.** When progress stalls it rewrites its own search plan or backs up to an earlier version.
- ⚖️ **Runs the others too.** GEPA, CAPO and LEVI run inside it, under one grader and one budget.
- 🤖 **Runs from Claude Code.** `/potter-run` launches the run, watches it and explains every round.

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates.gif" alt="The Candidates card across 11 real campaigns: candidate fitness per round, with the lineage tree under each chart" width="400">
</p>

<p align="center"><sub>11 real campaigns: each dot is a scored version, the crown marks the one that stands,<br>the tree shows who was bred from whom. In-run scores, not held-out results.</sub></p>

## Run it where your data lives

| | | |
|---|---|---|
| ☁️ **We run it** | Online, zero setup. Free to try, or bring your own key. | **[app.promptpotter.com](https://app.promptpotter.com)** |
| 💻 **You run it** | Local, on your own keys. We never see your data. | `pip install promptpotter` |
| 🤖 **Your AI runs it** | Claude Code launches and supervises the run; the browser app stays live. | `/potter-run` |
| 👥 **Your team runs it** | Self-hosted, multi-user. We never see your data. | [`deploy-linux/`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/deploy-linux/README.md) |

**From Python** you need Python 3.13+ and an [OpenRouter key](https://openrouter.ai/keys) in `OPENROUTER_API_KEY` for the optimizer's own calls ([other providers](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md)).

**Against your own pipeline**, from the terminal. Start the backend from the diagram above before `new`; without one, use the browser.

```bash
pip install "promptpotter[all]"
python -m promptpotter new tickets.csv --set raw_task_description="classify support tickets by urgency"
python -m promptpotter resume        # Ctrl+C pauses; resume picks up where it stopped
```

The shipped example datasets run against [TermNorm](https://github.com/runfish5/TermNorm-excel) on `:8000`; in [Claude Code](https://claude.com/claude-code), `/potter-run` starts it for you. To watch locally: `python -X utf8 -m uvicorn promptpotter.main:app --port 8001`, then open `http://localhost:8001/`.

<details>
<summary><b>In DSPy</b>: a drop-in optimizer, no backend needed</summary>

Use it where you would reach for MIPROv2 or GEPA. It runs inside your process, with no extra service:

```python
# pip install "promptpotter[dspy]"
from promptpotter.presentation.teleprompter import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset)
```

</details>

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tour-dark.gif" alt="A walk through the app: the chat thread, the run card, the dashboard, the sample leaderboard, one opened cell" width="100%">
</p>

<p align="center"><sub>The app: the chat where a run starts, the round-by-round scoreboard, and one example opened down to the call.</sub></p>

## A round, in four steps

1. **Generate.** An LLM reads the last round's misses and writes a few new versions of the prompt and its settings.
2. **Score.** Each version runs on your examples. A version that falls behind is cut as soon as it cannot win.
3. **Critique.** It reads each miss to steer what the next round tries; the round's winner becomes the next starting point.
4. **Escalate.** When progress stalls, it rewrites how it searches, or backs up the lineage tree and tries another branch. It stops on your goal, your budget or a plateau.

<details>
<summary><b>One real round, replayed from its log</b>: the third version is cut after 7 of 34 examples</summary>

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-replay.svg" alt="Replay of one real round: three candidates measured, the third eliminated after 7 of 34 samples" width="880">
</p>

<p align="center"><sub>Three versions race on 34 examples; the third is cut after 7, so its other 27 calls are never paid for (<a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md">how the cut is decided</a>).</sub></p>

</details>

### Under the hood: the statistics and the search

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: generate, score and critique repeat until a stopping rule fires; L2 and L3 escalate lazily on a stall" width="760">
  </picture>
</p>

- **The cut** is Posterior-of-Being-Best: a version stops when its chance of being the best falls under a bar ([candidate elimination](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md)).
- **The score** is a Rasch ability (θ), so versions measured on different examples still compare on one scale; each round says when its lift is noise ([what a winner's number may claim](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md)).
- **The search** has three layers: L1 proposes versions, L2 rewrites how L1 searches when L1 stalls, L3 rewrites the plan when L2 stalls. When a branch is spent, a UCB rule picks the ancestor to re-expand: AlphaZero-shaped tree search over the lineage, with deterministic scoring in place of rollouts ([the loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md), [the mapping](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/landscape.md#comparison-to-mcts)).
- **Self-optimization (L4):** the optimizer is itself a pipeline of nodes, so it can be pointed at its own optimizer prompts ([L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/l4.md)). Research, run from a checkout.
- **A campaign is a folder on disk.** Each round is a JSON file the app renders as a scoreboard; pause, resume, rewind to any round, or fork a sibling and compare ([persistence and recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md)).

## Same family as GEPA. A wider reach.

PromptPotter belongs to the same family as [GEPA](https://arxiv.org/abs/2507.19457), [MIPROv2](https://arxiv.org/abs/2406.11695) and [CAPO](https://arxiv.org/abs/2504.16005): an LLM proposes versions, a scorer ranks them, the winners breed.

| | GEPA | PromptPotter |
|---|:--:|:--:|
| Tunes model, temperature and pipeline thresholds together with the prompt | partly | ✓ |
| Stops scoring a losing version early | partly | ✓ |
| Prices every measurement, cache hits included | ✗ | ✓ |
| Open and inspectable: code on GitHub, a browser app, a run you pause, resume, rewind or fork | partly | ✓ |

GEPA is ahead on reach: it ships inside DSPy's ecosystem. PromptPotter is also a bench: GEPA, CAPO and LEVI run inside it as optimizers under one grader, one budget meter and one stopping rule. The capability comparison is not a benchmark result ([the landscape](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/landscape.md#against-gepa-and-alphaevolve), [what we measure on](https://promptpotter.com/benchmarks)).

<details>
<summary><b>Works with what you already run</b></summary>

| You built it with | How PromptPotter meets it |
|---|---|
| **LangGraph, CrewAI, the OpenAI Agents SDK, Bedrock Agents, n8n, Dify, plain FastAPI** | Two HTTP endpoints on your backend; the knobs your pipeline declares become what is tuned ([backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md)) |
| **DSPy** | `PromptPotterOpt`, in place of MIPROv2 or GEPA ([DSPy guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md)) |
| **An agent in a sandbox** | Each version runs as a [Harbor](https://github.com/laude-institute/harbor) trial, graded by the task's own checker |
| **OpenRouter, OpenAI, Anthropic, Groq** | Any of them, for your pipeline and for the optimizer |

**What you get back:** `export.json`, the winning prompt and settings by field name, with the evidence behind them. We never load, host, route or hot-swap anything in your production path.

</details>

<details>
<summary><b>What is stable, what is beta</b></summary>

- 🟢 **Stable:** the campaign loop from the CLI, the local app and `/potter-run`; the HTTP connector and its `export.json`; pause, resume, rewind (`resume --from N`) and fork; the head-to-head bench (GEPA, CAPO and the rest under one grader and one budget); the zero-spend offline run.
- 🟡 **Beta:** the hosted app and the team box (accounts, delegates, API keys, the wallet); the MCP host tools; embedded Python, whose surface still changes between releases; the n8n, Harbor and DSPy connectors.
- 🧪 **Research:** L4 self-optimization (`promptpotter-self`), run from a checkout.

</details>

<details>
<summary><b>Limitations</b></summary>

- **Parameters only.** Prompts, thresholds, model settings; never model weights or architectures.
- **It needs a known answer or a checker.** Each example's expected output, or the backend's own grader (Harbor).
- **In-run scores are not held-out scores.** The number to quote is the winner's score on held-out examples no optimizer saw: `python -m promptpotter held-out`.

</details>

<details>
<summary><b>Contributing</b></summary>

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
python scripts/gate.py          # every check CI runs; the first run builds .venv from uv.lock
```

Five entry points share one engine: the webapp, `/potter-run`, the CLI, the REST API, and embedded Python. Every optimizer runs end to end at zero spend ([offline run](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/offline-run.md)), so a change can prove it moves no decision the loop makes. Start at [the developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md#developer--seams-no-single-file-shows), then [`CONTRIBUTING.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/CONTRIBUTING.md).

</details>

**Docs:** [the manual](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md) · [docs index](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md) · [architecture](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/architecture.md) · [roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md)

If PromptPotter saves you an afternoon of prompt fiddling, a ⭐ helps the next person find it.

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```
